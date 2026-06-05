#!/bin/bash

set -e

echo "=========================================="
echo "Checking JVector Plugin Installation"
echo "=========================================="
echo ""

# Check if cluster manager exists
if kubectl get pod opensearch-cluster-manager-0 -n os-jvector &>/dev/null; then
    echo "Cluster Manager (opensearch-cluster-manager-0):"
    echo "------------------------------------------------"
    kubectl exec opensearch-cluster-manager-0 -n os-jvector -- \
        curl -sk -u admin:admin https://localhost:9200/_cat/plugins?v 2>/dev/null | grep -E "name|jvector" || echo "  No JVector plugin found"
    echo ""
fi

# Check data nodes
for i in 0 1 2; do
    if kubectl get pod opensearch-data-$i -n os-jvector &>/dev/null; then
        echo "Data Node (opensearch-data-$i):"
        echo "------------------------------------------------"
        kubectl exec opensearch-data-$i -n os-jvector -- \
            curl -sk -u admin:admin https://localhost:9200/_cat/plugins?v 2>/dev/null | grep -E "name|jvector" || echo "  No JVector plugin found"
        echo ""
    fi
done

# Check old nodes if they exist
for i in 0 1 2 3; do
    if kubectl get pod opensearch-native-$i -n os-jvector &>/dev/null; then
        echo "Legacy Node (opensearch-native-$i):"
        echo "------------------------------------------------"
        kubectl exec opensearch-native-$i -n os-jvector -- \
            curl -sk -u admin:admin https://localhost:9200/_cat/plugins?v 2>/dev/null | grep -E "name|jvector" || echo "  No JVector plugin found"
        echo ""
    fi
done

echo "=========================================="
echo "Summary"
echo "=========================================="
echo ""
echo "Expected plugin: opensearch-jvector version 3.6.0.0"
echo ""
echo "To see detailed plugin information:"
echo "  kubectl exec <pod-name> -n os-jvector -- curl -sk -u admin:admin https://localhost:9200/_nodes/plugins?pretty"

# Made with Bob
