#!/bin/bash

set -e

echo "=========================================="
echo "Deploying Separated 4-Node OpenSearch Cluster"
echo "1 Cluster Manager + 3 Data Nodes"
echo "=========================================="

echo ""
echo "Step 1: Cleaning up existing cluster..."
kubectl delete -f opensearch-jvector-cluster-manager.yaml --ignore-not-found=true
kubectl delete -f opensearch-jvector-data-nodes.yaml --ignore-not-found=true

echo "Waiting for pods to terminate..."
kubectl wait --for=delete pod -l app=opensearch-cluster-manager -n os-jvector --timeout=120s 2>/dev/null || true
kubectl wait --for=delete pod -l app=opensearch-data -n os-jvector --timeout=120s 2>/dev/null || true

echo "Deleting PVCs for clean slate..."
kubectl delete pvc -l app=opensearch-cluster-manager -n os-jvector --ignore-not-found=true
kubectl delete pvc -l app=opensearch-data -n os-jvector --ignore-not-found=true

echo "Waiting for cleanup to complete..."
sleep 5

echo ""
echo "Step 2: Deploying Cluster Manager..."
kubectl apply -f opensearch-jvector-cluster-manager.yaml

echo "Waiting for cluster manager to be ready..."
kubectl wait --for=condition=ready pod/opensearch-cluster-manager-0 -n os-jvector --timeout=600s

echo ""
echo "Step 3: Deploying Data Nodes..."
kubectl apply -f opensearch-jvector-data-nodes.yaml

echo "Waiting for data nodes to be ready (this may take several minutes)..."
for i in 0 1 2; do
  echo "  Waiting for opensearch-data-$i..."
  kubectl wait --for=condition=ready pod/opensearch-data-$i -n os-jvector --timeout=600s
done

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Cluster Configuration:"
echo "  - opensearch-cluster-manager-0: Cluster Manager only"
echo "  - opensearch-data-0: Data + Ingest"
echo "  - opensearch-data-1: Data + Ingest"
echo "  - opensearch-data-2: Data + Ingest"
echo ""
echo "Services:"
echo "  - opensearch-cluster: Routes to data nodes only (for benchmarking)"
echo "  - opensearch-data: Headless service for data nodes"
echo "  - opensearch-cluster-manager: Headless service for cluster manager"
echo ""
echo "For performance testing, use: opensearch-cluster:9200"
echo ""
echo "Verifying cluster health..."
kubectl exec opensearch-cluster-manager-0 -n os-jvector -- curl -sk -u admin:admin https://localhost:9200/_cluster/health?pretty

echo ""
echo "Checking node roles..."
kubectl exec opensearch-cluster-manager-0 -n os-jvector -- curl -sk -u admin:admin https://localhost:9200/_cat/nodes?v

echo ""
echo "Done!"

# Made with Bob
