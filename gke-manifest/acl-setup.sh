#!/usr/bin/env bash
# acl-setup.sh — Idempotent ACL environment bootstrap for a single OpenSearch namespace.
#
# Sets up everything required to benchmark Query-Time ACL (Document Level Security):
#   1. acl_guard ingest pipeline  — normalises/validates access_roles/groups/users
#   2. Index template             — attaches acl_guard as default_pipeline + adds ACL field mappings
#   3. DLS roles                  — test_full_identity (3-path identity check)
#   4. benchmark-acl-user         — internal user mapped to the DLS role
#
# Usage:
#   gke-manifest/acl-setup.sh <namespace>
#   gke-manifest/acl-setup.sh os-jvector
#
# All calls are routed through the engine's benchmark worker pod (kubectl exec)
# because Jenkins has no direct network path to the OpenSearch cluster endpoint —
# only pods inside GKE do.  This matches the pattern used throughout the Jenkinsfile.
#
# Idempotency: every call uses PUT (create-or-replace), so re-running this script
# during a REDEPLOY_CLUSTERS run is safe — it will simply overwrite with the same config.

set -euo pipefail

NS="${1:?Usage: $0 <namespace>  (e.g. os-jvector)}"
ENGINE="${NS#os-}"   # strip "os-" prefix → jvector | faiss | lucene

WORKER_POD="opensearch-benchmark-worker-${ENGINE}-0"
WORKER_NS="benchmark-api"
OS_HOST="opensearch-cluster.${NS}.svc.cluster.local:9200"

# Two curl helpers — both run inside the worker pod via kubectl exec.
#
# os_api()       — regular REST (ingest pipeline, index template, _cat, _count …).
#                  Basic auth only; -sk skips TLS cert verification.
#
# os_security()  — OpenSearch Security REST API (/_plugins/_security/api/…).
#                  The Security plugin requires the admin TLS client certificate
#                  in addition to basic auth; without it the plugin returns:
#                  "No permission to access REST API: Role based access not enabled."
#                  Certs are mounted into the worker pod at /certs/ by the
#                  opensearch-benchmark-worker-template.yaml volumeMount.
#
# Both helpers use the same admin:admin credentials and -sk to skip CA verification.
os_api() {
    kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin \
        "$@"
}

os_security() {
    kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin \
        --cert /certs/admin.pem \
        --key  /certs/admin-key.pem \
        "$@"
}

echo "============================================================"
echo "ACL Setup — namespace: ${NS}  (engine: ${ENGINE})"
echo "Worker pod: ${WORKER_NS}/${WORKER_POD}"
echo "OpenSearch host: ${OS_HOST}"
echo "============================================================"
echo ""

# ── 0. Wait for OpenSearch to be reachable via the Service ───────────────────
# The worker pod can be Ready while OpenSearch's Service endpoint is still
# propagating (or while the cluster is finishing its startup sequence).
# curl exit-7 = connection refused.  Retry for up to 5 minutes (30 × 10 s).
echo "[0/4] Waiting for OpenSearch to be reachable at ${OS_HOST}..."
WAIT_RETRIES=30
WAIT_INTERVAL=10
for i in $(seq 1 $WAIT_RETRIES); do
    HTTP_CODE=$(kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin -o /dev/null -w "%{http_code}" \
        "https://${OS_HOST}/_cluster/health" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        echo "  ✅ OpenSearch reachable (attempt ${i})"
        break
    fi
    echo "  [${i}/${WAIT_RETRIES}] HTTP ${HTTP_CODE} — waiting ${WAIT_INTERVAL}s..."
    if [ "$i" -eq "$WAIT_RETRIES" ]; then
        echo "❌ OpenSearch did not become reachable after $((WAIT_RETRIES * WAIT_INTERVAL))s — aborting"
        exit 1
    fi
    sleep $WAIT_INTERVAL
done
echo ""

# ── 1. acl_guard Ingest Pipeline ─────────────────────────────────────────────
# Normalises access_roles / access_groups / access_users (lowercase + trim +
# strip empty strings), then injects synthetic access_groups from _id mod 3
# when all three fields are absent.  Guarantees every indexed document carries
# at least one ACL value — required for meaningful DLS benchmarking.
echo "[1/4] Installing acl_guard ingest pipeline..."
os_api -X PUT "https://${OS_HOST}/_ingest/pipeline/acl_guard" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Normalise existing ACL fields; inject synthetic access_groups from _id mod 3 when all ACL fields are absent",
    "processors": [
      { "lowercase": { "field": "access_roles",  "ignore_missing": true } },
      { "trim":      { "field": "access_roles",  "ignore_missing": true } },
      { "lowercase": { "field": "access_groups", "ignore_missing": true } },
      { "trim":      { "field": "access_groups", "ignore_missing": true } },
      { "lowercase": { "field": "access_users",  "ignore_missing": true } },
      { "trim":      { "field": "access_users",  "ignore_missing": true } },
      {
        "script": {
          "description": "1) Strip empty strings from ACL arrays. 2) If all three fields are still absent/empty, inject access_groups deterministically from _id mod 3. The three bucket values match the backend_roles defined in the role mapping: grp-finance-readers (bucket 0), grp-all-employees (bucket 1), both (bucket 2 — visible to either role). DLS expands ${user.roles} to these same strings at query time.",
          "lang": "painless",
          "source": "for (def f : [\"access_roles\", \"access_groups\", \"access_users\"]) { if (ctx.containsKey(f) && ctx[f] != null) { ctx[f].removeIf(v -> v == null || v == \"\"); } } boolean missing = (ctx.access_roles == null || ctx.access_roles.isEmpty()) && (ctx.access_groups == null || ctx.access_groups.isEmpty()) && (ctx.access_users == null || ctx.access_users.isEmpty()); if (missing) { long id = Long.parseLong(ctx._id); int bucket = (int)(id % 3); if (bucket == 0) { ctx.access_groups = [\"grp-finance-readers\"]; } else if (bucket == 1) { ctx.access_groups = [\"grp-all-employees\"]; } else { ctx.access_groups = [\"grp-finance-readers\", \"grp-all-employees\"]; } }"
        }
      }
    ]
  }'
