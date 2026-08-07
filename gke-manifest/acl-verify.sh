#!/usr/bin/env bash
# Post-ingest checks. Run after bulk-ingest-data completes.
#
# Confirms:
#   A - index was created with default_pipeline: acl_guard
#   B - 10 random docs have ACL fields populated
#   C - DLS is restrictive: user-m2 sees fewer docs than admin

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
echo "--- B: 10 random docs — ACL field presence ---"
os_api -X POST "https://${OS_HOST}/${INDEX}/_search" \
  -H "Content-Type: application/json" \
  -d '{
    "size": 10,
    "_source": ["tenant_id","access_groups","access_users","security_classification"],
    "query": { "function_score": { "query": { "match_all": {} }, "random_score": {} } }
  }' 2>/dev/null \
  | jq -r '
    "total docs: \(.hits.total.value)",
    (.hits.hits[] | "  _id=\(._id)  tenant=\(._source.tenant_id // "-")  groups=\(._source.access_groups // [])  users=\(._source.access_users // [])  class=\(._source.security_classification // "-")")
  ' 2>/dev/null || echo "(index empty or not found)"

echo ""
echo "--- C: doc count — admin vs user-m2 (M2 DLS: tenant_001 + grp-finance) ---"
ADMIN_COUNT=$(os_api "https://${OS_HOST}/${INDEX}/_count" \
  2>/dev/null | jq -r '.count // "N/A"' 2>/dev/null || echo "N/A")
M2_COUNT=$(kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
  curl -sk -u "user-m2:BenchmarkACL-2024!" \
  "https://${OS_HOST}/${INDEX}/_count" \
  2>/dev/null | jq -r '.count // "N/A"' 2>/dev/null || echo "N/A")

echo "  admin:  ${ADMIN_COUNT}"
echo "  user-m2: ${M2_COUNT}  (expected ~500k)"

if [ "$ADMIN_COUNT" != "N/A" ] && [ "$M2_COUNT" != "N/A" ] && [ "$ADMIN_COUNT" != "0" ]; then
    if [ "$ADMIN_COUNT" -gt "$M2_COUNT" ] 2>/dev/null; then
        echo "  PASS — DLS is restrictive"
    else
        echo "  FAIL — DLS not restrictive (same count for both users)"
    fi
else
    echo "  SKIP — could not compare counts (index may be empty)"
fi

echo ""
echo "[acl-verify] Done."
