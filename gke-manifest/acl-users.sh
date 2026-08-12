#!/usr/bin/env bash
# Creates 8 internal users, one per DLS scenario.
#
# Each user's backend_roles simulates their JWT claims in production:
#   - backend_roles values that match corpus access_roles/access_groups fields
#     are matched via ${user.roles} expansion in the DLS filter
#   - The username itself matches corpus access_users fields via ${user.name}
#   - Classification clearance is encoded as the classification level string
#     inside backend_roles (e.g. "restricted") — the DLS filter expands
#     terms: security_classification: ${user.roles} which then matches docs
#     whose security_classification field equals that value
#
# User -> backend_roles -> DLS Role -> what they can see:
#   user-unrestricted  ["grp-broad"]                      dls-baseline        (~1M no DLS baseline)
#   user-role-only     ["role-svc"]                       dls-role-only       (~320k role-only docs)
#   user-group-only    ["grp-finance"]                    dls-group-only      (~400k group-only docs)
#   user-name-only     []                                 dls-name-only       (~100k name-only docs)
#   user-role-group    ["role-svc","grp-finance"]         dls-role-group      (~640k role OR group docs)
#   user-group-name    ["grp-finance"]                    dls-group-name      (~400k group OR name docs)
#   user-classified    ["grp-restricted","restricted"]    dls-classified      (~60k restricted group + classified)
#   user-ultrastrict   []                                 dls-ultrastrict     (~10k hard restricted only)

set -euo pipefail

OS_HOST="$1"

os_security() {
    kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin \
        --cert /certs/admin.pem \
        --key  /certs/admin-key.pem \
        "$@"
}

create_user() {
    local username="$1"
    local backend_roles="$2"
    os_security -X PUT "https://${OS_HOST}/_plugins/_security/api/internalusers/${username}" \
        -H "Content-Type: application/json" \
        -d "{\"password\":\"BenchmarkACL-2024!\",\"backend_roles\":${backend_roles},\"attributes\":{}}"
    echo "  ${username}  backend_roles=${backend_roles}"
}

echo "[acl-users] Creating users..."

# dls-baseline role — no DLS, backend_roles satisfies role mapping
create_user user-unrestricted '["grp-broad"]'

# dls-role-only role — backend_roles simulates JWT with role-svc
create_user user-role-only '["role-svc"]'

# dls-group-only role — backend_roles simulates JWT with grp-finance group
create_user user-group-only '["grp-finance"]'

# dls-name-only role — identity-only match; no classification clearance needed (buckets 68–77 have no classification)
create_user user-name-only '[]'

# dls-role-group role — backend_roles simulates JWT with role-svc AND grp-finance
create_user user-role-group '["role-svc","grp-finance"]'

# dls-group-name role — backend_roles simulates JWT with grp-finance + username match
create_user user-group-name '["grp-finance"]'

# dls-classified role — backend_roles with grp-restricted group and restricted clearance
create_user user-classified '["grp-restricted","restricted"]'

# dls-ultrastrict role — identity-only match, MUST have "restricted" clearance for bucket 99
create_user user-ultrastrict '["restricted"]'

echo "[acl-users] Done."
