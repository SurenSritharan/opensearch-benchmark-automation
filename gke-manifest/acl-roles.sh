#!/usr/bin/env bash
# Creates 11 user-specific DLS roles and their role mappings.
#
# Design: Each user gets a dedicated role (not shared) so OpenSearch does not
# create a union of backend_roles. This matches production where each user/service
# account would have a specific role with their own DLS filter.
#
# Each role defines a DLS filter that expands ${user.roles} and ${user.name}
# at query time to the user's specific backend_roles and username.
#
# Role -> User mapping (one-to-one):
#   dls-baseline              -> user-unrestricted       (no DLS filter — raw baseline)
#   dls-role-only             -> user-role-only          (backend_roles: [role-svc])
#   dls-group-only            -> user-group-only         (backend_roles: [grp-finance])
#   dls-name-only             -> user-name-only          (backend_roles: [] — identity-only)
#   dls-role-group            -> user-role-group         (backend_roles: [role-svc, grp-finance])
#   dls-group-name            -> user-group-name         (backend_roles: [grp-finance])
#   dls-classified            -> user-classified         (backend_roles: [grp-restricted, restricted])
#   dls-ultrastrict           -> user-ultrastrict        (backend_roles: [restricted] — bucket 99 only, ~1%)
#   dls-ultrastrict-narrow      -> user-ultrastrict-narrow   (backend_roles: [restricted] — bucket 94 only, ~1%)
#   dls-ultrastrict-micro     -> user-ultrastrict-micro  (backend_roles: [restricted] — id%1000<6, ~0.6%)
#   dls-ultrastrict-fixed        -> user-ultrastrict-fixed     (backend_roles: [restricted] — id<5000, fixed 5k docs)
#
# Expected visible doc counts at 1M — least → most restrictive:
#   user-unrestricted        -> ~1M    (no DLS)
#   user-role-group          -> ~640k  (buckets 0–26, 35–64, 78–87; classified blocked)
#   user-group-only          -> ~400k  (buckets 35–64, 78–87; internal blocked)
#   user-group-name          -> ~400k  (buckets 35–64, 78–87)
#   user-role-only           -> ~320k  (buckets 0–26, 78–82; classified blocked)
#   user-name-only           -> ~100k  (buckets 68–77)
#   user-classified          -> ~60k   (buckets 88–93)
#   user-ultrastrict         -> ~10k   (bucket 99 — hard restricted, no absent fallback)
#   user-ultrastrict-narrow    -> ~10k   (bucket 94 — hard restricted, ~1%)
#   user-ultrastrict-micro   -> ~6k    (id%1000<6 outside first 5k — hard restricted, ~0.6%)
#   user-ultrastrict-fixed      -> 5,000  (id<5000 — hard restricted, fixed count)
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

# ── dls-role-only: user-role-only (backend_roles: [role-svc]) ─────────────────
put_role dls-role-only '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"term\":{\"access_users\":\"${user.name}\"}}],\"minimum_should_match\":1}},{\"bool\":{\"should\":[{\"terms\":{\"security_classification\":[${user.roles}]}},{\"bool\":{\"must_not\":{\"exists\":{\"field\":\"security_classification\"}}}}],\"minimum_should_match\":1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-role-only '{"backend_roles":["role-svc"],"users":["user-role-only"]}'
echo "  dls-role-only -> user-role-only"

# ── dls-group-only: user-group-only (backend_roles: [grp-finance]) ────────────
put_role dls-group-only '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"term\":{\"access_users\":\"${user.name}\"}}],\"minimum_should_match\":1}},{\"bool\":{\"should\":[{\"terms\":{\"security_classification\":[${user.roles}]}},{\"bool\":{\"must_not\":{\"exists\":{\"field\":\"security_classification\"}}}}],\"minimum_should_match\":1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-group-only '{"backend_roles":["grp-finance"],"users":["user-group-only"]}'
echo "  dls-group-only -> user-group-only"

# ── dls-name-only: user-name-only (backend_roles: [] — identity-only) ─────────
put_role dls-name-only '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"term\":{\"access_users\":\"${user.name}\"}}],\"minimum_should_match\":1}},{\"bool\":{\"should\":[{\"terms\":{\"security_classification\":[${user.roles}]}},{\"bool\":{\"must_not\":{\"exists\":{\"field\":\"security_classification\"}}}}],\"minimum_should_match\":1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-name-only '{"backend_roles":[],"users":["user-name-only"]}'
echo "  dls-name-only -> user-name-only"

