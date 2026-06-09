#!/bin/bash

# Destroy a specific OpenSearch namespace cluster by deleting manifest files
# Usage: ./destroy-namespace-cluster.sh <namespace> [--force]

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if namespace argument is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <namespace> [--force]"
    echo "Example: $0 os-jvector"
    echo ""
    echo "Supported namespaces:"
    echo "  - os-jvector"
    echo "  - os-faiss"
    echo "  - os-lucene"
    exit 1
fi

NAMESPACE=$1
FORCE_FLAG=$2

# Determine which manifests to use based on namespace
case "$NAMESPACE" in
    os-jvector)
        TYPE="jvector"
        ;;
    os-faiss|os-lucene)
        TYPE="standard"
        ;;
    *)
        echo -e "${RED}Error: Unknown namespace '$NAMESPACE'${NC}"
        echo "Supported namespaces: os-jvector, os-faiss, os-lucene"
        exit 1
        ;;
esac

echo -e "${RED}=========================================="
echo "⚠️  DESTROY OPENSEARCH CLUSTER"
echo -e "==========================================${NC}"
echo ""
echo "This will delete all resources in namespace: $NAMESPACE"
echo ""
echo -e "${YELLOW}WARNING: This action cannot be undone!${NC}"
echo -e "${YELLOW}All data, configurations, and resources will be permanently deleted.${NC}"
echo ""

# Check for --force flag
if [[ "$FORCE_FLAG" != "--force" ]]; then
    read -p "Are you sure you want to continue? (type 'yes' to confirm): " confirmation
    if [[ "$confirmation" != "yes" ]]; then
        echo -e "${GREEN}Operation cancelled.${NC}"
        exit 0
    fi
fi

echo ""
echo -e "${RED}Starting resource deletion...${NC}"
echo ""

echo -e "${YELLOW}Deleting resources in namespace: $NAMESPACE${NC}"

if [[ "$TYPE" == "jvector" ]]; then
    # Delete JVector-specific manifests
    kubectl delete -f "$SCRIPT_DIR/opensearch-jvector-data-nodes.yaml" --ignore-not-found=true
    kubectl delete -f "$SCRIPT_DIR/opensearch-jvector-cluster-manager.yaml" --ignore-not-found=true
else
    # Delete standard manifests with namespace substitution
    sed "s/\${NAMESPACE}/$NAMESPACE/g" "$SCRIPT_DIR/opensearch-standard-data-nodes.yaml" | kubectl delete -f - --ignore-not-found=true
    sed "s/\${NAMESPACE}/$NAMESPACE/g" "$SCRIPT_DIR/opensearch-standard-cluster-manager.yaml" | kubectl delete -f - --ignore-not-found=true
fi

# Delete benchmark client
kubectl delete -f "$SCRIPT_DIR/opensearch-benchmark-client.yaml" -n "$NAMESPACE" --ignore-not-found=true

echo -e "  ${GREEN}✓ Resources deleted from $NAMESPACE${NC}"
echo ""

# Delete namespace
echo -e "${YELLOW}Deleting namespace...${NC}"
kubectl delete namespace "$NAMESPACE" --ignore-not-found=true
echo "  ✓ Namespace $NAMESPACE deletion initiated"

echo ""
echo -e "${GREEN}=========================================="
echo "✓ Resource deletion initiated"
echo -e "==========================================${NC}"
echo ""
echo "Note: Namespace deletion is asynchronous and may take a few minutes to complete."
echo ""
echo "Monitor deletion progress with:"
echo "  kubectl get namespace $NAMESPACE"
echo ""
echo "Or watch in real-time:"
echo "  watch 'kubectl get namespace $NAMESPACE'"
echo ""

# Made with Bob
