#!/bin/bash

# Deploy separated cluster architecture to a specific namespace.
#
# Namespace convention:
#   os-<engine>              prod standard   (e.g. os-jvector, os-faiss, os-lucene)
#   os-develop-<engine>      develop         (e.g. os-develop-jvector)
#   os-<engine>-<pipeline>   prod isolated   (e.g. os-jvector-acl)
#
# The engine slug drives manifest selection:
#   contains "jvector"  → opensearch-jvector-cluster-manager.yaml + opensearch-jvector-data-nodes.yaml
#   anything else       → opensearch-standard-cluster-manager.yaml + opensearch-standard-data-nodes.yaml
#
# Adding a new pipeline (e.g. ACL):
#   1. Pick a namespace:  os-jvector-acl  (prod)
#   2. kubectl create namespace os-jvector-acl   (cluster-admin one-time)
#   3. Add a RoleBinding in jenkins-agent-rbac.yaml for that namespace
#   4. Run this script — no other changes needed
#
# Usage: ./deploy-namespace-cluster.sh <namespace> [--version VERSION] [--node-size small|medium|large] [--force] [--delete-pvcs]
# Example: ./deploy-namespace-cluster.sh os-jvector-acl
# Example: ./deploy-namespace-cluster.sh os-jvector-acl --version 3.7.0
# Example: ./deploy-namespace-cluster.sh os-jvector-acl --delete-pvcs

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAMESPACE=$1
OPENSEARCH_VERSION="3.7.0"  # Default version
FORCE_FLAG=""
DELETE_PVCS=false
NODE_SIZE="small"

if [ -z "$NAMESPACE" ]; then
    echo "Usage: $0 <namespace> [--version VERSION] [--node-size small|medium|large] [--force] [--delete-pvcs]"
    echo ""
    echo "Namespace format:  os-<engine>  or  os-develop-<engine>"
    echo "  <engine> is any slug, e.g. jvector, faiss, lucene, acl-jvector"
    echo "  The namespace must already exist in GKE (kubectl create namespace ...)."
    echo ""
    echo "Options:"
    echo "  --version VER              OpenSearch version to deploy (default: 3.7.0)"
    echo "  --node-size small|medium|large  Data node resource profile (default: small)"
    echo "  --force                    Skip confirmation prompts"
    echo "  --delete-pvcs              Delete PVCs (WARNING: destroys all indexed data and results)"
    exit 1
fi

# Parse additional arguments
shift  # Remove namespace from arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)
            OPENSEARCH_VERSION="$2"
            shift 2
            ;;
        --node-size)
            NODE_SIZE="$2"
            shift 2
            ;;
        --force)
            FORCE_FLAG="--force"
            shift
            ;;
        --delete-pvcs)
            DELETE_PVCS=true
            shift
            ;;
        *)
            echo "Error: Unknown option '$1'"
            echo "Usage: $0 <namespace> [--version VERSION] [--node-size small|medium|large] [--force] [--delete-pvcs]"
            exit 1
            ;;
    esac
done

# ── Node size table ─────────────────────────────────────────────────────────────
# Each size doubles the previous: small = current baseline, medium = 2x, large = 4x
# JVM heap is always ~half of RAM (standard OpenSearch recommendation).
case "$NODE_SIZE" in
    small)
        NODE_CPU_REQ="7000m";  NODE_CPU_LIM="8000m";  NODE_MEM="27Gi";  NODE_HEAP="13g" ;;
    medium)
        NODE_CPU_REQ="14000m"; NODE_CPU_LIM="16000m"; NODE_MEM="54Gi";  NODE_HEAP="26g" ;;
    large)
        NODE_CPU_REQ="28000m"; NODE_CPU_LIM="32000m"; NODE_MEM="108Gi"; NODE_HEAP="52g" ;;
    *)
        echo "Error: --node-size must be small, medium, or large (got: $NODE_SIZE)"
        exit 1
        ;;
esac

# Validate namespace pattern: os-<engine>  or  os-develop-<engine>
# <engine> is any non-empty slug (letters, digits, hyphens) — no hardcoded engine list.
# Existence in GKE is the real gate (checked below); the pattern just guards against typos.
if [[ ! "$NAMESPACE" =~ ^os(-develop)?-[a-z][a-z0-9-]+$ ]]; then
    echo "Error: Invalid namespace '$NAMESPACE'."
    echo "       Expected format: os-<engine>  or  os-develop-<engine>"
    echo "       where <engine> is a slug like jvector, faiss, lucene, acl-jvector"
    exit 1
fi

echo "=========================================="
echo "Deploying OpenSearch cluster"
echo "=========================================="
echo "Namespace:         $NAMESPACE"
echo "OpenSearch Version: $OPENSEARCH_VERSION"
echo "Node Size:         $NODE_SIZE  (cpu: $NODE_CPU_REQ/$NODE_CPU_LIM  mem: $NODE_MEM  heap: $NODE_HEAP)"
echo "=========================================="

# Namespaces are pre-created by jenkins-agent-rbac.yaml (requires cluster-admin).
# The Jenkins SA does not have permission to create namespaces — fail fast if missing.
echo "Ensuring namespace $NAMESPACE exists..."
if ! kubectl get namespace $NAMESPACE &>/dev/null; then
    echo "ERROR: namespace '$NAMESPACE' does not exist."
    echo "       Apply gke-manifest/jenkins-agent-rbac.yaml with a cluster-admin account first."
    exit 1
