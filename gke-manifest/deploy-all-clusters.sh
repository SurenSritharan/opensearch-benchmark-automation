#!/bin/bash

# Deploy all OpenSearch clusters (os-jvector, os-faiss, os-lucene)
# Usage: ./deploy-all-clusters.sh

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "Deploying All OpenSearch Clusters"
echo "=========================================="
echo ""
echo "This will deploy clusters to:"
echo "  - os-jvector (JVector plugin)"
echo "  - os-faiss (FAISS engine)"
echo "  - os-lucene (Lucene engine)"
echo ""

# Deploy each namespace in parallel
NAMESPACES=("os-jvector" "os-faiss" "os-lucene")

# Array to store background process IDs
declare -a PIDS

for ns in "${NAMESPACES[@]}"; do
    echo ""
    echo "=========================================="
    echo "Starting deployment for $ns"
    echo "=========================================="
    
    # Run deployment in background
    (
        "$SCRIPT_DIR/deploy-namespace-cluster.sh" "$ns" 2>&1 | sed "s/^/[$ns] /"
        echo "[$ns] ✓ Deployment completed"
    ) &
    
    # Store the process ID
    PIDS+=($!)
    
    # Small stagger to avoid race conditions on shared resources
    sleep 2
done

echo ""
echo "=========================================="
echo "All deployments started in parallel"
echo "Waiting for all deployments to complete..."
echo "=========================================="
echo ""

# Wait for all background processes to complete
for pid in "${PIDS[@]}"; do
    wait "$pid"
done

echo ""
echo "=========================================="
echo "All Deployments Completed"
echo "=========================================="
# Made with Bob
