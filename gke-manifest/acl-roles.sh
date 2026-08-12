#!/usr/bin/env bash
# Creates 3 DLS roles and their role mappings.
#
# Design: one production-equivalent dynamic DLS filter (dls-standard) is shared
# by all non-baseline users. Each user's backend_roles simulates their JWT claims.
# ${user.roles} expands to the individual user's own backend_roles at query time —
# not the combined list in the role mapping. The role mapping backend_roles is only
# the inheritance gate (which users may assume this role).
#
# Role -> User mapping:
#   dls-baseline     -> user-unrestricted  (no DLS filter — raw baseline)
#   dls-standard     -> user-role-only     (backend_roles: [role-svc])
#                    -> user-group-only    (backend_roles: [grp-finance])
#                    -> user-name-only     (backend_roles: [], matched by username)
#                    -> user-role-group    (backend_roles: [role-svc, grp-finance])
#                    -> user-group-name    (backend_roles: [grp-finance], also matched by username)
#                    -> user-classified    (backend_roles: [grp-restricted, restricted])
#   dls-ultrastrict  -> user-ultrastrict   (no backend_roles; hard classification requirement)
#
# Expected visible doc counts at 1M (each _id%100 bucket ~10k docs) — least → most restrictive:
#   user-unrestricted  -> ~1M   (no DLS)
#   user-role-group    -> ~640k (buckets 0–26, 35–64, 78–87; external+internal blocked)
#   user-group-only    -> ~400k (buckets 35–64, 78–87; internal blocked)
#   user-group-name    -> ~400k (buckets 35–64, 78–87)
#   user-role-only     -> ~320k (buckets 0–26, 78–82; external+internal blocked)
#   user-name-only     -> ~100k (buckets 68–77)
#   user-classified    -> ~60k  (buckets 88–93)
#   user-ultrastrict   -> ~10k  (bucket 99 only — hard restricted, no absent fallback)
#
# ${user.roles} and ${user.name} in DLS strings are OpenSearch Security template
# variables — NOT shell variables. The roles using them must be PUT with single-quoted
# outer strings to prevent shell expansion.

set -euo pipefail

OS_HOST="$1"

CLUSTER_PERMS='["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"]'
INDEX_PATTERNS='["cohere-wiki-en-768-*","cohere-msmarco-1024-*"]'

os_security() {
    kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin \
        --cert /certs/admin.pem \
        --key  /certs/admin-key.pem \
        "$@"
}

put_role() {
    local name="$1"
    local body="$2"
    os_security -X PUT "https://${OS_HOST}/_plugins/_security/api/roles/${name}" \
        -H "Content-Type: application/json" \
        -d "$body"
}

put_mapping() {
    local name="$1"
    local body="$2"
    os_security -X PUT "https://${OS_HOST}/_plugins/_security/api/rolesmapping/${name}" \
        -H "Content-Type: application/json" \
        -d "$body"
}

echo "[acl-roles] Installing DLS roles..."

# ── dls-baseline: no DLS filter — raw vector search baseline ──────────────────
put_role dls-baseline "{
  \"cluster_permissions\": ${CLUSTER_PERMS},
  \"index_permissions\": [{
    \"index_patterns\": ${INDEX_PATTERNS},
    \"allowed_actions\": [\"read\",\"search\",\"indices:monitor/stats\"]
  }]
}"
put_mapping dls-baseline '{"backend_roles":["grp-broad"],"users":["user-unrestricted"]}'
echo "  dls-baseline -> user-unrestricted"

# ── dls-standard: production-equivalent dynamic filter ────────────────────────
# Shared by 6 users. Each user's own backend_roles drives which docs they see.
# Access clause:       (access_roles OR access_groups matches ${user.roles})
#                   OR (access_users = ${user.name})
# Classification:      (security_classification in ${user.roles})
#                   OR (security_classification absent)
# Single-quoted outer string prevents shell expanding ${user.roles}/${user.name}.
put_role dls-standard '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":${user.roles}}},{\"terms\":{\"access_groups\":${user.roles}}},{\"term\":{\"access_users\":\"${user.name}\"}}],\"minimum_should_match\":1}},{\"bool\":{\"should\":[{\"terms\":{\"security_classification\":${user.roles}}},{\"bool\":{\"must_not\":{\"exists\":{\"field\":\"security_classification\"}}}}],\"minimum_should_match\":1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
# backend_roles in mapping = union of all 6 users' backend_roles values
# (inheritance gate only — DLS expansion uses each user's own backend_roles at query time)
# "external" included so any future user with external clearance can inherit this role.
put_mapping dls-standard '{"backend_roles":["role-svc","grp-finance","grp-restricted","restricted","external"],"users":["user-role-only","user-group-only","user-name-only","user-role-group","user-group-name","user-classified"]}'
echo "  dls-standard -> user-role-only, user-group-only, user-name-only, user-role-group, user-group-name, user-classified"

# ── dls-ultrastrict: hard classification requirement, no absent fallback ───────
# access_users must match ${user.name} AND security_classification must = restricted.
# No must_not exists fallback — user sees ONLY restricted-classified docs (~1%).
put_role dls-ultrastrict '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"term\":{\"access_users\":\"${user.name}\"}},{\"term\":{\"security_classification\":\"restricted\"}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-ultrastrict '{"backend_roles":[],"users":["user-ultrastrict"]}'
echo "  dls-ultrastrict -> user-ultrastrict"

echo "[acl-roles] Done."
