#!/usr/bin/env bash
# Creates all 9 DLS roles and their role mappings.
#
# Each role maps to exactly one dedicated user (1-to-1 isolation).
# OpenSearch merges DLS filters across roles with OR, so a user holding
# multiple roles would have the most-permissive filter win — isolation
# prevents that.
#
# Role -> User mapping:
#   test_baseline              -> user-e1   (E1: no DLS)
#   test_tenant_only           -> user-e2   (E2: tenant_id term)
#   test_static_groups         -> user-e3   (E3: static access_groups terms)
#   test_tenant_group          -> user-m1   (M1: tenant + group)
#   test_full_identity         -> user-m2   (M2: dynamic identity)
#   test_identity_classification -> user-m3 (M3: identity + classification)
#   test_dual_validation       -> user-c1   (C1: user AND group must both match)
#   test_large_users_list      -> user-c3   (C3: 50-entry static access_users list)
#   test_multi_tenant          -> user-c5   (C5: two tenants + identity + classification)
#   test_no_classification     -> dave-contractor (C8: named user + must_not exists)
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

# E1 — no DLS
put_role test_baseline "{
  \"cluster_permissions\": ${CLUSTER_PERMS},
  \"index_permissions\": [{
    \"index_patterns\": ${INDEX_PATTERNS},
    \"allowed_actions\": [\"read\",\"search\",\"indices:monitor/stats\"]
  }]
}"
put_mapping test_baseline '{"backend_roles":["grp-finance-readers"],"users":["user-e1"]}'
echo "  E1 test_baseline -> user-e1"

# E2 — single static term on tenant_id
put_role test_tenant_only "{
  \"cluster_permissions\": ${CLUSTER_PERMS},
  \"index_permissions\": [{
    \"index_patterns\": ${INDEX_PATTERNS},
    \"dls\": \"{\\\"term\\\": {\\\"tenant_id\\\": \\\"tenant_001\\\"}}\",
    \"allowed_actions\": [\"read\",\"search\",\"indices:monitor/stats\"]
  }]
}"
put_mapping test_tenant_only '{"backend_roles":["grp-finance-readers"],"users":["user-e2"]}'
echo "  E2 test_tenant_only -> user-e2"

# E3 — small static terms on access_groups
put_role test_static_groups "{
  \"cluster_permissions\": ${CLUSTER_PERMS},
  \"index_permissions\": [{
    \"index_patterns\": ${INDEX_PATTERNS},
    \"dls\": \"{\\\"terms\\\": {\\\"access_groups\\\": [\\\"grp-finance\\\",\\\"grp-controllers\\\"]}}\",
    \"allowed_actions\": [\"read\",\"search\",\"indices:monitor/stats\"]
  }]
}"
put_mapping test_static_groups '{"backend_roles":["grp-finance-readers"],"users":["user-e3"]}'
echo "  E3 test_static_groups -> user-e3"

# M1 — tenant_id + single group
put_role test_tenant_group "{
  \"cluster_permissions\": ${CLUSTER_PERMS},
  \"index_permissions\": [{
    \"index_patterns\": ${INDEX_PATTERNS},
    \"dls\": \"{\\\"bool\\\": {\\\"filter\\\": [{\\\"term\\\": {\\\"tenant_id\\\": \\\"tenant_001\\\"}},{\\\"terms\\\": {\\\"access_groups\\\": [\\\"grp-finance\\\"]}}]}}\",
    \"allowed_actions\": [\"read\",\"search\",\"indices:monitor/stats\"]
  }]
}"
put_mapping test_tenant_group '{"backend_roles":["grp-finance-readers"],"users":["user-m1"]}'
echo "  M1 test_tenant_group -> user-m1"

# M2 — tenant_id + dynamic identity (groups + roles + user.name)
# Single-quoted to prevent shell expanding ${user.roles} and ${user.name}
put_role test_full_identity '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\": {\"filter\": [{\"term\": {\"tenant_id\": \"tenant_001\"}}, {\"bool\": {\"should\": [{\"terms\": {\"access_groups\": ${user.roles}}}, {\"terms\": {\"access_roles\": ${user.roles}}}, {\"term\": {\"access_users\": \"${user.name}\"}}], \"minimum_should_match\": 1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping test_full_identity '{"backend_roles":["grp-finance-readers","grp-all-employees","role-employee"],"users":["user-m2"]}'
echo "  M2 test_full_identity -> user-m2"

# M3 — tenant_id + dynamic identity + security_classification
put_role test_identity_classification '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\": {\"filter\": [{\"term\": {\"tenant_id\": \"tenant_001\"}}, {\"bool\": {\"should\": [{\"terms\": {\"access_groups\": ${user.roles}}}, {\"terms\": {\"access_roles\": ${user.roles}}}, {\"term\": {\"access_users\": \"${user.name}\"}}], \"minimum_should_match\": 1}}, {\"bool\": {\"should\": [{\"term\": {\"security_classification\": \"internal\"}}, {\"bool\": {\"must_not\": {\"exists\": {\"field\": \"security_classification\"}}}}], \"minimum_should_match\": 1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping test_identity_classification '{"backend_roles":["grp-finance-readers","grp-all-employees","role-employee"],"users":["user-m3"]}'
echo "  M3 test_identity_classification -> user-m3"

