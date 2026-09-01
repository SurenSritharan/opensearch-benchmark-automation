#!/bin/bash

# Scale down OpenSearch clusters to save resources.
# This script reduces StatefulSet replicas to 0 while preserving data in PVCs.
#
# Namespace convention:  os-<engine>          (prod)
#                        os-develop-<engine>  (develop / feature pipelines)
#
# Usage: ./scale-down-clusters.sh <namespace|all>
# Example: ./scale-down-clusters.sh os-jvector
#          ./scale-down-clusters.sh os-develop-jvector
#          ./scale-down-clusters.sh os-acl-jvector
#          ./scale-down-clusters.sh all   ← scales down every os-* namespace found in GKE

set -e

NAMESPACE=$1

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "=========================================="
echo "OpenSearch Cluster Scale Down"
echo "=========================================="

# Function to scale down a namespace
scale_down_namespace() {
    local ns=$1
    
    echo ""
    echo -e "${BLUE}Scaling down namespace: $ns${NC}"
    echo "----------------------------------------"
    
    # Check if namespace exists
    if ! kubectl get namespace $ns &> /dev/null; then
        echo -e "${YELLOW}⚠️  Namespace $ns does not exist, skipping...${NC}"
        return
    fi
    
    # Check if there are any StatefulSets
    local statefulsets=$(kubectl get statefulset -n $ns -o name 2>/dev/null | wc -l)
    if [ "$statefulsets" -eq 0 ]; then
        echo -e "${YELLOW}⚠️  No StatefulSets found in $ns, skipping...${NC}"
        return
    fi
    
    # Get current replica counts before scaling down
    echo "Current StatefulSet status:"
    kubectl get statefulset -n $ns -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas 2>/dev/null || echo "  No StatefulSets found"
    
    echo ""
    echo "Scaling down StatefulSets to 0 replicas..."
    
    # Scale down data nodes first (graceful shutdown)
    if kubectl get statefulset opensearch-data -n $ns &> /dev/null; then
        echo "  📉 Scaling down opensearch-data..."
        kubectl scale statefulset opensearch-data --replicas=0 -n $ns
    fi
    
    # Scale down cluster manager
    if kubectl get statefulset opensearch-cluster-manager -n $ns &> /dev/null; then
        echo "  📉 Scaling down opensearch-cluster-manager..."
        kubectl scale statefulset opensearch-cluster-manager --replicas=0 -n $ns
    fi
    
    # Wait for pods to terminate
    echo ""
    echo "Waiting for pods to terminate..."
    local timeout=120
    local elapsed=0
    while [ $elapsed -lt $timeout ]; do
        local pod_count=$(kubectl get pods -n $ns --no-headers 2>/dev/null | wc -l)
        if [ "$pod_count" -eq 0 ]; then
            echo -e "${GREEN}✅ All pods terminated in $ns${NC}"
            break
        fi
        echo "  Waiting... ($pod_count pods remaining)"
        sleep 5
        elapsed=$((elapsed + 5))
    done
    
    if [ $elapsed -ge $timeout ]; then
        echo -e "${YELLOW}⚠️  Timeout waiting for pods to terminate. Some pods may still be running.${NC}"
    fi
    
    # Show PVC status (data is preserved)
    echo ""
    echo "Persistent Volume Claims (data preserved):"
    kubectl get pvc -n $ns 2>/dev/null || echo "  No PVCs found"
    
    echo -e "${GREEN}✅ Namespace $ns scaled down successfully${NC}"
}

# Main logic
if [ -z "$NAMESPACE" ]; then
    echo "Usage: $0 <namespace|all>"
    echo ""
    echo "  <namespace>  Any os-<engine> or os-develop-<engine> namespace, e.g.:"
    echo "                 os-jvector, os-faiss, os-lucene"
    echo "                 os-acl-jvector, os-develop-jvector"
    echo "  all          Scale down every os-* namespace currently present in GKE"
    echo "               (do NOT use while a pipeline is running)"
    echo ""
    exit 1
fi

if [ "$NAMESPACE" == "all" ]; then
    echo -e "${YELLOW}⚠️  WARNING: scaling down ALL os-* namespaces found in GKE.${NC}"
    echo -e "${YELLOW}   Do not use 'all' while a benchmark pipeline job is running — it will${NC}"
    echo -e "${YELLOW}   tear down clusters that are actively in use by the other job.${NC}"
    echo ""

    # Discover every namespace matching os-* dynamically — no hardcoded list.
    while IFS= read -r ns; do
        scale_down_namespace "$ns"
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

    scale_down_namespace "$NAMESPACE"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}Scale Down Complete${NC}"
echo "=========================================="
echo ""
echo "💡 To scale back up, use:"
echo "   ./scale-up-clusters.sh $NAMESPACE"
echo ""
echo "💡 To completely remove resources, use:"
echo "   ./destroy-namespace-cluster.sh $NAMESPACE"
echo ""
echo "📊 Resource savings:"
echo "   - All pods stopped (0 CPU/memory usage)"
echo "   - Data preserved in PVCs"
echo "   - Quick restart possible with scale-up"
echo ""

# Made with Bob