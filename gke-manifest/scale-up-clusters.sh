#!/bin/bash

# Scale up OpenSearch clusters from scaled-down state.
# This script restores StatefulSet replicas to their original counts.
#
# Namespace convention:  os-<engine>          (prod)
#                        os-develop-<engine>  (develop / feature pipelines)
#
# Usage: ./scale-up-clusters.sh <namespace|all>
# Example: ./scale-up-clusters.sh os-jvector
#          ./scale-up-clusters.sh os-develop-jvector
#          ./scale-up-clusters.sh os-acl-jvector
#          ./scale-up-clusters.sh all   ← scales every os-* namespace found in GKE

set -e

NAMESPACE=$1

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo "OpenSearch Cluster Scale Up"
echo "=========================================="

# Function to scale up a namespace
scale_up_namespace() {
    local ns=$1
    
    echo ""
    echo -e "${BLUE}Scaling up namespace: $ns${NC}"
    echo "----------------------------------------"
    
    # Check if namespace exists
    if ! kubectl get namespace $ns &> /dev/null; then
        echo -e "${YELLOW}⚠️  Namespace $ns does not exist, skipping...${NC}"
        return
    fi
    
    # If no StatefulSets exist, the cluster was never deployed — run initial deploy
    local statefulsets=$(kubectl get statefulset -n $ns -o name 2>/dev/null | wc -l)
    if [ "$statefulsets" -eq 0 ]; then
        echo -e "${YELLOW}⚠️  No StatefulSets found in $ns — running initial deploy...${NC}"
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        "$SCRIPT_DIR/deploy-namespace-cluster.sh" "$ns" --force
        return
    fi
    
    # Get current replica counts
    echo "Current StatefulSet status:"
    kubectl get statefulset -n $ns -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas 2>/dev/null || echo "  No StatefulSets found"
    
    echo ""
    echo "Scaling up StatefulSets to operational replicas..."
    
    # Scale up cluster manager
    if kubectl get statefulset opensearch-cluster-manager -n $ns &> /dev/null; then
        echo "  📈 Scaling up opensearch-cluster-manager to 1 replica..."
        kubectl scale statefulset opensearch-cluster-manager --replicas=1 -n $ns
    fi

    # Scale up data nodes
    if kubectl get statefulset opensearch-data -n $ns &> /dev/null; then
        echo "  📈 Scaling up opensearch-data to 3 replicas..."
        kubectl scale statefulset opensearch-data --replicas=3 -n $ns
    fi

    echo -e "${GREEN}✅ Scale commands issued for $ns${NC}"
}

# Main logic
if [ -z "$NAMESPACE" ]; then
    echo "Usage: $0 <namespace|all>"
    echo ""
    echo "  <namespace>  Any os-<engine> or os-develop-<engine> namespace, e.g.:"
    echo "                 os-jvector, os-faiss, os-lucene"
    echo "                 os-acl-jvector, os-develop-jvector"
    echo "  all          Scale up every os-* namespace currently present in GKE"
    echo "               (manual recovery only — pipelines scale up only what they need)"
    echo ""
    exit 1
fi

if [ "$NAMESPACE" == "all" ]; then
    echo -e "${YELLOW}⚠️  WARNING: scaling up ALL os-* namespaces found in GKE.${NC}"
    echo -e "${YELLOW}   Intended for manual recovery only — the pipeline scales up${NC}"
    echo -e "${YELLOW}   only the namespaces it needs, never all at once.${NC}"
    echo ""

    # Discover every namespace matching os-* dynamically — no hardcoded list.
    while IFS= read -r ns; do
        scale_up_namespace "$ns"
    done < <(kubectl get namespaces -o jsonpath='{.items[*].metadata.name}' \
                 | tr ' ' '\n' \
                 | grep -E '^os(-develop)?-[a-z][a-z0-9-]+$' \
                 | sort)

else
    # Validate pattern: os-<engine> or os-develop-<engine>
    if [[ ! "$NAMESPACE" =~ ^os(-develop)?-[a-z][a-z0-9-]+$ ]]; then
        echo -e "${RED}❌ Error: Invalid namespace '$NAMESPACE'."
        echo "   Expected format: os-<engine>  or  os-develop-<engine>"
        echo "   where <engine> is a slug like jvector, faiss, lucene, acl-jvector${NC}"
        exit 1
    fi

    scale_up_namespace "$NAMESPACE"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}Scale Up Complete${NC}"
echo "=========================================="
echo ""
echo "💡 Monitor cluster status:"
echo "   kubectl get pods -n ${NAMESPACE:-<namespace>} -w"
echo ""
echo "💡 Check cluster health:"
echo "   kubectl exec -n $NAMESPACE opensearch-data-0 -- curl -k -u admin:admin https://localhost:9200/_cluster/health?pretty"
echo ""
echo "💡 To scale back down when done:"
echo "   ./scale-down-clusters.sh $NAMESPACE"
echo ""

# Made with Bob