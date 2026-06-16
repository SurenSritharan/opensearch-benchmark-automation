#!/bin/bash

# Destroy all OpenSearch namespaces by calling destroy-namespace-cluster.sh
# Usage: ./destroy-all-clusters.sh [--force]

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Define namespaces
NAMESPACES=(
    "os-jvector"
    "os-faiss"
    "os-lucene"
)

echo -e "${RED}=========================================="
echo "⚠️  DESTROY ALL OPENSEARCH CLUSTERS"
echo -e "==========================================${NC}"
echo ""
echo "This will delete all resources in namespaces:"
for ns in "${NAMESPACES[@]}"; do
    echo "  - $ns"
done
echo ""
echo -e "${YELLOW}WARNING: Cluster resources will be deleted!${NC}"
echo -e "${GREEN}NOTE: PVCs will be preserved by default (data is safe).${NC}"
echo -e "${YELLOW}To also delete PVCs, you must manually run destroy-namespace-cluster.sh with --delete-pvcs${NC}"
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

# Iterate over all namespaces and call destroy-namespace-cluster.sh
for ns in "${NAMESPACES[@]}"; do
    echo -e "${YELLOW}Destroying cluster in namespace: $ns${NC}"
    "$SCRIPT_DIR/destroy-namespace-cluster.sh" "$ns" --force
    echo ""
done

# Scale down metrics store (preserving data) instead of destroying
echo -e "${YELLOW}Scaling down metrics store (preserving data): os-metrics${NC}"
if kubectl get namespace os-metrics &> /dev/null; then
    # Scale down metrics store StatefulSet
    if kubectl get statefulset opensearch-metrics-store -n os-metrics &> /dev/null; then
        kubectl scale statefulset opensearch-metrics-store -n os-metrics --replicas=0
        echo -e "${GREEN}  ✓ Metrics store scaled down to 0 replicas${NC}"
    fi
    
    # Scale down dashboards if they exist
    if kubectl get deployment opensearch-dashboards -n os-metrics &> /dev/null; then
        kubectl scale deployment opensearch-dashboards -n os-metrics --replicas=0
        echo -e "${GREEN}  ✓ Dashboards scaled down to 0 replicas${NC}"
    fi
    
    echo -e "${GREEN}  💾 Metrics data preserved in PersistentVolumeClaims${NC}"
    echo ""
else
    echo -e "${YELLOW}  ℹ️  Metrics store namespace not found, skipping${NC}"
    echo ""
fi

echo ""
echo -e "${GREEN}=========================================="
echo "✓ All clusters destroyed/scaled down"
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
