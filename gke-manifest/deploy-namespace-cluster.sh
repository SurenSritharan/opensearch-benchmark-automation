#!/bin/bash

# Deploy separated cluster architecture to a specific namespace
# Usage: ./deploy-namespace-cluster.sh <namespace>
# Example: ./deploy-namespace-cluster.sh os-faiss

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAMESPACE=$1

if [ -z "$NAMESPACE" ]; then
    echo "Usage: $0 <namespace>"
    echo "Available namespaces: os-jvector, os-faiss, os-lucene"
    exit 1
fi

# Validate namespace
if [[ ! "$NAMESPACE" =~ ^(os-jvector|os-faiss|os-lucene)$ ]]; then
    echo "Error: Invalid namespace. Must be one of: os-jvector, os-faiss, os-lucene"
    exit 1
fi

echo "=========================================="
echo "Deploying OpenSearch cluster to namespace: $NAMESPACE"
echo "=========================================="

# Create namespace if it doesn't exist
echo "Creating namespace $NAMESPACE..."
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Clean up existing resources for fresh deployment
echo ""
echo "Cleaning up existing resources in $NAMESPACE..."

# Delete StatefulSets first (to trigger graceful pod shutdown)
kubectl delete statefulset --all -n $NAMESPACE --ignore-not-found=true --wait=false

# Delete pods
kubectl delete pod --all -n $NAMESPACE --ignore-not-found=true --wait=false

# Delete services
kubectl delete service --all -n $NAMESPACE --ignore-not-found=true --wait=false

# Delete configmaps (except kube-root-ca.crt)
kubectl delete configmap -n $NAMESPACE --field-selector metadata.name!=kube-root-ca.crt --ignore-not-found=true --wait=false

# Delete PVCs
kubectl delete pvc --all -n $NAMESPACE --ignore-not-found=true --wait=false

echo "Waiting for resources to be deleted..."
sleep 5

echo "Cleanup complete. Proceeding with fresh deployment..."
echo ""

# Create shared certificates secret if it doesn't exist
echo "Checking for certificates in $NAMESPACE namespace..."
if ! kubectl get secret opensearch-shared-certs -n $NAMESPACE &> /dev/null; then
    echo "Generating new certificates for $NAMESPACE namespace..."
    
    # Run generate-certs.sh from the same directory
    if [ -f "$SCRIPT_DIR/generate-certs.sh" ]; then
        "$SCRIPT_DIR/generate-certs.sh"
        
        # Verify certificate files were created
        CERT_DIR="$SCRIPT_DIR/certs"
        if [ ! -f "$CERT_DIR/root-ca.pem" ] || [ ! -f "$CERT_DIR/esnode.pem" ] || [ ! -f "$CERT_DIR/admin.pem" ]; then
            echo "❌ ERROR: Certificate files not found after generation"
            echo "   Expected files in: $CERT_DIR"
            echo "   - root-ca.pem, root-ca-key.pem"
            echo "   - esnode.pem, esnode-key.pem"
            echo "   - admin.pem, admin-key.pem"
            exit 1
        fi
        
        # Create Kubernetes secret in the target namespace
        echo "Creating Kubernetes secret in $NAMESPACE namespace..."
        kubectl create secret generic opensearch-shared-certs \
            --from-file="$CERT_DIR/root-ca.pem" \
            --from-file="$CERT_DIR/root-ca-key.pem" \
            --from-file="$CERT_DIR/esnode.pem" \
            --from-file="$CERT_DIR/esnode-key.pem" \
            --from-file="$CERT_DIR/admin.pem" \
            --from-file="$CERT_DIR/admin-key.pem" \
            -n $NAMESPACE \
            --dry-run=client -o yaml | kubectl apply -f -
        
        # Verify secret was created
        if ! kubectl get secret opensearch-shared-certs -n $NAMESPACE &> /dev/null; then
            echo "❌ ERROR: Failed to create certificates secret in $NAMESPACE namespace"
            exit 1
        fi
        echo "✅ Certificates secret created successfully in $NAMESPACE namespace"
    else
        echo "❌ ERROR: generate-certs.sh not found at $SCRIPT_DIR/generate-certs.sh"
        echo "   Cannot proceed without certificates"
        exit 1
    fi
    
    # Final verification: Ensure secret exists in target namespace
    echo "Verifying certificates in $NAMESPACE namespace..."
    if ! kubectl get secret opensearch-shared-certs -n $NAMESPACE &> /dev/null; then
        echo "❌ ERROR: Certificate secret not found in $NAMESPACE namespace"
        echo "   Deployment cannot proceed without certificates"
        exit 1
    fi
    echo "✅ Certificates verified in $NAMESPACE namespace"
