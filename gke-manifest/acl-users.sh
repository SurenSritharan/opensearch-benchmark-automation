#!/usr/bin/env bash
# Creates one internal user per DLS role.
#
# Each user is mapped to exactly one role in acl-roles.sh.
# Users whose DLS role uses ${user.roles} need a backend_role that matches
# the corpus access_groups values (grp-finance).
# Users whose DLS role uses only static values need no backend_roles.

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

# Static-filter roles — DLS does not reference ${user.roles}, no backend_roles needed
create_user user-e1  '[]'
create_user user-e2  '[]'
create_user user-e3  '[]'
create_user user-m1  '[]'
create_user user-c3  '[]'

# Dynamic-filter roles — DLS expands ${user.roles}, must match corpus access_groups
create_user user-m2  '["grp-finance"]'
create_user user-m3  '["grp-finance"]'
create_user user-c1  '["grp-finance"]'
create_user user-c5  '["grp-finance"]'

# C8 contractor — matched by ${user.name} = "dave-contractor" in corpus access_users
create_user dave-contractor '[]'

echo "[acl-users] Done."
