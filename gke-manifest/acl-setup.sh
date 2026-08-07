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
MODE="${2:-setup}"   # setup (default) | verify
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
echo "ACL Setup — namespace: ${NS}  (engine: ${ENGINE})  mode: ${MODE}"
echo "Worker pod: ${WORKER_NS}/${WORKER_POD}"
echo "OpenSearch host: ${OS_HOST}"
echo "============================================================"
echo ""

# In verify mode skip all setup steps — go straight to post-ingest checks.
if [ "$MODE" = "verify" ]; then
    echo "(verify mode — skipping setup steps 1–4)"
    echo ""
else

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
    "cluster_permissions": ["cluster_composite_ops_ro", "cluster:monitor/health", "cluster:monitor/main", "cluster:monitor/state", "cluster:monitor/nodes/stats", "cluster:monitor/nodes/info"],
    "index_permissions": [
      {
        "index_patterns": ["cohere-wiki-en-768-*", "cohere-msmarco-1024-*"],
        "dls": "{\"bool\": {\"should\": [{\"terms\": {\"access_groups\": [${user.roles}]}}, {\"terms\": {\"access_roles\": [${user.roles}]}}, {\"term\": {\"access_users\": \"${user.name}\"}}], \"minimum_should_match\": 1}}",
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

# ── Config verification (setup mode only) ─────────────────────────────────────
# Confirms the four resources were created correctly.  Does NOT check doc counts
# — the index does not exist yet at this point in the pipeline.
echo "============================================================"
echo "Verifying setup..."
echo "============================================================"

echo ""
echo "  [1] Pipeline acl_guard:"
os_api "https://${OS_HOST}/_ingest/pipeline/acl_guard" 2>/dev/null \
  | jq '.' 2>/dev/null || echo "    (pipeline not found)"

echo ""
echo "  [2] Index template acl-benchmark-template:"
os_api "https://${OS_HOST}/_index_template/acl-benchmark-template" 2>/dev/null \
  | jq '.' 2>/dev/null || echo "    (template not found)"

echo ""
echo "  [3] DLS role test_full_identity:"
os_security "https://${OS_HOST}/_plugins/_security/api/roles/test_full_identity" 2>/dev/null \
  | jq '.' 2>/dev/null || echo "    (role not found)"

echo ""
echo "  [4] Role mapping test_full_identity:"
os_security "https://${OS_HOST}/_plugins/_security/api/rolesmapping/test_full_identity" 2>/dev/null \
  | jq '.' 2>/dev/null || echo "    (role mapping not found)"

echo ""
echo "  [5] User benchmark-acl-user:"
os_security "https://${OS_HOST}/_plugins/_security/api/internalusers/benchmark-acl-user" 2>/dev/null \
  | jq '.' 2>/dev/null || echo "    (user not found)"

echo ""
echo "============================================================"
echo "✅ ACL setup complete for ${NS}"
echo "   Next: run the benchmark with pipeline complete-1m-acl"
echo "============================================================"

fi  # end of setup-only block

# ── Post-ingest verification (acl-setup.sh <ns> verify) ─────────────────────
# Run this after bulk-ingest-data completes to confirm:
#   1. The index was created with default_pipeline: acl_guard
#   2. ACL fields are populated in the actual indexed documents
#   3. DLS is restrictive — ACL user sees fewer docs than admin
if [ "$MODE" != "verify" ]; then
    exit 0
fi

INDEX="cohere-wiki-en-768-1m"

echo ""
echo "============================================================"
echo "Post-ingest ACL verification — ${INDEX}"
echo "============================================================"

echo ""
echo "  [A] Index settings (default_pipeline + knn):"
os_api "https://${OS_HOST}/${INDEX}/_settings" 2>/dev/null \
  | jq --arg idx "$INDEX" \
       '.[$idx].settings.index | {default_pipeline, knn}' \
  2>/dev/null || echo "    (index not found)"

echo ""
echo "  [B] 10 random docs — ACL field presence:"
os_api -X POST "https://${OS_HOST}/${INDEX}/_search" \
  -H "Content-Type: application/json" \
  -d '{
    "size": 10,
    "_source": ["access_groups", "access_roles", "access_users"],
    "query": { "function_score": { "query": { "match_all": {} }, "random_score": {} } }
  }' 2>/dev/null \
  | jq -r '
    "    total docs in index: \(.hits.total.value)",
    "    ---",
    (.hits.hits[] |
      "    _id=\(._id)  access_groups=\(._source.access_groups // "MISSING ❌")  access_roles=\(._source.access_roles // [])  access_users=\(._source.access_users // [])")
  ' 2>/dev/null || echo "    (index empty or not found)"

echo ""
echo "  [C] Doc count — admin vs benchmark-acl-user:"
ADMIN_COUNT=$(os_api "https://${OS_HOST}/${INDEX}/_count" \
  2>/dev/null | jq -r '.count // "N/A"' 2>/dev/null || echo "N/A")
ACL_COUNT=$(kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
  curl -sk -u "benchmark-acl-user:BenchmarkACL-2024!" \
  "https://${OS_HOST}/${INDEX}/_count" \
  2>/dev/null | jq -r '.count // "N/A"' 2>/dev/null || echo "N/A")
echo "    admin count:              ${ADMIN_COUNT}"
echo "    benchmark-acl-user count: ${ACL_COUNT}"
if [ "$ADMIN_COUNT" != "N/A" ] && [ "$ACL_COUNT" != "N/A" ] && \
   [ "$ADMIN_COUNT" != "0" ]; then
  if [ "$ADMIN_COUNT" -gt "$ACL_COUNT" ] 2>/dev/null; then
    echo "    ✅ DLS IS restrictive — ACL user sees fewer docs than admin"
    echo "    Expected ~667k / 1M (buckets 0+2 visible to grp-finance-readers)"
  elif [ "$ADMIN_COUNT" -eq "$ACL_COUNT" ] 2>/dev/null; then
    echo "    ❌ DLS NOT restrictive — same count for both users"
  fi
else
  echo "    ❌ Could not compare counts (index may be empty)"
fi

echo ""
echo "============================================================"
echo "✅ Post-ingest ACL verification complete for ${NS}"
echo "============================================================"

# Made with Bob
