#!/bin/bash

set -e

echo "=========================================="
echo "Cleaning up existing JVector cluster"
echo "=========================================="

# Delete the StatefulSet and services
echo "Deleting StatefulSet and services..."
kubectl delete -f opensearch-jvector-statefulset.yaml --ignore-not-found=true

# Wait for pods to terminate
echo "Waiting for pods to terminate..."
kubectl wait --for=delete pod -l app=opensearch-native -n os-jvector --timeout=120s 2>/dev/null || true

# Delete PVCs to start fresh
echo "Deleting PVCs for clean slate..."
kubectl delete pvc -l app=opensearch-native -n os-jvector --ignore-not-found=true

# Wait a moment for cleanup
echo "Waiting for cleanup to complete..."
sleep 5

echo "=========================================="
echo "Deploying fresh JVector cluster"
echo "=========================================="

# Apply the configuration
echo "Applying JVector StatefulSet configuration..."
kubectl apply -f opensearch-jvector-statefulset.yaml

# Wait for pods to be created
echo "Waiting for pods to be created..."
sleep 5

# Watch the pods come up
echo "=========================================="
echo "Monitoring pod startup..."
echo "=========================================="
echo "Press Ctrl+C to stop watching"
echo ""

kubectl get pods -n os-jvector -w

# Made with Bob
