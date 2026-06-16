#!/bin/bash

# Script to check current index statistics from OpenSearch cluster
# Interactive mode: lists namespaces and indices, allows user to select

echo "=========================================="
echo "OpenSearch Index Statistics"
echo "=========================================="
echo ""

# Function to select namespace
select_namespace() {
  if [ -n "$1" ]; then
    echo "Using specified namespace: $1"
    NAMESPACE="$1"
    return
  fi
  
  echo "📋 Fetching available namespaces..."
  NAMESPACES=$(kubectl get namespaces -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep -E '^os-')
  
  if [ -z "$NAMESPACES" ]; then
    echo "❌ No OpenSearch namespaces found (looking for 'os-*' pattern)"
    echo ""
    echo "All namespaces:"
    kubectl get namespaces
    exit 1
  fi
  
  # Convert to array using while loop (portable)
  NS_ARRAY=()
  while IFS= read -r line; do
    NS_ARRAY+=("$line")
  done <<< "$NAMESPACES"
  
  echo ""
  echo "Available Namespaces:"
  echo "---------------------"
  for i in "${!NS_ARRAY[@]}"; do
    echo "  $((i+1))) ${NS_ARRAY[$i]}"
  done
  echo ""
  
  read -p "Select namespace (1-${#NS_ARRAY[@]}): " NS_SELECTION
  
  if ! [[ "$NS_SELECTION" =~ ^[0-9]+$ ]] || [ "$NS_SELECTION" -lt 1 ] || [ "$NS_SELECTION" -gt ${#NS_ARRAY[@]} ]; then
    echo "❌ Invalid selection"
    exit 1
  fi
  
  NAMESPACE="${NS_ARRAY[$((NS_SELECTION-1))]}"
  echo ""
  echo "Selected namespace: $NAMESPACE"
}

# Select namespace
select_namespace "$1"

echo ""
echo "=========================================="
echo "Namespace: $NAMESPACE"
echo "=========================================="
echo ""

# Check if benchmark client pod exists
if ! kubectl get pod -n "$NAMESPACE" opensearch-benchmark-client-0 &>/dev/null; then
  echo "❌ Error: opensearch-benchmark-client pod not found in namespace $NAMESPACE"
  echo ""
  echo "Available pods in $NAMESPACE:"
  kubectl get pods -n "$NAMESPACE"
  exit 1
fi

# Get list of all indices
echo "📋 Fetching available indices..."
INDICES_OUTPUT=$(kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client-0 -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/_cat/indices?v&h=index,health,status,docs.count,store.size" 2>/dev/null)

if [ -z "$INDICES_OUTPUT" ]; then
  echo "❌ Error: Could not retrieve indices from cluster"
  exit 1
fi

# Parse indices into array using while loop (portable alternative to mapfile)
INDICES=()
while IFS= read -r line; do
  # Skip header line
  if [[ "$line" =~ ^index ]]; then
    continue
  fi
  # Extract index name (first column)
  INDEX=$(echo "$line" | awk '{print $1}')
  if [ -n "$INDEX" ]; then
    INDICES+=("$INDEX")
  fi
done <<< "$INDICES_OUTPUT"

if [ ${#INDICES[@]} -eq 0 ]; then
  echo "⚠️  No indices found in the cluster"
  exit 0
fi

# Display indices with numbers
echo ""
echo "Available Indices:"
echo "-------------------"
echo "$INDICES_OUTPUT"
echo ""

# If index name provided as second argument, use it directly
if [ -n "$2" ]; then
  INDEX_NAME="$2"
  echo "Using specified index: $INDEX_NAME"
else
  # Interactive selection
  echo "Select an index to view detailed statistics:"
  for i in "${!INDICES[@]}"; do
    echo "  $((i+1))) ${INDICES[$i]}"
  done
  echo ""
  
  # Read user selection
  read -p "Enter selection (1-${#INDICES[@]}): " SELECTION
  
  # Validate selection
  if ! [[ "$SELECTION" =~ ^[0-9]+$ ]] || [ "$SELECTION" -lt 1 ] || [ "$SELECTION" -gt ${#INDICES[@]} ]; then
    echo "❌ Invalid selection"
    exit 1
  fi
  
  # Get selected index (array is 0-indexed, user input is 1-indexed)
  INDEX_NAME="${INDICES[$((SELECTION-1))]}"
fi

echo ""
echo "=========================================="
echo "Detailed Statistics for: $INDEX_NAME"
echo "=========================================="
echo ""

echo "📊 Index Overview:"
echo "-------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client-0 -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/_cat/indices/${INDEX_NAME}?v&h=index,health,status,pri,rep,docs.count,store.size,pri.store.size"

echo ""
echo "📈 Detailed Index Stats:"
echo "------------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client-0 -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${INDEX_NAME}/_stats?pretty" 2>/dev/null | jq '{
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
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client-0 -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${INDEX_NAME}/_settings?pretty" 2>/dev/null | jq '.[] | {
    number_of_shards: .settings.index.number_of_shards,
    number_of_replicas: .settings.index.number_of_replicas,
    refresh_interval: .settings.index.refresh_interval,
    knn: .settings.index.knn
  }'

echo ""
echo "🗺️  Index Mapping (Vector Field):"
echo "----------------------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client-0 -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/${INDEX_NAME}/_mapping?pretty" 2>/dev/null | jq '.[] | .mappings.properties | to_entries[] | select(.value.type == "knn_vector")'

echo ""
echo "🔍 Cluster Health:"
echo "------------------"
kubectl exec -n "$NAMESPACE" -c benchmark opensearch-benchmark-client-0 -- \
  curl -sk -u admin:admin "https://opensearch-cluster:9200/_cluster/health?pretty" 2>/dev/null | jq '{
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
echo "✅ Index stats check complete for: $INDEX_NAME"

# Made with Bob
