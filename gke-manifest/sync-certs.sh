#!/bin/bash
# sync-certs.sh — Regenerate node/admin certs and sync opensearch-shared-certs
# secret to all namespaces.
#
# The root CA is reused if it already exists in gke-manifest/certs/ so all
# namespaces always share the same CA. Node/admin certs are always regenerated
# so they stay fresh and match the current root CA.
#
# Usage: ./gke-manifest/sync-certs.sh
# Prerequisites: openssl, kubectl (with a valid KUBECONFIG)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Regenerating certificates ==="
"$SCRIPT_DIR/generate-certs.sh"

CERT_DIR="$SCRIPT_DIR/certs"

echo ""
echo "=== Syncing opensearch-shared-certs to all namespaces ==="
for ns in benchmark-api benchmark-api-develop os-jvector os-faiss os-lucene os-develop-jvector os-develop-faiss os-develop-lucene; do
    if kubectl get namespace "$ns" &>/dev/null; then
        kubectl create secret generic opensearch-shared-certs \
            --from-file="$CERT_DIR/root-ca.pem" \
            --from-file="$CERT_DIR/root-ca-key.pem" \
            --from-file="$CERT_DIR/esnode.pem" \
            --from-file="$CERT_DIR/esnode-key.pem" \
            --from-file="$CERT_DIR/admin.pem" \
            --from-file="$CERT_DIR/admin-key.pem" \
            -n "$ns" --dry-run=client -o yaml | kubectl apply -f -
        echo "  ✅ $ns"
    else
        echo "  ⏭  $ns (namespace not found — skipping)"
    fi
done

echo ""
echo "✅ Done. Restart affected pods to pick up the new certs."
