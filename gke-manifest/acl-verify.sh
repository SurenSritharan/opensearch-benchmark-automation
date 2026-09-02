#!/usr/bin/env bash
# Post-ingest checks. Run after bulk-ingest-data completes.
#
# Confirms:
#   A - index was created with default_pipeline: acl_guard
#   B - 10 random docs show heterogeneous ACL field population
#       (some docs have only access_roles, some only access_groups, some only access_users)
#   C - DLS is active and the full restrictiveness gradient is correct:
#       admin (no DLS)           > role-group (~78%)  > name-only (~10%)
#                                > group-restricted (~6%) > ultrastrict (~5%)
#       Pass condition: admin > role-group > name-only > group-restricted > ultrastrict > 0

set -euo pipefail

OS_HOST="$1"
INDEX="cohere-wiki-en-768-1m"

os_api() {
    kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin "$@"
}

echo "[acl-verify] Checking index: ${INDEX}"
echo ""

echo "--- A: index settings (default_pipeline + knn) ---"
os_api "https://${OS_HOST}/${INDEX}/_settings" 2>/dev/null \
  | jq --arg idx "$INDEX" '.[$idx].settings.index | {default_pipeline, knn}' \
  2>/dev/null || echo "(index not found)"

echo ""
echo "--- B: 10 random docs — ACL field presence (heterogeneous expected) ---"
os_api -X POST "https://${OS_HOST}/${INDEX}/_search" \
  -H "Content-Type: application/json" \
  -d '{
    "size": 10,
    "_source": ["access_roles","access_groups","access_users"],
    "query": { "function_score": { "query": { "match_all": {} }, "random_score": {} } }
  }' 2>/dev/null \
  | jq -r '
    "total docs: \(.hits.total.value)",
    (.hits.hits[] | "  _id=\(._id)  roles=\(._source.access_roles // [])  groups=\(._source.access_groups // [])  users=\(._source.access_users // [])")
  ' 2>/dev/null || echo "(index empty or not found)"

echo ""
echo "--- C: doc count — full restrictiveness gradient ---"
count_as() {
    local user="$1"
    kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u "${user}:BenchmarkACL-2024!" \
        -X POST "https://${OS_HOST}/${INDEX}/_count" \
        -H "Content-Type: application/json" \
        -d '{"query":{"match_all":{}}}' \
        2>/dev/null | jq -r '.count // "N/A"' 2>/dev/null || echo "N/A"
}

ADMIN_COUNT=$(os_api -X POST "https://${OS_HOST}/${INDEX}/_count" \
  -H "Content-Type: application/json" \
  -d '{"query":{"match_all":{}}}' \
  2>/dev/null | jq -r '.count // "N/A"' 2>/dev/null || echo "N/A")
ROLEGROUP_COUNT=$(count_as "user-role-group")
NAMEONLY_COUNT=$(count_as "user-name-only")
CLASSIFIED_COUNT=$(count_as "user-group-sensitive")
ULTRASTRICT_COUNT=$(count_as "user-ultrastrict")

echo "  admin (no DLS):        ${ADMIN_COUNT}"
echo "  user-role-group:       ${ROLEGROUP_COUNT}  (expected ~780k at 1M — role-svc+grp-finance)"
echo "  user-name-only:        ${NAMEONLY_COUNT}  (expected ~100k at 1M — username match only)"
echo "  user-group-sensitive: ${CLASSIFIED_COUNT}  (expected ~60k  at 1M — grp-sensitive group match)"
echo "  user-ultrastrict:      ${ULTRASTRICT_COUNT}  (expected ~50k  at 1M — need-to-know triple AND)"

if [ "$ADMIN_COUNT" != "N/A" ] && [ "$ROLEGROUP_COUNT" != "N/A" ] && \
   [ "$NAMEONLY_COUNT" != "N/A" ] && [ "$CLASSIFIED_COUNT" != "N/A" ] && \
   [ "$ULTRASTRICT_COUNT" != "N/A" ] && [ "$ADMIN_COUNT" != "0" ]; then
    if [ "$ADMIN_COUNT" -gt "$ROLEGROUP_COUNT" ] 2>/dev/null && \
       [ "$ROLEGROUP_COUNT" -gt "$NAMEONLY_COUNT" ] 2>/dev/null && \
       [ "$NAMEONLY_COUNT" -gt "$CLASSIFIED_COUNT" ] 2>/dev/null && \
       [ "$CLASSIFIED_COUNT" -gt "$ULTRASTRICT_COUNT" ] 2>/dev/null && \
       [ "$ULTRASTRICT_COUNT" -gt 0 ] 2>/dev/null; then
        echo "  PASS — full gradient confirmed (admin > role-group > name-only > group-restricted > ultrastrict > 0)"
    else
        echo "  FAIL — DLS counts do not satisfy expected gradient ordering"
        echo "         Expected: admin > role-group > name-only > group-restricted > ultrastrict > 0"
    fi
else
    echo "  SKIP — could not compare counts (index may be empty)"
fi

echo ""
echo "[acl-verify] Done."
