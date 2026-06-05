#!/bin/bash

# Test script to verify index creation with parameterized shard/replica settings
# This script tests the msmarco workload index template rendering

set -e

NAMESPACE="${1:-os-jvector}"
TEST_INDEX="test-msmarco-shards-$(date +%s)"

echo "=========================================="
echo "Testing Index Creation with Parameters"
echo "Namespace: $NAMESPACE"
echo "Test Index: $TEST_INDEX"
echo "=========================================="
echo ""

# Check if benchmark client pod exists
if ! kubectl get pod -n "$NAMESPACE" opensearch-benchmark-client &>/dev/null; then
  echo "❌ Error: opensearch-benchmark-client pod not found in namespace $NAMESPACE"
  exit 1
fi

echo "📝 Step 1: Creating test parameter file with custom shard settings..."
cat > /tmp/test-params.json <<EOF
{
  "target_index_name": "$TEST_INDEX",
  "target_index_body": "indices/index.json",
  "target_index_primary_shards": 3,
  "target_index_replica_shards": 0,
  "target_index_dimension": 1024,
  "engine": "jvector",
  "space_type": "cosinesimil"
}
EOF

echo "✅ Test parameters created:"
cat /tmp/test-params.json
echo ""

echo "📤 Step 2: Copying test parameters to pod..."
kubectl cp /tmp/test-params.json "$NAMESPACE/opensearch-benchmark-client:/tmp/test-params.json" -c benchmark
echo "✅ Parameters copied to pod"
echo ""

echo "🔨 Step 3: Creating index using OpenSearch Benchmark..."
kubectl exec -it -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  opensearch-benchmark run \
  --workload-path=/root/custom-workloads/msmarco \
  --test-procedure=index-only \
  --workload-params=/tmp/test-params.json \
  --target-hosts=opensearch-cluster:9200 \
  --client-options="timeout:300,use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin" \
  --kill-running-processes 2>&1 | tee /tmp/osb-output.log

echo ""
echo "✅ Index creation command executed"
echo ""

# Wait a moment for index to be fully created
sleep 3

echo "🔍 Step 4: Verifying index settings..."
INDEX_SETTINGS=$(kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${TEST_INDEX}/_settings?pretty" 2>/dev/null)

echo "$INDEX_SETTINGS"
echo ""

# Extract shard and replica counts
ACTUAL_SHARDS=$(echo "$INDEX_SETTINGS" | jq -r ".\"$TEST_INDEX\".settings.index.number_of_shards // \"not found\"")
ACTUAL_REPLICAS=$(echo "$INDEX_SETTINGS" | jq -r ".\"$TEST_INDEX\".settings.index.number_of_replicas // \"not found\"")

echo "=========================================="
echo "Test Results"
echo "=========================================="
echo "Expected Shards:   3"
echo "Actual Shards:     $ACTUAL_SHARDS"
echo ""
echo "Expected Replicas: 2"
echo "Actual Replicas:   $ACTUAL_REPLICAS"
echo ""

# Verify results
SUCCESS=true

if [ "$ACTUAL_SHARDS" != "3" ]; then
  echo "❌ FAILED: Shard count mismatch!"
  SUCCESS=false
else
  echo "✅ PASSED: Shard count matches"
fi

if [ "$ACTUAL_REPLICAS" != "2" ]; then
  echo "❌ FAILED: Replica count mismatch!"
  SUCCESS=false
else
  echo "✅ PASSED: Replica count matches"
fi

echo ""
echo "🗺️  Index Mapping (Vector Field):"
echo "----------------------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${TEST_INDEX}/_mapping?pretty" 2>/dev/null | \
  jq ".\"$TEST_INDEX\".mappings.properties.vector"

echo ""
echo "🧹 Step 5: Cleaning up test index..."
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin -X DELETE "https://opensearch-cluster:9200/${TEST_INDEX}" 2>/dev/null
echo "✅ Test index deleted"

echo ""
echo "=========================================="
if [ "$SUCCESS" = true ]; then
  echo "✅ ALL TESTS PASSED"
  echo "Index creation with parameterized shards/replicas is working correctly!"
else
  echo "❌ TESTS FAILED"
  echo "Index creation is NOT respecting the parameter settings"
  exit 1
fi
echo "=========================================="

# Cleanup
rm -f /tmp/test-params.json /tmp/osb-output.log

# Made with Bob