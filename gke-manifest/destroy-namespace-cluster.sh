#!/bin/bash

# Destroy a specific OpenSearch namespace cluster.
# By default, scales down StatefulSets and preserves PVCs.
# Usage: ./destroy-namespace-cluster.sh <namespace> [--force] [--delete-pvcs] [--delete-all]
#
# Options:
#   --force        Skip confirmation prompt
#   --delete-pvcs  Delete PVCs (but keep StatefulSets scaled to 0)
#   --delete-all   Delete everything including StatefulSets and PVCs

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
    echo "Usage: $0 <namespace> [--force] [--delete-pvcs] [--delete-all]"
    echo "Example: $0 os-jvector"
    echo ""
    echo "Supported namespaces:"
    echo "  - os-jvector"
    echo "  - os-faiss"
    echo "  - os-lucene"
    echo ""
    echo "Options:"
    echo "  --force        Skip confirmation prompt"
    echo "  --delete-pvcs  Delete PVCs (but keep StatefulSets scaled to 0)"
    echo "  --delete-all   Delete everything including StatefulSets and PVCs"
    exit 1
fi

NAMESPACE=$1
FORCE_FLAG=""
DELETE_PVCS=false
DELETE_ALL=false

for arg in "${@:2}"; do
    case "$arg" in
        --force)
            FORCE_FLAG="--force"
            ;;
        --delete-pvcs)
            DELETE_PVCS=true
            ;;
        --delete-all)
            DELETE_ALL=true
            DELETE_PVCS=true  # --delete-all implies --delete-pvcs
            ;;
        *)
            echo -e "${RED}Error: Unknown option '$arg'${NC}"
            echo "Usage: $0 <namespace> [--force] [--delete-pvcs] [--delete-all]"
            exit 1
            ;;
    esac
done

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
if [[ "$DELETE_PVCS" == true ]]; then
    echo -e "${YELLOW}All data, configurations, resources, and PVCs will be permanently deleted.${NC}"
else
    echo -e "${YELLOW}Cluster resources will be deleted, but PVCs will be preserved by default.${NC}"
fi
echo ""

# Check for --force flag
if [[ "$FORCE_FLAG" != "--force" ]]; then
    read -p "Are you sure you want to continue? (type 'yes' to confirm): " confirmation
    if [[ "$confirmation" != "yes" ]]; then
        echo -e "${GREEN}Operation cancelled.${NC}"
        exit 0
    fi
fi

PRESERVED_PVCS=$(kubectl get pvc -n "$NAMESPACE" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)

echo ""
echo -e "${RED}Starting resource deletion...${NC}"
echo ""

echo -e "${YELLOW}Deleting resources in namespace: $NAMESPACE${NC}"

if [[ "$TYPE" == "jvector" ]]; then
    # Delete JVector-specific resources (StatefulSets, Services, ConfigMaps - but not PVCs)
    echo "Deleting data nodes..."
    kubectl delete statefulset opensearch-data -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete service opensearch-data opensearch-cluster -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete configmap opensearch-data-config -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    
    echo "Deleting cluster manager..."
    kubectl delete statefulset opensearch-cluster-manager -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete service opensearch-cluster-manager -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete configmap opensearch-cluster-manager-config -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
else
    # Delete standard resources (StatefulSets, Services, ConfigMaps - but not PVCs)
    echo "Deleting data nodes..."
    kubectl delete statefulset opensearch-data -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete service opensearch-data opensearch-cluster -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete configmap opensearch-data-config -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    
    echo "Deleting cluster manager..."
    kubectl delete statefulset opensearch-cluster-manager -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete service opensearch-cluster-manager -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
    kubectl delete configmap opensearch-cluster-manager-config -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
fi

# Delete benchmark client resources (StatefulSet and Service - but not PVCs)
echo "Deleting benchmark client..."
kubectl delete statefulset opensearch-benchmark-client -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true
kubectl delete service opensearch-benchmark-client -n "$NAMESPACE" --ignore-not-found=true 2>/dev/null || true

echo -e "  ${GREEN}✓ Resources deleted from $NAMESPACE${NC}"
echo ""

if [[ "$DELETE_PVCS" == true ]]; then
    echo -e "${YELLOW}Deleting namespace and PVCs...${NC}"
    kubectl delete namespace "$NAMESPACE" --ignore-not-found=true
    echo "  ✓ Namespace $NAMESPACE deletion initiated"
else
    echo -e "${YELLOW}Preserving PVCs in namespace: $NAMESPACE${NC}"
    if [[ -n "$PRESERVED_PVCS" ]]; then
        echo "  ✓ Preserved PVCs:"
        while IFS= read -r pvc; do
            if [[ -n "$pvc" ]]; then
                echo "    - $pvc"
            fi
        done <<< "$PRESERVED_PVCS"
    else
        echo "  ℹ️  No PVCs found to preserve in $NAMESPACE"
    fi
    echo ""
    echo -e "${YELLOW}Namespace not deleted because deleting the namespace would also delete preserved PVCs.${NC}"
    echo "  ℹ️  Remaining resources in $NAMESPACE can be inspected with:"
    echo "     kubectl get all -n $NAMESPACE"
fi

echo ""
echo -e "${GREEN}=========================================="
if [[ "$DELETE_PVCS" == true ]]; then
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
else
    echo "✓ Resource deletion complete (PVCs preserved)"
    echo -e "==========================================${NC}"
    echo ""
    echo "Preserved PVCs remain available in namespace: $NAMESPACE"
    echo "List them again with:"
    echo "  kubectl get pvc -n $NAMESPACE"
fi
echo ""

# Made with Bob
