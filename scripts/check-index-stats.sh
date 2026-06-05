#!/bin/bash

# Script to check current index statistics from OpenSearch cluster

NAMESPACE="${1:-os-jvector}"
INDEX_NAME="${2:-jvector_index}"

echo "=========================================="
echo "OpenSearch Index Statistics"
echo "Namespace: $NAMESPACE"
echo "Index: $INDEX_NAME"
echo "=========================================="
echo ""

# Check if benchmark client pod exists
if ! kubectl get pod -n "$NAMESPACE" opensearch-benchmark-client &>/dev/null; then
  echo "❌ Error: opensearch-benchmark-client pod not found in namespace $NAMESPACE"
  echo "Available namespaces:"
  kubectl get namespaces | grep -E "os-|opensearch"
  exit 1
fi

echo "📊 Index Overview:"
echo "-------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/_cat/indices/${INDEX_NAME}?v&h=index,health,status,pri,rep,docs.count,store.size,pri.store.size"

echo ""
echo "📈 Detailed Index Stats:"
echo "------------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${INDEX_NAME}/_stats?pretty" | jq '{
    index: .indices | keys[0],
    total: {
      docs: .indices[].total.docs,
      store: .indices[].total.store,
      segments: .indices[].total.segments,
      search: .indices[].total.search,
      indexing: .indices[].total.indexing,
      merges: .indices[].total.merges
    }
  }'

echo ""
echo "⚙️  Index Settings:"
echo "-------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${INDEX_NAME}/_settings?pretty" | jq '.[] | {
    number_of_shards: .settings.index.number_of_shards,
    number_of_replicas: .settings.index.number_of_replicas,
    refresh_interval: .settings.index.refresh_interval,
    knn: .settings.index.knn
  }'

echo ""
echo "🗺️  Index Mapping (Vector Field):"
echo "----------------------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${INDEX_NAME}/_mapping?pretty" | jq '.[] | .mappings.properties | to_entries[] | select(.value.type == "knn_vector")'

echo ""
echo "🔍 Cluster Health:"
echo "------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/_cluster/health?pretty" | jq '{
    cluster_name,
    status,
    number_of_nodes,
    number_of_data_nodes,
    active_primary_shards,
    active_shards,
    relocating_shards,
    initializing_shards,
    unassigned_shards
  }'

echo ""
echo "✅ Index stats check complete"

# Made with Bob
