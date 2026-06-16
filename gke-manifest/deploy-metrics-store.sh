#!/bin/bash
set -e

NAMESPACE="os-metrics"

echo "=================================================="
echo "🚀 Deploying OpenSearch Metrics Store"
echo "=================================================="

# Check if namespace exists
if kubectl get namespace "$NAMESPACE" &> /dev/null; then
    echo "✅ Namespace $NAMESPACE already exists"
else
    echo "📦 Creating namespace $NAMESPACE..."
    kubectl create namespace "$NAMESPACE"
fi

# Check if certs secret exists in the namespace
if kubectl get secret opensearch-shared-certs -n "$NAMESPACE" &> /dev/null; then
    echo "✅ Certificates already exist in $NAMESPACE"
else
    echo "🔐 Copying certificates to $NAMESPACE namespace..."
    # Copy from os-jvector namespace (or any other namespace that has it)
    if kubectl get secret opensearch-shared-certs -n os-jvector &> /dev/null; then
        kubectl get secret opensearch-shared-certs -n os-jvector -o yaml | \
            sed "s/namespace: os-jvector/namespace: $NAMESPACE/" | \
            kubectl apply -f -
    else
        echo "⚠️  Warning: opensearch-shared-certs not found in os-jvector namespace"
        echo "   Please run ./generate-certs.sh first and ensure certs are available"
        exit 1
    fi
fi

# Apply the metrics store manifest
echo "📋 Applying metrics store configuration..."
kubectl apply -f opensearch-metrics-store.yaml

# Wait for the pod to be ready
echo "⏳ Waiting for metrics store pod to be ready..."
kubectl wait --for=condition=ready pod -l app=opensearch-metrics-store -n "$NAMESPACE" --timeout=300s || {
    echo "❌ Timeout waiting for metrics store pod"
    echo "📊 Current pod status:"
    kubectl get pods -n "$NAMESPACE"
    echo ""
    echo "📋 Pod logs:"
    kubectl logs -n "$NAMESPACE" -l app=opensearch-metrics-store --tail=50
    exit 1
}

# Verify the cluster is healthy
echo "🔍 Verifying cluster health..."
POD_NAME=$(kubectl get pods -n "$NAMESPACE" -l app=opensearch-metrics-store -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n "$NAMESPACE" "$POD_NAME" -- curl -sk -u admin:admin https://localhost:9200/_cluster/health?pretty

echo ""
echo "=================================================="
echo "✅ Metrics Store Deployment Complete!"
echo "=================================================="
echo ""
echo "📊 Metrics Store Details:"
echo "   Namespace: $NAMESPACE"
echo "   Service: opensearch-metrics-store.$NAMESPACE.svc.cluster.local:9200"
echo "   Pod: $POD_NAME"
echo ""
echo "🔧 Useful Commands:"
echo "   Check status: kubectl get pods -n $NAMESPACE"
echo "   View logs: kubectl logs -n $NAMESPACE $POD_NAME"
echo "   Port forward: kubectl port-forward -n $NAMESPACE svc/opensearch-metrics-store 9200:9200"
echo ""

# Made with Bob