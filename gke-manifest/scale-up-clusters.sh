#!/bin/bash

# Scale up OpenSearch clusters from scaled-down state
# This script restores StatefulSet replicas to their original counts
# Usage: ./scale-up-clusters.sh [namespace]
# Example: ./scale-up-clusters.sh os-jvector
#          ./scale-up-clusters.sh all

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
    
    # Check if there are any StatefulSets
    local statefulsets=$(kubectl get statefulset -n $ns -o name 2>/dev/null | wc -l)
    if [ "$statefulsets" -eq 0 ]; then
        echo -e "${YELLOW}⚠️  No StatefulSets found in $ns, skipping...${NC}"
        return
    fi
    
    # Get current replica counts
    echo "Current StatefulSet status:"
    kubectl get statefulset -n $ns -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas 2>/dev/null || echo "  No StatefulSets found"
    
    echo ""
    echo "Scaling up StatefulSets to operational replicas..."
    
    # Scale up cluster manager first (needs to be ready before data nodes)
    if kubectl get statefulset opensearch-cluster-manager -n $ns &> /dev/null; then
        echo "  📈 Scaling up opensearch-cluster-manager to 1 replica..."
        kubectl scale statefulset opensearch-cluster-manager --replicas=1 -n $ns
        
        echo "  ⏳ Waiting for cluster manager to be ready..."
        kubectl wait --for=condition=ready pod -l app=opensearch-cluster-manager -n $ns --timeout=300s || {
            echo -e "${YELLOW}⚠️  Cluster manager took longer than expected to start${NC}"
        }
    fi
    
    # Scale up data nodes
    if kubectl get statefulset opensearch-data -n $ns &> /dev/null; then
        echo "  📈 Scaling up opensearch-data to 3 replicas..."
        kubectl scale statefulset opensearch-data --replicas=3 -n $ns
        
        echo "  ⏳ Waiting for data nodes to be ready..."
        kubectl wait --for=condition=ready pod -l app=opensearch-data -n $ns --timeout=300s || {
            echo -e "${YELLOW}⚠️  Data nodes took longer than expected to start${NC}"
        }
    fi
    
    # Scale up benchmark client
    if kubectl get statefulset opensearch-benchmark-client -n $ns &> /dev/null; then
        echo "  📈 Scaling up opensearch-benchmark-client to 1 replica..."
        kubectl scale statefulset opensearch-benchmark-client --replicas=1 -n $ns
        
        echo "  ⏳ Waiting for benchmark client to be ready..."
        kubectl wait --for=condition=ready pod -l app=opensearch-benchmark-client -n $ns --timeout=180s || {
            echo -e "${YELLOW}⚠️  Benchmark client took longer than expected to start${NC}"
        }
    fi
    
    # Show final status
    echo ""
    echo "Final StatefulSet status:"
    kubectl get statefulset -n $ns -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,READY:.status.readyReplicas
    
    echo ""
    echo "Pod status:"
    kubectl get pods -n $ns
    
    # Check cluster health
    echo ""
    echo "Checking cluster health..."
    sleep 5  # Give cluster a moment to stabilize
    
    if kubectl get pod opensearch-data-0 -n $ns &> /dev/null; then
        echo "Cluster health:"
        kubectl exec -n $ns opensearch-data-0 -- curl -k -u admin:admin -s https://localhost:9200/_cluster/health?pretty 2>/dev/null || {
            echo -e "${YELLOW}⚠️  Could not retrieve cluster health (cluster may still be initializing)${NC}"
        }
    fi
    
    echo -e "${GREEN}✅ Namespace $ns scaled up successfully${NC}"
}

# Main logic
if [ -z "$NAMESPACE" ]; then
    echo "Usage: $0 <namespace|all>"
    echo ""
    echo "Available options:"
    echo "  os-jvector  - Scale up JVector cluster"
    echo "  os-faiss    - Scale up FAISS cluster"
    echo "  os-lucene   - Scale up Lucene cluster"
    echo "  all         - Scale up all clusters"
    echo ""
    exit 1
fi

if [ "$NAMESPACE" == "all" ]; then
    echo "Scaling up all OpenSearch clusters..."
    echo ""
    
    for ns in os-jvector os-faiss os-lucene; do
        scale_up_namespace $ns
    done
    
else
    # Validate namespace
    if [[ ! "$NAMESPACE" =~ ^(os-jvector|os-faiss|os-lucene)$ ]]; then
        echo -e "${RED}❌ Error: Invalid namespace. Must be one of: os-jvector, os-faiss, os-lucene, all${NC}"
        exit 1
    fi
    
    scale_up_namespace $NAMESPACE
fi

echo ""
echo "=========================================="
echo -e "${GREEN}Scale Up Complete${NC}"
echo "=========================================="
echo ""
echo "💡 Monitor cluster status:"
echo "   kubectl get pods -n $NAMESPACE -w"
echo ""
echo "💡 Check cluster health:"
echo "   kubectl exec -n $NAMESPACE opensearch-data-0 -- curl -k -u admin:admin https://localhost:9200/_cluster/health?pretty"
echo ""
echo "💡 Access benchmark client:"
echo "   kubectl exec -it -n $NAMESPACE opensearch-benchmark-client-0 -- bash"
echo ""
echo "💡 To scale back down when done:"
echo "   ./scale-down-clusters.sh $NAMESPACE"
echo ""

# Made with Bob