echo "  ✅ acl_guard pipeline installed"
echo ""

# ── 2. Index Template — default_pipeline + ACL field mappings ─────────────────
# Applied to all benchmark index names (cohere-wiki-en-768-* and cohere-msmarco-1024-*).
# The template adds:
#   • default_pipeline: acl_guard   → every bulk-ingest call passes through the pipeline
#   • ACL keyword fields             → required for DLS term filters to work
# Priority 200 ensures this template takes precedence over any lower-priority defaults.
echo "[2/4] Installing index template with default_pipeline and ACL field mappings..."
os_api -X PUT "https://${OS_HOST}/_index_template/acl-benchmark-template" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["cohere-wiki-en-768-*", "cohere-msmarco-1024-*"],
    "priority": 200,
    "template": {
      "settings": {
        "index": {
          "default_pipeline": "acl_guard"
        }
      },
      "mappings": {
        "properties": {
          "access_groups":           { "type": "keyword" },
          "access_roles":            { "type": "keyword" },
          "access_users":            { "type": "keyword" },
          "security_classification": { "type": "keyword" }
        }
      }
    }
  }'
echo "  ✅ Index template installed"
echo ""

# ── 3. DLS Role — test_full_identity ─────────────────────────────────────────
# Three-path identity check matching the M2 design:
#   access_groups ∋ any of user's roles  OR
#   access_roles  ∋ any of user's roles  OR
#   access_users  == user's own name
# Role mapping assigns backend_roles so any user with those LDAP/backend groups
# inherits the DLS restriction automatically.
echo "[3/4] Creating DLS role test_full_identity..."
os_security -X PUT "https://${OS_HOST}/_plugins/_security/api/roles/test_full_identity" \
  -H "Content-Type: application/json" \
  -d '{
    "cluster_permissions": ["cluster_composite_ops_ro"],
    "index_permissions": [
      {
        "index_patterns": ["cohere-wiki-en-768-*", "cohere-msmarco-1024-*"],
        "dls": "{\"bool\": {\"should\": [{\"terms\": {\"access_groups\": ${user.roles}}}, {\"terms\": {\"access_roles\": ${user.roles}}}, {\"term\": {\"access_users\": \"${user.name}\"}}], \"minimum_should_match\": 1}}",
        "allowed_actions": ["read", "search"]
      }
    ]
  }'
echo "  ✅ DLS role test_full_identity created"

echo "    Creating role mapping (backend_roles: grp-finance-readers, grp-all-employees, role-employee)..."
os_security -X PUT "https://${OS_HOST}/_plugins/_security/api/rolesmapping/test_full_identity" \
  -H "Content-Type: application/json" \
  -d '{
    "backend_roles": ["grp-finance-readers", "grp-all-employees", "role-employee"],
    "users": ["benchmark-acl-user"]
  }'
echo "  ✅ Role mapping created"
echo ""

# ── 4. benchmark-acl-user ─────────────────────────────────────────────────────
# Internal OpenSearch user used by the ACL benchmark pipeline.
# Password must match the "password" param in complete-1m-acl.json.
# backend_roles matches the role mapping above so DLS is applied at search time.
echo "[4/4] Creating internal user benchmark-acl-user..."
os_security -X PUT "https://${OS_HOST}/_plugins/_security/api/internalusers/benchmark-acl-user" \
  -H "Content-Type: application/json" \
  -d '{
    "password": "BenchmarkACL-2024!",
    "backend_roles": ["grp-finance-readers"],
    "attributes": {}
  }'
echo "  ✅ User benchmark-acl-user created"
echo ""

# ── Verification ──────────────────────────────────────────────────────────────
# Uses jq (guaranteed present on all worker pods — used throughout the project).
echo "============================================================"
echo "Verifying setup..."
echo "============================================================"

