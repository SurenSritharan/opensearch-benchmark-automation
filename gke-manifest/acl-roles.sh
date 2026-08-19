#!/usr/bin/env bash
# Creates 11 user-specific DLS roles and their role mappings.
#
# Design: Each user gets a dedicated role (one-to-one) so OpenSearch does not
# union backend_roles across roles. This matches production where each user or
# service account has a specific role with its own DLS filter.
#
# security_classification is NOT used anywhere. Access control is enforced
# purely through access_roles, access_groups, and access_users.
#
# DLS filter patterns:
#
#   Standard (dls-role-only … dls-classified):
#     A single bool.should with minimum_should_match:1 across:
#       terms: {access_roles:  [${user.roles}]}
#       terms: {access_groups: [${user.roles}]}
#       terms: {access_users:  ["${user.name}"]}
#     A document is visible if ANY of the three checks matches.
#     ${user.roles} expands to the user's backend_roles array at query time.
#     ${user.name}  expands to the user's login name at query time.
#
#   Ultrastrict (dls-ultrastrict … dls-ultrastrict-fixed):
#     A hard bool.filter (AND) requiring ALL three checks to match:
#       terms: {access_roles:  [${user.roles}]}   (backend_roles = ["need-to-know"])
#       terms: {access_groups: [${user.roles}]}   (backend_roles = ["need-to-know"])
#       terms: {access_users:  ["${user.name}"]}  (login name)
#     The ingest pipeline writes need-to-know in access_roles and access_groups,
#     and the specific username in access_users, on every ultrastrict document.
#     A document is visible only if the user's name appears in access_users
#     AND "need-to-know" appears in both access_roles and access_groups.
#
# IMPORTANT — shell quoting rule:
#   ${user.roles} and ${user.name} are OpenSearch Security template variables
#   expanded at QUERY TIME by the Security plugin — NOT bash variables.
#   All role bodies that contain a "dls" field use single-quoted strings so
#   that bash never expands them. CLUSTER_PERMS and INDEX_PATTERNS are
#   defined below and their literal values are pasted inline into each body.
#   Do NOT convert DLS role bodies to double-quoted strings — bash would
#   silently expand ${user.roles} and ${user.name} to empty, corrupting the
#   DLS filter before it reaches the Security API.
#
# Role → User mapping (one-to-one):
#   dls-baseline           → user-unrestricted       (no DLS filter — raw baseline)
#   dls-role-only          → user-role-only          (backend_roles: [role-svc])
#   dls-group-only         → user-group-only         (backend_roles: [grp-finance])
#   dls-name-only          → user-name-only          (backend_roles: [] — identity-only)
#   dls-role-group         → user-role-group         (backend_roles: [role-svc, grp-finance])
#   dls-group-name         → user-group-name         (backend_roles: [grp-finance])
#   dls-classified         → user-classified         (backend_roles: [grp-restricted, need-to-know])
#   dls-ultrastrict        → user-ultrastrict        (backend_roles: [need-to-know] — buckets 95–99, ~5%)
#   dls-ultrastrict-narrow → user-ultrastrict-narrow (backend_roles: [need-to-know] — bucket 94, ~1%)
#   dls-ultrastrict-micro  → user-ultrastrict-micro  (backend_roles: [need-to-know] — id%1000<6, ~0.6%)
#   dls-ultrastrict-fixed  → user-ultrastrict-fixed  (backend_roles: [need-to-know] — id<5000, fixed 5k)
#
# Expected visible doc counts at 1M (each standard bucket ~10k docs):
#   user-unrestricted       → ~1M     (no DLS)
#   user-role-group         → ~780k   (buckets 0–87; role-svc OR grp-finance match)
#   user-group-only         → ~430k   (buckets 35–87; grp-finance match)
#   user-group-name         → ~430k   (buckets 35–87; grp-finance OR username match)
#   user-role-only          → ~400k   (buckets 0–34, 78–82; role-svc match)
#   user-name-only          → ~100k   (buckets 68–77; username match only)
#   user-classified         → ~60k    (buckets 88–93; grp-restricted match)
#   user-ultrastrict        → ~50k    (buckets 95–99; triple AND: name+need-to-know+need-to-know)
#   user-ultrastrict-narrow → ~10k    (bucket 94; triple AND)
#   user-ultrastrict-micro  → ~6k     (Tier B: id%1000<6 outside first 5k; triple AND)
#   user-ultrastrict-fixed  → 5,000   (Tier A: id<5000; triple AND — fixed regardless of corpus)

set -euo pipefail

OS_HOST="$1"

# These two values are expanded by bash (they contain no OpenSearch template variables).
# They are pasted as literal JSON into each role body below.
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
# No dls clause — double-quoted body is safe here (no OpenSearch template variables).
put_role dls-baseline "{
  \"cluster_permissions\": ${CLUSTER_PERMS},
  \"index_permissions\": [{
    \"index_patterns\": ${INDEX_PATTERNS},
    \"allowed_actions\": [\"read\",\"search\",\"indices:monitor/stats\",\"indices:monitor/settings/get\"]
  }]
}"
put_mapping dls-baseline '{"backend_roles":["grp-broad"],"users":["user-unrestricted"]}'
echo "  dls-baseline -> user-unrestricted"

# ── dls-role-only: access via access_roles match only ─────────────────────────
# backend_roles = ["role-svc"]
# ${user.roles} expands to ["role-svc"] → matches docs with access_roles:["role-svc"]
# Visible: buckets 0–34 (35 buckets) + 78–82 (5 buckets) = ~400k docs
# NOTE: single-quoted body — ${user.roles} and ${user.name} must reach the server
# unexpanded as OpenSearch Security template variables (bash must not touch them).
put_role dls-role-only '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}],\"minimum_should_match\":1}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-role-only '{"backend_roles":["role-svc"],"users":["user-role-only"]}'
echo "  dls-role-only -> user-role-only"

