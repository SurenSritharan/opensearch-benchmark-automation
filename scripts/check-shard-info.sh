#!/bin/bash
# Check OpenSearch Shard Information
# This script provides detailed information about shards in your OpenSearch cluster

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
NAMESPACE="${1:-os-jvector}"
INDEX_NAME="${2:-*}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}OpenSearch Shard Information${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get the OpenSearch data or cluster pod
echo -e "${GREEN}Finding OpenSearch pod in namespace: ${NAMESPACE}${NC}"
CLIENT_POD=$(kubectl get pods -n "$NAMESPACE" -l app=opensearch-data -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || \
             kubectl get pods -n "$NAMESPACE" -l app=opensearch-cluster -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [ -z "$CLIENT_POD" ]; then
    echo -e "${YELLOW}No OpenSearch pod found in namespace ${NAMESPACE}${NC}"
    echo -e "${YELLOW}Looking for pods with 'opensearch' in the name...${NC}"
    CLIENT_POD=$(kubectl get pods -n "$NAMESPACE" -o jsonpath='{.items[?(@.metadata.name contains "opensearch")].metadata.name}' | awk '{print $1}')
fi

if [ -z "$CLIENT_POD" ]; then
    echo -e "${YELLOW}No OpenSearch pod found in namespace ${NAMESPACE}${NC}"
    echo -e "${YELLOW}Available pods:${NC}"
    kubectl get pods -n "$NAMESPACE"
    exit 1
fi

echo -e "Using pod: ${CLIENT_POD}"
echo ""

# Function to run curl command in pod
run_curl() {
    kubectl exec -n "$NAMESPACE" "$CLIENT_POD" -c opensearch -- \
        curl -s -k -u admin:admin \
        -H "Content-Type: application/json" \
        "https://localhost:9200$1" 2>/dev/null || \
    kubectl exec -n "$NAMESPACE" "$CLIENT_POD" -- \
        curl -s -k -u admin:admin \
        -H "Content-Type: application/json" \
        "https://localhost:9200$1"
}

echo -e "${GREEN}1. Shard Allocation Overview${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
run_curl "/_cat/shards/${INDEX_NAME}?v&h=index,shard,prirep,state,docs,store,node&s=index,shard"
echo ""

echo -e "${GREEN}2. Shard Count Summary${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
run_curl "/_cat/indices/${INDEX_NAME}?v&h=index,pri,rep,docs.count,store.size&s=index"
echo ""

echo -e "${GREEN}3. Detailed Shard Statistics${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SHARD_STATS=$(run_curl "/_cat/shards/${INDEX_NAME}?format=json")
echo "$SHARD_STATS" | python3 -c "
import json
import sys

data = json.load(sys.stdin)

# Count shards by type
primary_count = sum(1 for s in data if s.get('prirep') == 'p')
replica_count = sum(1 for s in data if s.get('prirep') == 'r')
total_count = len(data)

# Count by state
started = sum(1 for s in data if s.get('state') == 'STARTED')
unassigned = sum(1 for s in data if s.get('state') == 'UNASSIGNED')

# Get unique indices
indices = set(s.get('index', '') for s in data)

print(f'Total Shards:      {total_count}')
print(f'Primary Shards:    {primary_count}')
print(f'Replica Shards:    {replica_count}')
print(f'Started:           {started}')
print(f'Unassigned:        {unassigned}')
print(f'Indices:           {len(indices)}')
print()
print('Indices:', ', '.join(sorted(indices)))
"
echo ""

echo -e "${GREEN}4. Shard Distribution Across Nodes${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
run_curl "/_cat/allocation?v&h=node,shards,disk.indices,disk.used,disk.avail,disk.percent"
echo ""

echo -e "${GREEN}5. Index Settings (Shard Configuration)${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$INDEX_NAME" != "*" ]; then
    run_curl "/${INDEX_NAME}/_settings?pretty" | python3 -c "
import json
import sys

try:
    data = json.load(sys.stdin)
    for index_name, index_data in data.items():
        settings = index_data.get('settings', {}).get('index', {})
        print(f'Index: {index_name}')
        print(f'  Number of Shards:   {settings.get(\"number_of_shards\", \"N/A\")}')
        print(f'  Number of Replicas: {settings.get(\"number_of_replicas\", \"N/A\")}')
        print()
except:
    print('Could not parse index settings')
"
else
    echo "Specify an index name to see detailed settings"
    echo "Usage: $0 <namespace> <index-name>"
fi
echo ""

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Quick Reference:${NC}"
echo -e "${BLUE}========================================${NC}"
echo "• Primary Shards: Original data shards"
echo "• Replica Shards: Copies for redundancy"
echo "• Total Shards = Primary × (1 + Replicas)"
echo ""
echo "Example: 5 primary shards with 1 replica = 10 total shards"
echo ""
echo -e "${YELLOW}To check a specific index:${NC}"
echo "  $0 $NAMESPACE <index-name>"
echo ""

# Made with Bob