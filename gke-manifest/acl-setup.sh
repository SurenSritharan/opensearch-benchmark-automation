#!/usr/bin/env bash
# Master ACL setup script.
#
# Usage:
#   acl-setup.sh <namespace>  — full setup (pipeline + roles + users)
#
# All OpenSearch calls are routed through the benchmark worker pod because
# Jenkins has no direct network path to the OpenSearch cluster endpoint.

set -euo pipefail

NS="${1:?Usage: $0 <namespace>  e.g. os-jvector}"
ENGINE="${NS#os-}"

export WORKER_POD="opensearch-benchmark-worker-${ENGINE}-0"
export WORKER_NS="benchmark-api"
OS_HOST="opensearch-cluster.${NS}.svc.cluster.local:9200"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "ACL setup  namespace=${NS}"
echo "Worker: ${WORKER_NS}/${WORKER_POD}"
echo "Host:   ${OS_HOST}"
echo ""

# Wait for OpenSearch to be reachable (up to 5 minutes)
echo "Waiting for OpenSearch at ${OS_HOST}..."
for i in $(seq 1 30); do
    HTTP=$(kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin -o /dev/null -w "%{http_code}" \
        "https://${OS_HOST}/_cluster/health" 2>/dev/null || echo "000")
    [ "$HTTP" = "200" ] && echo "OpenSearch reachable (attempt ${i})" && break
    echo "  attempt ${i}/30 — HTTP ${HTTP}"
    [ "$i" -eq 30 ] && echo "Timed out waiting for OpenSearch" && exit 1
    sleep 10
done
echo ""

bash "${SCRIPT_DIR}/acl-ingest-pipeline.sh" "$OS_HOST"
echo ""
bash "${SCRIPT_DIR}/acl-roles.sh"           "$OS_HOST"
echo ""
bash "${SCRIPT_DIR}/acl-users.sh"           "$OS_HOST"
echo ""
echo "ACL setup complete for ${NS}."