echo ""
echo "  [1] Pipeline acl_guard:"
os_api "https://${OS_HOST}/_ingest/pipeline/acl_guard?filter_path=*.description" \
  | jq -r 'to_entries[0] | "    \(.key) — \(.value.description)"'

echo ""
echo "  [2] Index template acl-benchmark-template (default_pipeline + ACL mappings):"
os_api "https://${OS_HOST}/_index_template/acl-benchmark-template?filter_path=index_templates.*.index_template.index_patterns,index_templates.*.index_template.template.settings.index.default_pipeline,index_templates.*.index_template.template.mappings.properties" \
  | jq -r '
    .index_templates[0].index_template as $t |
    "    patterns: \($t.index_patterns)  default_pipeline: \($t.template.settings.index.default_pipeline)",
    "    acl fields in template mappings: \($t.template.mappings.properties | keys | map(select(startswith("access_"))))"'

echo ""
echo "  [3] DLS role test_full_identity:"
os_security "https://${OS_HOST}/_plugins/_security/api/roles/test_full_identity?filter_path=test_full_identity.index_permissions" \
  | jq -r '.test_full_identity.index_permissions[0] | "    index_patterns: \(.index_patterns)  dls: \(if .dls then "[set — \(.dls | .[0:80])…]" else "MISSING ❌" end)  actions: \(.allowed_actions)"'

echo ""
echo "  [4] Role mapping test_full_identity:"
os_security "https://${OS_HOST}/_plugins/_security/api/rolesmapping/test_full_identity" \
  | jq -r '.test_full_identity | "    backend_roles: \(.backend_roles)  users: \(.users)"'

echo ""
echo "  [5] User benchmark-acl-user:"
os_security "https://${OS_HOST}/_plugins/_security/api/internalusers/benchmark-acl-user?filter_path=benchmark-acl-user.backend_roles,benchmark-acl-user.attributes" \
  | jq -r '."benchmark-acl-user" | "    backend_roles: \(.backend_roles)  (must contain grp-finance-readers)"'

echo ""
echo "  [6] Live index settings — default_pipeline on cohere-wiki-en-768-1m (if exists):"
os_api "https://${OS_HOST}/cohere-wiki-en-768-1m/_settings?filter_path=*.settings.index.default_pipeline,*.settings.index.knn" \
  | jq -r 'to_entries[0] | "    index: \(.key)  default_pipeline: \(.value.settings.index.default_pipeline // "NOT SET ❌")  knn: \(.value.settings.index.knn // "false")"' \
  2>/dev/null || echo "    (index not yet created — will be checked after ingest)"

echo ""
echo "  [7] ACL field presence in indexed documents (sample 3 docs — requires index to exist):"
os_api -X POST "https://${OS_HOST}/cohere-wiki-en-768-1m/_search" \
  -H "Content-Type: application/json" \
  -d '{"size":3,"_source":["access_groups","access_roles","access_users","_id"],"query":{"match_all":{}}}' \
  2>/dev/null | jq -r '
    if .hits.total.value > 0 then
      "    total docs visible: \(.hits.total.value)",
      (.hits.hits[] | "    _id=\(._id)  access_groups=\(._source.access_groups // "MISSING ❌")  access_roles=\(._source.access_roles // "[]")  access_users=\(._source.access_users // "[]")")
    else
      "    (index empty or not yet created)"
    end' \
  2>/dev/null || echo "    (index not yet created — will be checked after ingest)"

echo ""
echo "  [8] DLS verification — docs visible as benchmark-acl-user vs admin:"
ADMIN_COUNT=$(os_api "https://${OS_HOST}/cohere-wiki-en-768-1m/_count" \
  2>/dev/null | jq -r '.count // "N/A"')
ACL_COUNT=$(kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
  curl -sk -u "benchmark-acl-user:BenchmarkACL-2024!" \
  "https://${OS_HOST}/cohere-wiki-en-768-1m/_count" \
  2>/dev/null | jq -r '.count // "N/A"')
echo "    admin count:             ${ADMIN_COUNT}"
echo "    benchmark-acl-user count: ${ACL_COUNT}"
if [ "$ADMIN_COUNT" != "N/A" ] && [ "$ACL_COUNT" != "N/A" ]; then
  if [ "$ADMIN_COUNT" -gt "$ACL_COUNT" ] 2>/dev/null; then
    echo "    ✅ DLS IS restrictive — ACL user sees fewer docs than admin"
  elif [ "$ADMIN_COUNT" -eq "$ACL_COUNT" ] 2>/dev/null; then
    echo "    ❌ DLS NOT restrictive — ACL user sees same count as admin (check role mapping and DLS query)"
  fi
else
  echo "    (index not yet created — run this script again after ingest to verify DLS)"
fi

echo ""
echo "============================================================"
echo "✅ ACL setup complete for ${NS}"
echo "   Next: run the benchmark with pipeline complete-1m-acl"
echo "============================================================"

# Made with Bob