# C1 — tenant_id + access_users AND access_groups must both match
put_role test_dual_validation '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\": {\"filter\": [{\"term\": {\"tenant_id\": \"tenant_001\"}}, {\"term\": {\"access_users\": \"${user.name}\"}}, {\"terms\": {\"access_groups\": ${user.roles}}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping test_dual_validation '{"backend_roles":["grp-finance-readers","grp-all-employees"],"users":["user-c1"]}'
echo "  C1 test_dual_validation -> user-c1"

# C3 — tenant_id + large static 50-entry access_users list
put_role test_large_users_list "{
  \"cluster_permissions\": ${CLUSTER_PERMS},
  \"index_permissions\": [{
    \"index_patterns\": ${INDEX_PATTERNS},
    \"dls\": \"{\\\"bool\\\": {\\\"filter\\\": [{\\\"term\\\": {\\\"tenant_id\\\": \\\"tenant_001\\\"}},{\\\"terms\\\": {\\\"access_users\\\": [\\\"user001@example.com\\\",\\\"user002@example.com\\\",\\\"user003@example.com\\\",\\\"user004@example.com\\\",\\\"user005@example.com\\\",\\\"user006@example.com\\\",\\\"user007@example.com\\\",\\\"user008@example.com\\\",\\\"user009@example.com\\\",\\\"user010@example.com\\\",\\\"user011@example.com\\\",\\\"user012@example.com\\\",\\\"user013@example.com\\\",\\\"user014@example.com\\\",\\\"user015@example.com\\\",\\\"user016@example.com\\\",\\\"user017@example.com\\\",\\\"user018@example.com\\\",\\\"user019@example.com\\\",\\\"user020@example.com\\\",\\\"user021@example.com\\\",\\\"user022@example.com\\\",\\\"user023@example.com\\\",\\\"user024@example.com\\\",\\\"user025@example.com\\\",\\\"user026@example.com\\\",\\\"user027@example.com\\\",\\\"user028@example.com\\\",\\\"user029@example.com\\\",\\\"user030@example.com\\\",\\\"user031@example.com\\\",\\\"user032@example.com\\\",\\\"user033@example.com\\\",\\\"user034@example.com\\\",\\\"user035@example.com\\\",\\\"user036@example.com\\\",\\\"user037@example.com\\\",\\\"user038@example.com\\\",\\\"user039@example.com\\\",\\\"user040@example.com\\\",\\\"user041@example.com\\\",\\\"user042@example.com\\\",\\\"user043@example.com\\\",\\\"user044@example.com\\\",\\\"user045@example.com\\\",\\\"user046@example.com\\\",\\\"user047@example.com\\\",\\\"user048@example.com\\\",\\\"user049@example.com\\\",\\\"user050@example.com\\\"]}}]}}\",
    \"allowed_actions\": [\"read\",\"search\",\"indices:monitor/stats\"]
  }]
}"
put_mapping test_large_users_list '{"backend_roles":["grp-finance-readers"],"users":["user-c3"]}'
echo "  C3 test_large_users_list -> user-c3"

# C5 — two tenant IDs (should) + dynamic identity + classification
put_role test_multi_tenant '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\": {\"filter\": [{\"bool\": {\"should\": [{\"term\": {\"tenant_id\": \"tenant_001\"}}, {\"term\": {\"tenant_id\": \"tenant_002\"}}], \"minimum_should_match\": 1}}, {\"bool\": {\"should\": [{\"terms\": {\"access_groups\": ${user.roles}}}, {\"terms\": {\"access_roles\": ${user.roles}}}, {\"term\": {\"access_users\": \"${user.name}\"}}], \"minimum_should_match\": 1}}, {\"bool\": {\"should\": [{\"term\": {\"security_classification\": \"internal\"}}, {\"bool\": {\"must_not\": {\"exists\": {\"field\": \"security_classification\"}}}}], \"minimum_should_match\": 1}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping test_multi_tenant '{"backend_roles":["grp-finance-readers","grp-all-employees","role-employee"],"users":["user-c5"]}'
echo "  C5 test_multi_tenant -> user-c5"

# C8 — tenant_id + named user match + must_not exists classification
put_role test_no_classification '{
  "cluster_permissions": ["cluster_composite_ops_ro","cluster:monitor/health","cluster:monitor/main","cluster:monitor/state","cluster:monitor/nodes/stats","cluster:monitor/nodes/info"],
  "index_permissions": [{
    "index_patterns": ["cohere-wiki-en-768-*","cohere-msmarco-1024-*"],
    "dls": "{\"bool\": {\"filter\": [{\"term\": {\"tenant_id\": \"tenant_001\"}}, {\"term\": {\"access_users\": \"${user.name}\"}}, {\"bool\": {\"must_not\": {\"exists\": {\"field\": \"security_classification\"}}}}]}}",
    "allowed_actions": ["read","search","indices:monitor/stats"]
  }]
}'
put_mapping test_no_classification '{"backend_roles":[],"users":["dave-contractor"]}'
echo "  C8 test_no_classification -> dave-contractor"

echo "[acl-roles] Done."