else
    echo "✅ Shared certificates already exist in $NAMESPACE"
fi

# Deploy based on namespace type
if [ "$NAMESPACE" == "os-jvector" ]; then
    echo ""
    echo "Deploying JVector cluster (with custom plugin)..."
    echo "1. Deploying cluster manager..."
    kubectl apply -f "$SCRIPT_DIR/opensearch-jvector-cluster-manager.yaml"
    
    echo "2. Waiting for cluster manager to be ready..."
    kubectl wait --for=condition=ready pod -l app=opensearch-cluster-manager -n $NAMESPACE --timeout=300s || true
    
    echo "3. Deploying data nodes..."
    kubectl apply -f "$SCRIPT_DIR/opensearch-jvector-data-nodes.yaml"
    
else
    # For os-faiss and os-lucene, use the standard manifests
    echo ""
    echo "Deploying standard OpenSearch cluster (FAISS/Lucene)..."
    
    # Use kubectl -n flag instead of sed substitution (cleaner approach)
    echo "1. Deploying cluster manager..."
    sed "s/\${NAMESPACE}/$NAMESPACE/g" "$SCRIPT_DIR/opensearch-standard-cluster-manager.yaml" | kubectl apply -n $NAMESPACE -f -
    
    echo "2. Waiting for cluster manager to be ready..."
    kubectl wait --for=condition=ready pod -l app=opensearch-cluster-manager -n $NAMESPACE --timeout=300s || true
    
    echo "3. Deploying data nodes..."
    sed "s/\${NAMESPACE}/$NAMESPACE/g" "$SCRIPT_DIR/opensearch-standard-data-nodes.yaml" | kubectl apply -n $NAMESPACE -f -
fi

# Deploy benchmark client
echo ""
echo "4. Deploying benchmark client..."
if kubectl get pod opensearch-benchmark-client -n $NAMESPACE &> /dev/null; then
    echo "Benchmark client already exists in $NAMESPACE, skipping..."
else
    kubectl apply -f "$SCRIPT_DIR/opensearch-benchmark-client.yaml" -n $NAMESPACE
    echo "Benchmark client deployed to $NAMESPACE"
fi

echo ""
echo "=========================================="
echo "Deployment initiated for $NAMESPACE"
echo "=========================================="
echo ""
echo "Monitor deployment status:"
echo "  kubectl get pods -n $NAMESPACE -w"
echo ""
echo "Check cluster health:"
echo "  kubectl exec -n $NAMESPACE opensearch-data-0 -- curl -k -u admin:admin https://localhost:9200/_cluster/health?pretty"
echo ""
echo "Access benchmark client:"
echo "  kubectl exec -it -n $NAMESPACE opensearch-benchmark-client -- bash"
echo ""
echo "View logs:"
echo "  kubectl logs -n $NAMESPACE -l app=opensearch-cluster-manager -f"
echo "  kubectl logs -n $NAMESPACE -l app=opensearch-data -f"
echo "  kubectl logs -n $NAMESPACE opensearch-benchmark-client -f"
echo ""

# Made with Bob