fi

# Clean up existing resources for fresh deployment
echo ""
echo "Cleaning up existing resources in $NAMESPACE..."

# Delete StatefulSets and Pods in the background to trigger graceful shutdown
echo "Initiating deletion of StatefulSets and Pods..."
kubectl delete statefulset --all -n $NAMESPACE --ignore-not-found=true --wait=false
kubectl delete pod --all -n $NAMESPACE --ignore-not-found=true --wait=false

# Explicitly wait for pods to terminate to release PVC locks (finalizers)
echo "Waiting for pods to terminate..."
kubectl wait --for=delete pod --all -n $NAMESPACE --timeout=120s 2>/dev/null || true

# Delete services
kubectl delete service --all -n $NAMESPACE --ignore-not-found=true --wait=false

# Delete configmaps (except kube-root-ca.crt)
kubectl delete configmap -n $NAMESPACE --field-selector metadata.name!=kube-root-ca.crt --ignore-not-found=true --wait=false

# Handle PVCs based on --delete-pvcs flag
if [[ "$DELETE_PVCS" == true ]]; then
    echo "⚠️  Deleting PVCs (all indexed data and results will be lost)..."
    # This blocks natively until the PVCs are completely deleted from the cluster
    kubectl delete pvc --all -n $NAMESPACE --ignore-not-found=true
    echo "   ✅ PVCs successfully deleted"
else
    echo "💾 Preserving PVCs to retain data (indexed vectors, benchmark results, etc.)"
    EXISTING_PVCS=$(kubectl get pvc -n $NAMESPACE -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
    if [ -n "$EXISTING_PVCS" ]; then
        echo "   Preserved PVCs:"
        while IFS= read -r pvc; do
            if [ -n "$pvc" ]; then
                echo "   - $pvc"
            fi
        done <<< "$EXISTING_PVCS"
    else
        echo "   No existing PVCs found"
    fi
fi

echo "Waiting for remaining resources to settle..."
sleep 5

echo "Cleanup complete. Proceeding with deployment..."
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

# Deploy based on namespace type — any namespace containing "jvector" uses the
# JVector manifest (custom plugin); all others use the standard manifest.
if [[ "$NAMESPACE" =~ jvector ]]; then
    MANAGER_MANIFEST="$SCRIPT_DIR/opensearch-jvector-cluster-manager.yaml"
    DATA_MANIFEST="$SCRIPT_DIR/opensearch-jvector-data-nodes.yaml"
    echo ""
    echo "Deploying JVector cluster (with custom plugin)..."

    echo "1. Deploying cluster manager..."
    sed -e "s/\${NAMESPACE}/$NAMESPACE/g" \
        -e "s/\${OPENSEARCH_VERSION}/$OPENSEARCH_VERSION/g" \
        "$MANAGER_MANIFEST" | kubectl apply -n $NAMESPACE -f -

    echo "2. Waiting for cluster manager to be ready..."
    kubectl wait --for=condition=ready pod -l app=opensearch-cluster-manager -n $NAMESPACE --timeout=300s || true

    echo "3. Deploying data nodes..."
    sed -e "s/\${NAMESPACE}/$NAMESPACE/g" \
        -e "s/\${OPENSEARCH_VERSION}/$OPENSEARCH_VERSION/g" \
        -e "s/\${NODE_CPU_REQ}/$NODE_CPU_REQ/g" \
        -e "s/\${NODE_CPU_LIM}/$NODE_CPU_LIM/g" \
        -e "s/\${NODE_MEM}/$NODE_MEM/g" \
        -e "s/\${NODE_HEAP}/$NODE_HEAP/g" \
        "$DATA_MANIFEST" | kubectl apply -n $NAMESPACE -f -

else
    # For os-faiss and os-lucene, use the standard manifests
    echo ""
    echo "Deploying standard OpenSearch cluster (FAISS/Lucene)..."

    echo "1. Deploying cluster manager..."
    sed -e "s/\${NAMESPACE}/$NAMESPACE/g" \
        -e "s/\${OPENSEARCH_VERSION}/$OPENSEARCH_VERSION/g" \
        "$SCRIPT_DIR/opensearch-standard-cluster-manager.yaml" | kubectl apply -n $NAMESPACE -f -

    echo "2. Waiting for cluster manager to be ready..."
    kubectl wait --for=condition=ready pod -l app=opensearch-cluster-manager -n $NAMESPACE --timeout=300s || true

    echo "3. Deploying data nodes..."
    sed -e "s/\${NAMESPACE}/$NAMESPACE/g" \
        -e "s/\${OPENSEARCH_VERSION}/$OPENSEARCH_VERSION/g" \
        -e "s/\${NODE_CPU_REQ}/$NODE_CPU_REQ/g" \
        -e "s/\${NODE_CPU_LIM}/$NODE_CPU_LIM/g" \
        -e "s/\${NODE_MEM}/$NODE_MEM/g" \
        -e "s/\${NODE_HEAP}/$NODE_HEAP/g" \
        "$SCRIPT_DIR/opensearch-standard-data-nodes.yaml" | kubectl apply -n $NAMESPACE -f -
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
echo "View logs:"
echo "  kubectl logs -n $NAMESPACE -l app=opensearch-cluster-manager -f"
echo "  kubectl logs -n $NAMESPACE -l app=opensearch-data -f"
echo ""

# Made with Bob
