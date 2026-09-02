#!/usr/bin/env bash
# Creates 11 internal users, one per DLS scenario.
#
# Each user's backend_roles simulates their JWT claims in production:
#   - backend_roles values are expanded into ${user.roles} by OpenSearch Security
#     at query time and matched against access_roles / access_groups fields.
#   - The username itself is expanded into ${user.name} and matched against
#     access_users fields.
#   - security_classification is NOT used. Access control is enforced purely
#     through access_roles, access_groups, and access_users (see acl-roles.sh).
#
# Standard users (dls-baseline … dls-group-sensitive):
#   DLS filter is a bool.should (OR) across access_roles, access_groups, access_users.
#   backend_roles must match the values written by the ingest pipeline.
#
# Ultrastrict users (dls-ultrastrict … dls-ultrastrict-fixed):
#   DLS filter is a hard bool.filter (AND) requiring all three fields to match.
#   Ingest pipeline writes "need-to-know" in access_roles AND access_groups on
#   ultrastrict docs, so users must carry "need-to-know" in their backend_roles.
#
# User -> backend_roles -> DLS Role -> what they can see:
#   user-unrestricted        ["grp-broad"]                   dls-baseline           (~1M    no DLS baseline)
#   user-role-only           ["role-svc"]                    dls-role-only          (~400k  role-only docs)
#   user-group-only          ["grp-finance"]                 dls-group-only         (~430k  group-only docs)
#   user-name-only           []                              dls-name-only          (~100k  name-only docs)
#   user-role-group          ["role-svc","grp-finance"]      dls-role-group         (~780k  role OR group docs)
#   user-group-name          ["grp-finance"]                 dls-group-name         (~430k  group OR name docs)
#   user-group-sensitive          ["grp-sensitive"]              dls-group-sensitive    (~60k   grp-sensitive docs)
#   user-ultrastrict         ["need-to-know"]                dls-ultrastrict        (~50k   buckets 95–99, triple AND)
#   user-ultrastrict-narrow  ["need-to-know"]                dls-ultrastrict-narrow (~10k   bucket 94, triple AND)
#   user-ultrastrict-micro   ["need-to-know"]                dls-ultrastrict-micro  (~6k    Tier B id%1000<6, triple AND)
#   user-ultrastrict-fixed   ["need-to-know"]                dls-ultrastrict-fixed  (5,000  Tier A id<5000, triple AND)

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

# dls-group-sensitive role — grp-sensitive in ${user.roles} matches access_groups on buckets 88–93.
# Must NOT include need-to-know — that would bleed into ultrastrict docs via the should-OR filter.
create_user user-group-sensitive '["grp-sensitive"]'

# dls-ultrastrict role — need-to-know in ${user.roles} matches access_roles AND access_groups
# on ultrastrict docs (buckets 95–99); username matches access_users. Triple AND DLS.
create_user user-ultrastrict '["need-to-know"]'

# dls-ultrastrict-narrow role — same triple AND logic, dedicated to bucket 94 (~10k docs).
create_user user-ultrastrict-narrow '["need-to-know"]'

# dls-ultrastrict-micro role — same triple AND logic, Tier B: id%1000<6 outside first 5k (~6k docs).
create_user user-ultrastrict-micro '["need-to-know"]'

# dls-ultrastrict-fixed role — same triple AND logic, Tier A: id<5000 (exactly 5,000 docs).
create_user user-ultrastrict-fixed '["need-to-know"]'

echo "[acl-users] Done."
