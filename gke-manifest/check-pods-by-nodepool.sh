#!/bin/bash

# Show pods grouped by node pool
# Usage: ./check-pods-by-nodepool.sh

echo "=========================================="
echo "Pods by Node Pool"
echo "=========================================="
echo ""

# Get all pods with their node assignments
echo "Fetching pod assignments..."
echo ""

# Get unique node pools
NODE_POOLS=$(kubectl get nodes -o jsonpath='{.items[*].metadata.labels.cloud\.google\.com/gke-nodepool}' | tr ' ' '\n' | sort -u)

for pool in $NODE_POOLS; do
    echo "=========================================="
    echo "NODE POOL: $pool"
    echo "=========================================="
    
    # Get nodes in this pool
    NODES=$(kubectl get nodes -l cloud.google.com/gke-nodepool=$pool -o jsonpath='{.items[*].metadata.name}')
    
    if [ -z "$NODES" ]; then
        echo "No nodes found in this pool"
        echo ""
        continue
    fi
    
    echo "Nodes: $NODES"
    echo ""
    
    # For each node in this pool, show pods
    for node in $NODES; do
        echo "--- Node: $node ---"
        kubectl get pods --all-namespaces --field-selector spec.nodeName=$node \
            -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,STATUS:.status.phase,NODE:.spec.nodeName \
            --no-headers 2>/dev/null | grep -v "kube-system" || echo "  (no user pods)"
        echo ""
    done
    
    # Summary for this pool
    TOTAL_PODS=$(kubectl get pods --all-namespaces --field-selector spec.nodeName=$(echo $NODES | tr ' ' ',') -o json 2>/dev/null | jq '[.items[] | select(.metadata.namespace != "kube-system")] | length')
    echo "Total user pods on $pool: $TOTAL_PODS"
    echo ""
done

echo "=========================================="
echo "Summary by Namespace and Node Pool"
echo "=========================================="
echo ""

# Show OpenSearch namespaces
for ns in os-jvector os-faiss os-lucene; do
    echo "Namespace: $ns"
    kubectl get pods -n $ns -o wide 2>/dev/null | awk 'NR==1 || NR>1 {print}' | while read line; do
        if [[ "$line" == NAME* ]]; then
            echo "  $line"
        else
            POD_NAME=$(echo "$line" | awk '{print $1}')
            NODE=$(echo "$line" | awk '{print $7}')
            if [ ! -z "$NODE" ] && [ "$NODE" != "<none>" ]; then
                NODE_POOL=$(kubectl get node "$NODE" -o jsonpath='{.metadata.labels.cloud\.google\.com/gke-nodepool}' 2>/dev/null)
                echo "  $line [pool: $NODE_POOL]"
            else
                echo "  $line"
            fi
        fi
    done
    echo ""
done

echo "=========================================="
echo "Node Pool Resource Usage"
echo "=========================================="
echo ""

for pool in $NODE_POOLS; do
    echo "--- $pool ---"
    kubectl top nodes -l cloud.google.com/gke-nodepool=$pool 2>/dev/null || echo "  (metrics not available)"
    echo ""
done

# Made with Bob
