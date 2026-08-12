#!/usr/bin/env bash
# Creates one internal user per DLS scenario.
#
# Each user's backend_roles simulates their JWT claims in production:
#   - backend_roles values that match corpus access_roles/access_groups fields
#     are matched via ${user.roles} expansion in the dls-standard filter
#   - The username itself matches corpus access_users fields via ${user.name}
#   - Classification clearance is encoded as the classification level string
#     inside backend_roles (e.g. "restricted") — the DLS filter expands
#     terms: security_classification: ${user.roles} which then matches docs
#     whose security_classification field equals that value
#
# User -> backend_roles -> what they can see:
#   user-unrestricted  ["grp-broad"]                      no DLS (dls-baseline role)
#   user-role-only     ["role-svc"]                       access_roles:role-svc docs (~35%)
#   user-group-only    ["grp-finance"]                    access_groups:grp-finance docs (~40%)
#   user-name-only     []                                 access_users:user-name-only docs (~10%)
#   user-role-group    ["role-svc","grp-finance"]          role-svc OR grp-finance docs (~67%)
#   user-group-name    ["grp-finance"]                    grp-finance docs + user-group-name docs (~40%)
#   user-classified    ["grp-restricted","restricted"]    grp-restricted docs, restricted clearance (~6%)
#   user-ultrastrict   []                                 access_users:user-ultrastrict + restricted only (~1%)

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

# dls-baseline role — no DLS, backend_roles just satisfies role mapping
create_user user-unrestricted '["grp-broad"]'

# dls-standard role — backend_roles = simulated JWT claims
create_user user-role-only   '["role-svc"]'
create_user user-group-only  '["grp-finance"]'
create_user user-name-only   '[]'
create_user user-role-group  '["role-svc","grp-finance"]'
create_user user-group-name  '["grp-finance"]'
create_user user-classified  '["grp-restricted","restricted"]'

# dls-ultrastrict role — identity-only match, hard restricted classification required
create_user user-ultrastrict '[]'

echo "[acl-users] Done."