# ── dls-group-only: access via access_groups match only ───────────────────────
# backend_roles = ["grp-finance"]
# ${user.roles} expands to ["grp-finance"] → matches docs with access_groups:["grp-finance"]
# Visible: buckets 35–87 (53 buckets) = ~430k docs
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-group-only '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}],\"minimum_should_match\":1}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-group-only '{"backend_roles":["grp-finance"],"users":["user-group-only"]}'
echo "  dls-group-only -> user-group-only"

# ── dls-name-only: access via access_users (username) match only ──────────────
# backend_roles = [] (empty)
# ${user.roles} expands to [] → terms checks on access_roles/access_groups match nothing
# Only ${user.name} = "user-name-only" fires → matches docs with access_users:["user-name-only"]
# Visible: buckets 68–77 (10 buckets) = ~100k docs
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-name-only '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}],\"minimum_should_match\":1}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-name-only '{"backend_roles":[],"users":["user-name-only"]}'
echo "  dls-name-only -> user-name-only"

# ── dls-role-group: access via role OR group match ────────────────────────────
# backend_roles = ["role-svc", "grp-finance"]
# ${user.roles} expands to ["role-svc","grp-finance"] — both values checked against
# access_roles and access_groups simultaneously.
# Visible: buckets 0–87 (88 buckets, minus 68–77 username-only) = ~780k docs
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-role-group '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}],\"minimum_should_match\":1}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-role-group '{"backend_roles":["role-svc","grp-finance"],"users":["user-role-group"]}'
echo "  dls-role-group -> user-role-group"

# ── dls-group-name: access via group OR username match ────────────────────────
# backend_roles = ["grp-finance"]
# In addition to group match, ${user.name} = "user-group-name" fires on buckets 83–87
# where the ingest pipeline writes access_users:[..., "user-group-name", ...]
# Visible: buckets 35–87 = ~430k docs (same count as group-only but two OR branches fire)
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-group-name '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}],\"minimum_should_match\":1}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-group-name '{"backend_roles":["grp-finance"],"users":["user-group-name"]}'
echo "  dls-group-name -> user-group-name"

# ── dls-classified: access via grp-restricted group match ─────────────────────
# backend_roles = ["grp-restricted", "need-to-know"]
# "grp-restricted" in ${user.roles} matches docs with access_groups:["grp-restricted"]
# "need-to-know" in ${user.roles} also checked but ingest writes it only on ultrastrict
# docs that require the AND filter — so it does not expand access to ultrastrict buckets.
# Visible: buckets 88–93 (6 buckets) = ~60k docs
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-classified '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"should\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}],\"minimum_should_match\":1}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-classified '{"backend_roles":["grp-restricted","need-to-know"],"users":["user-classified"]}'
echo "  dls-classified -> user-classified"

# ── dls-ultrastrict: hard AND — username + need-to-know in all three fields ───
# backend_roles = ["need-to-know"]
# Doc must satisfy ALL of:
#   access_roles  contains "need-to-know"  (${user.roles} = ["need-to-know"])
#   access_groups contains "need-to-know"  (${user.roles} = ["need-to-know"])
#   access_users  contains "user-ultrastrict" (${user.name})
# Ingest writes all three on buckets 95–99. ~50k docs at 1M.
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-ultrastrict '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-ultrastrict '{"backend_roles":["need-to-know"],"users":["user-ultrastrict"]}'
echo "  dls-ultrastrict -> user-ultrastrict"

# ── dls-ultrastrict-narrow: hard AND — bucket 94 only (~1%) ───────────────────
# backend_roles = ["need-to-know"]
# Same triple AND filter as dls-ultrastrict but keyed to "user-ultrastrict-narrow".
# Ingest writes all three on bucket 94 only. ~10k docs at 1M.
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-ultrastrict-narrow '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-ultrastrict-narrow '{"backend_roles":["need-to-know"],"users":["user-ultrastrict-narrow"]}'
echo "  dls-ultrastrict-narrow -> user-ultrastrict-narrow"

# ── dls-ultrastrict-micro: hard AND — Tier B (~0.6%) ─────────────────────────
# backend_roles = ["need-to-know"]
# Same triple AND filter keyed to "user-ultrastrict-micro".
# Ingest writes all three on docs where id>=5000 AND id%1000<6. ~6k docs at 1M.
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-ultrastrict-micro '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-ultrastrict-micro '{"backend_roles":["need-to-know"],"users":["user-ultrastrict-micro"]}'
echo "  dls-ultrastrict-micro -> user-ultrastrict-micro"

# ── dls-ultrastrict-fixed: hard AND — Tier A (fixed 5k) ───────────────────────
# backend_roles = ["need-to-know"]
# Same triple AND filter keyed to "user-ultrastrict-fixed".
# Ingest writes all three on docs where id<5000. Exactly 5,000 docs regardless of corpus size.
# NOTE: single-quoted body — protects ${user.roles} and ${user.name} from bash.
put_role dls-ultrastrict-fixed '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\":{\"filter\":[{\"terms\":{\"access_roles\":[${user.roles}]}},{\"terms\":{\"access_groups\":[${user.roles}]}},{\"terms\":{\"access_users\":[\"${user.name}\"]}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats","indices:monitor/settings/get"]
  }]
}'
put_mapping dls-ultrastrict-fixed '{"backend_roles":["need-to-know"],"users":["user-ultrastrict-fixed"]}'
echo "  dls-ultrastrict-fixed -> user-ultrastrict-fixed"

echo "[acl-roles] Done."
