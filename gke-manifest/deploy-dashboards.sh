#!/bin/bash
set -e

NAMESPACE="os-metrics"

echo "=================================================="
echo "🎨 Deploying OpenSearch Dashboards for Metrics Store"
echo "=================================================="

# Check if metrics store is running
if ! kubectl get pods -n "$NAMESPACE" -l app=opensearch-metrics-store | grep -q Running; then
    echo "❌ Error: Metrics store is not running in $NAMESPACE"
    echo "   Please deploy the metrics store first:"
    echo "   ./deploy-metrics-store.sh"
    exit 1
fi

echo "✅ Metrics store is running"

# Apply the dashboards manifest
echo "📋 Deploying OpenSearch Dashboards..."
kubectl apply -f opensearch-dashboards-metrics.yaml

# Wait for the pod to be ready
echo "⏳ Waiting for dashboards pod to be ready..."
kubectl wait --for=condition=ready pod -l app=opensearch-dashboards -n "$NAMESPACE" --timeout=300s || {
    echo "❌ Timeout waiting for dashboards pod"
    echo "📊 Current pod status:"
    kubectl get pods -n "$NAMESPACE" -l app=opensearch-dashboards
    echo ""
    echo "📋 Pod logs:"
    kubectl logs -n "$NAMESPACE" -l app=opensearch-dashboards --tail=50
    exit 1
}

# Get the LoadBalancer IP
echo ""
echo "⏳ Waiting for LoadBalancer IP assignment..."
sleep 10

EXTERNAL_IP=""
for i in {1..30}; do
    EXTERNAL_IP=$(kubectl get svc opensearch-dashboards -n "$NAMESPACE" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
    if [ -n "$EXTERNAL_IP" ]; then
        break
    fi
    echo "   Waiting for external IP... (attempt $i/30)"
    sleep 10
done

echo ""
echo "=================================================="
echo "✅ OpenSearch Dashboards Deployment Complete!"
echo "=================================================="
echo ""
echo "📊 Dashboard Access:"
if [ -n "$EXTERNAL_IP" ]; then
    echo "   URL: http://$EXTERNAL_IP:5601"
    echo "   Username: admin"
    echo "   Password: admin"
else
    echo "   LoadBalancer IP not yet assigned. Check with:"
    echo "   kubectl get svc opensearch-dashboards -n $NAMESPACE"
    echo ""
    echo "   Or use port-forward:"
    echo "   kubectl port-forward -n $NAMESPACE svc/opensearch-dashboards 5601:5601"
    echo "   Then access: http://localhost:5601"
fi
echo ""
echo "🔧 Useful Commands:"
echo "   Check status: kubectl get pods -n $NAMESPACE -l app=opensearch-dashboards"
echo "   View logs: kubectl logs -n $NAMESPACE -l app=opensearch-dashboards"
echo "   Port forward: kubectl port-forward -n $NAMESPACE svc/opensearch-dashboards 5601:5601"
echo ""
echo "📚 Next Steps:"
echo "   1. Access the dashboard URL"
echo "   2. Login with admin/admin"
echo "   3. Create index patterns (see DASHBOARDS_GUIDE.md)"
echo "   4. Build visualizations and dashboards"
echo ""

# Made with Bob