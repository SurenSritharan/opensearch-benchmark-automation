#!/usr/bin/env bash
# Creates ./gke-sa-kubeconfig.yaml for the jenkins SA in benchmark-api.
# Uses a Secret-based token (never expires) rather than kubectl create token.
#
# Prerequisites:
#   - kubectl configured with a context that has cluster-admin or sufficient access
#   - gke-manifest/jenkins-agent-rbac.yaml already applied
#
# Usage:
#   ./gke-manifest/create-credential.sh
#   Then upload ./gke-sa-kubeconfig.yaml to Jenkins as a Secret file
#   credential with ID: gke-kubeconfig

set -euo pipefail

# Ensure the non-expiring token Secret exists
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: jenkins-token
  namespace: benchmark-api
  annotations:
    kubernetes.io/service-account.name: jenkins
type: kubernetes.io/service-account-token
EOF

# Wait for the controller to populate the token
echo "Waiting for token to be populated..."
for i in $(seq 1 10); do
    TOKEN=$(kubectl get secret jenkins-token -n benchmark-api \
        -o jsonpath='{.data.token}' 2>/dev/null | base64 -d)
    if [ -n "$TOKEN" ]; then break; fi
    sleep 2
done

if [ -z "$TOKEN" ]; then
    echo "ERROR: token was not populated in jenkins-token secret" >&2
    exit 1
fi

# Get cluster details from the current context
SERVER=$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')
CA=$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
CLUSTER=$(kubectl config view --minify -o jsonpath='{.clusters[0].name}')

# Write the kubeconfig
cat > ./gke-sa-kubeconfig.yaml << EOF
apiVersion: v1
kind: Config
clusters:
- name: ${CLUSTER}
  cluster:
    server: ${SERVER}
    certificate-authority-data: ${CA}
contexts:
- name: jenkins
  context:
    cluster: ${CLUSTER}
    user: jenkins
current-context: jenkins
users:
- name: jenkins
  user:
    token: ${TOKEN}
EOF

chmod 600 ./gke-sa-kubeconfig.yaml
echo "Written: ./gke-sa-kubeconfig.yaml"
echo ""
echo "Verifying access..."
KUBECONFIG=./gke-sa-kubeconfig.yaml kubectl get pods -n benchmark-api
echo ""
echo "Upload ./gke-sa-kubeconfig.yaml to Jenkins as a Secret file credential with ID: gke-kubeconfig"

# Made with Bob