# ── dls-role-group: user-role-group (backend_roles: [role-svc, grp-finance]) ──
put_role dls-role-group '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"term\":{\"access_users\":\"${user.name}\"}}],\"minimum_should_match\":1}},{\"bool\":{\"should\":[{\"terms\":{\"security_classification\":[${user.roles}]}},{\"bool\":{\"must_not\":{\"exists\":{\"field\":\"security_classification\"}}}}],\"minimum_should_match\":1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-role-group '{"backend_roles":["role-svc","grp-finance"],"users":["user-role-group"]}'
echo "  dls-role-group -> user-role-group"

# ── dls-group-name: user-group-name (backend_roles: [grp-finance]) ───────────
put_role dls-group-name '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"term\":{\"access_users\":\"${user.name}\"}}],\"minimum_should_match\":1}},{\"bool\":{\"should\":[{\"terms\":{\"security_classification\":[${user.roles}]}},{\"bool\":{\"must_not\":{\"exists\":{\"field\":\"security_classification\"}}}}],\"minimum_should_match\":1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-group-name '{"backend_roles":["grp-finance"],"users":["user-group-name"]}'
echo "  dls-group-name -> user-group-name"

# ── dls-classified: user-classified (backend_roles: [grp-restricted, restricted]) ─
put_role dls-classified '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"term\":{\"access_users\":\"${user.name}\"}}],\"minimum_should_match\":1}},{\"bool\":{\"should\":[{\"terms\":{\"security_classification\":[${user.roles}]}},{\"bool\":{\"must_not\":{\"exists\":{\"field\":\"security_classification\"}}}}],\"minimum_should_match\":1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-classified '{"backend_roles":["grp-restricted","restricted"],"users":["user-classified"]}'
echo "  dls-classified -> user-classified"

# ── dls-ultrastrict: user-ultrastrict (backend_roles: [] — hard classification) ──
# Hard filter: access_users MUST match AND security_classification MUST = restricted.
# No must_not exists fallback — user sees ONLY restricted-classified docs (~1%).
put_role dls-ultrastrict '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"term\":{\"access_users\":\"${user.name}\"}},{\"term\":{\"security_classification\":\"restricted\"}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-ultrastrict '{"backend_roles":["restricted"],"users":["user-ultrastrict"]}'
echo "  dls-ultrastrict -> user-ultrastrict"

# ── dls-ultrastrict-narrow: user-ultrastrict-narrow (bucket 94 only, ~1%) ─────────
# Hard filter: access_users MUST match AND security_classification MUST = restricted.
# Dedicated bucket 94 — exactly 1% of corpus, isolated from user-ultrastrict.
put_role dls-ultrastrict-narrow '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"term\":{\"access_users\":\"${user.name}\"}},{\"term\":{\"security_classification\":\"restricted\"}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-ultrastrict-narrow '{"backend_roles":["restricted"],"users":["user-ultrastrict-narrow"]}'
echo "  dls-ultrastrict-narrow -> user-ultrastrict-narrow"

# ── dls-ultrastrict-micro: user-ultrastrict-micro (id%1000<6, ~0.6%) ──────────
# Hard filter: access_users MUST match AND security_classification MUST = restricted.
# ~0.6% of corpus (id%1000<6 window, outside first 5000 IDs).
put_role dls-ultrastrict-micro '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"term\":{\"access_users\":\"${user.name}\"}},{\"term\":{\"security_classification\":\"restricted\"}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-ultrastrict-micro '{"backend_roles":["restricted"],"users":["user-ultrastrict-micro"]}'
echo "  dls-ultrastrict-micro -> user-ultrastrict-micro"

# ── dls-ultrastrict-fixed: user-ultrastrict-fixed (id<5000, fixed 5k docs) ──────────
# Hard filter: access_users MUST match AND security_classification MUST = restricted.
# Fixed 5,000 documents regardless of corpus size (first 5k IDs at ingest time).
put_role dls-ultrastrict-fixed '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"term\":{\"access_users\":\"${user.name}\"}},{\"term\":{\"security_classification\":\"restricted\"}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping dls-ultrastrict-fixed '{"backend_roles":["restricted"],"users":["user-ultrastrict-fixed"]}'
echo "  dls-ultrastrict-fixed -> user-ultrastrict-fixed"

echo "[acl-roles] Done."
