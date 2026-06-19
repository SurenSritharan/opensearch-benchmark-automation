#!/bin/bash

# Deploy all OpenSearch clusters (os-jvector, os-faiss, os-lucene)
# Usage: ./deploy-all-clusters.sh [--version VERSION] [--delete-pvcs]
# Example: ./deploy-all-clusters.sh --version 3.7.0

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Parse arguments
OPENSEARCH_VERSION="3.6.0"  # Default version
EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            OPENSEARCH_VERSION="$2"
            EXTRA_ARGS="$EXTRA_ARGS --version $2"
            shift 2
            ;;
        --delete-pvcs)
            EXTRA_ARGS="$EXTRA_ARGS --delete-pvcs"
            shift
            ;;
        --force)
            EXTRA_ARGS="$EXTRA_ARGS --force"
            shift
            ;;
        *)
            echo "Error: Unknown option '$1'"
            echo "Usage: $0 [--version VERSION] [--delete-pvcs] [--force]"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "Deploying All OpenSearch Clusters"
echo "=========================================="
echo "OpenSearch Version: $OPENSEARCH_VERSION"
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
    
    # Run deployment in background with version and extra args
    (
        "$SCRIPT_DIR/deploy-namespace-cluster.sh" "$ns" $EXTRA_ARGS 2>&1 | sed "s/^/[$ns] /"
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
