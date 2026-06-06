#!/bin/bash

# Check status of all OpenSearch namespaces
# Usage: ./check-all-namespaces.sh

echo "=========================================="
echo "OpenSearch Cluster Status"
echo "=========================================="
echo ""

echo "=== OS-JVECTOR ==="
kubectl get pods -n os-jvector
echo ""

echo "=== OS-FAISS ==="
kubectl get pods -n os-faiss
echo ""

echo "=== OS-LUCENE ==="
kubectl get pods -n os-lucene
echo ""

echo "=========================================="
echo "Summary"
echo "=========================================="
echo ""

# Count running pods in each namespace
JVECTOR_RUNNING=$(kubectl get pods -n os-jvector --no-headers 2>/dev/null | grep -c "Running" || echo "0")
JVECTOR_TOTAL=$(kubectl get pods -n os-jvector --no-headers 2>/dev/null | wc -l || echo "0")

FAISS_RUNNING=$(kubectl get pods -n os-faiss --no-headers 2>/dev/null | grep -c "Running" || echo "0")
FAISS_TOTAL=$(kubectl get pods -n os-faiss --no-headers 2>/dev/null | wc -l || echo "0")

LUCENE_RUNNING=$(kubectl get pods -n os-lucene --no-headers 2>/dev/null | grep -c "Running" || echo "0")
LUCENE_TOTAL=$(kubectl get pods -n os-lucene --no-headers 2>/dev/null | wc -l || echo "0")

echo "os-jvector:  $JVECTOR_RUNNING/$JVECTOR_TOTAL pods running"
echo "os-faiss:    $FAISS_RUNNING/$FAISS_TOTAL pods running"
echo "os-lucene:   $LUCENE_RUNNING/$LUCENE_TOTAL pods running"
echo ""

# Check cluster health for each namespace
echo "=========================================="
echo "Cluster Health"
echo "=========================================="
echo ""

for ns in os-jvector os-faiss os-lucene; do
    echo "=== $ns ==="
    if kubectl get pod opensearch-data-0 -n $ns &> /dev/null; then
        kubectl exec -n $ns opensearch-data-0 -- curl -s -k -u admin:admin https://localhost:9200/_cluster/health?pretty 2>/dev/null | grep -E "cluster_name|status|number_of_nodes" || echo "Cluster not ready yet"
    else
        echo "Data node not available yet"
    fi
    echo ""
done

# Made with Bob
