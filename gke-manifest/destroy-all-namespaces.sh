#!/bin/bash

# Destroy all OpenSearch namespaces by deleting manifest files
# Usage: ./destroy-all-namespaces.sh [--force]

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Define namespaces and their types (namespace:type)
NAMESPACES=(
    "os-jvector:jvector"
    "os-faiss:standard"
    "os-lucene:standard"
)

echo -e "${RED}=========================================="
echo "⚠️  DESTROY ALL OPENSEARCH CLUSTERS"
echo -e "==========================================${NC}"
echo ""
echo "This will delete all resources in namespaces:"
for entry in "${NAMESPACES[@]}"; do
    ns="${entry%%:*}"
    type="${entry##*:}"
    echo "  - $ns ($type manifests)"
done
echo ""
echo -e "${YELLOW}WARNING: This action cannot be undone!${NC}"
echo -e "${YELLOW}All data, configurations, and resources will be permanently deleted.${NC}"
echo ""

# Check for --force flag
if [[ "$1" != "--force" ]]; then
    read -p "Are you sure you want to continue? (type 'yes' to confirm): " confirmation
    if [[ "$confirmation" != "yes" ]]; then
        echo -e "${GREEN}Operation cancelled.${NC}"
        exit 0
    fi
fi

echo ""
echo -e "${RED}Starting resource deletion...${NC}"
echo ""

# Function to delete resources for a namespace
delete_namespace_resources() {
    local ns=$1
    local type=$2
    
    echo -e "${YELLOW}Deleting resources in namespace: $ns${NC}"
    
    if [[ "$type" == "jvector" ]]; then
        # Delete JVector-specific manifests
        kubectl delete -f "$SCRIPT_DIR/opensearch-jvector-data-nodes.yaml" --ignore-not-found=true
        kubectl delete -f "$SCRIPT_DIR/opensearch-jvector-cluster-manager.yaml" --ignore-not-found=true
    else
        # Delete standard manifests with namespace substitution
        sed "s/\${NAMESPACE}/$ns/g" "$SCRIPT_DIR/opensearch-standard-data-nodes.yaml" | kubectl delete -f - --ignore-not-found=true
        sed "s/\${NAMESPACE}/$ns/g" "$SCRIPT_DIR/opensearch-standard-cluster-manager.yaml" | kubectl delete -f - --ignore-not-found=true
    fi
    
    # Delete benchmark client
    kubectl delete -f "$SCRIPT_DIR/opensearch-benchmark-client.yaml" -n "$ns" --ignore-not-found=true
    
    echo -e "  ${GREEN}✓ Resources deleted from $ns${NC}"
}

# Iterate over all namespaces and delete resources
for entry in "${NAMESPACES[@]}"; do
    ns="${entry%%:*}"
    type="${entry##*:}"
    delete_namespace_resources "$ns" "$type"
    echo ""
done

# Delete namespaces
echo -e "${YELLOW}Deleting namespaces...${NC}"
for entry in "${NAMESPACES[@]}"; do
    ns="${entry%%:*}"
    kubectl delete namespace "$ns" --ignore-not-found=true
    echo "  ✓ Namespace $ns deletion initiated"
done

echo ""
echo -e "${GREEN}=========================================="
echo "✓ All resource deletions initiated"
echo -e "==========================================${NC}"
echo ""
echo "Note: Namespace deletion is asynchronous and may take a few minutes to complete."
echo ""
echo "Monitor deletion progress with:"
echo "  kubectl get namespaces | grep 'os-'"
echo ""
echo "Or watch in real-time:"
echo "  watch 'kubectl get namespaces | grep os-'"
echo ""
