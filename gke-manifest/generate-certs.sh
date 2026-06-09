#!/bin/bash
set -e

# Automatically find the directory where THIS script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_DIR="${SCRIPT_DIR}/certs"

# Create the certs folder if it doesn't exist
mkdir -p "${CERT_DIR}"
echo "📁 Target directory set to: ${CERT_DIR}"

# 1. Generate root CA
echo "📝 Generating root CA..."
openssl genrsa -out "${CERT_DIR}/root-ca-key.pem" 2048
openssl req -new -x509 -sha256 -key "${CERT_DIR}/root-ca-key.pem" -out "${CERT_DIR}/root-ca.pem" -days 365 \
    -subj "/C=US/ST=CA/L=San Francisco/O=OpenSearch/OU=Benchmark/CN=root-ca"

# 2. Create OpenSSL config for Node
cat > "${CERT_DIR}/node-cert.conf" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = US
ST = CA
L = San Francisco
O = OpenSearch
OU = Benchmark
CN = opensearch-node

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
DNS.2 = *.svc.cluster.local
DNS.3 = opensearch-cluster-manager
DNS.4 = opensearch-data
DNS.5 = opensearch-benchmark-client
IP.1 = 127.0.0.1
EOF

# 3. Generate node certificate
echo "📝 Generating node certificate..."
openssl genrsa -out "${CERT_DIR}/esnode-key.pem" 2048
openssl req -new -key "${CERT_DIR}/esnode-key.pem" -out "${CERT_DIR}/esnode.csr" -config "${CERT_DIR}/node-cert.conf"
openssl x509 -req -in "${CERT_DIR}/esnode.csr" -CA "${CERT_DIR}/root-ca.pem" -CAkey "${CERT_DIR}/root-ca-key.pem" \
    -CAcreateserial -out "${CERT_DIR}/esnode.pem" -days 365 -extensions v3_req -extfile "${CERT_DIR}/node-cert.conf"

# 4. Create OpenSSL config for Admin
cat > "${CERT_DIR}/admin-cert.conf" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = US
ST = CA
L = San Francisco
O = OpenSearch
OU = Benchmark
CN = admin

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
EOF

# 5. Generate admin certificate
echo "📝 Generating admin certificate..."
openssl genrsa -out "${CERT_DIR}/admin-key.pem" 2048
openssl req -new -key "${CERT_DIR}/admin-key.pem" -out "${CERT_DIR}/admin.csr" -config "${CERT_DIR}/admin-cert.conf"
openssl x509 -req -in "${CERT_DIR}/admin.csr" -CA "${CERT_DIR}/root-ca.pem" -CAkey "${CERT_DIR}/root-ca-key.pem" \
    -CAcreateserial -out "${CERT_DIR}/admin.pem" -days 365 -extensions v3_req -extfile "${CERT_DIR}/admin-cert.conf"

# 6. Clean up temporary CSR and config files
rm -f "${CERT_DIR}/esnode.csr" "${CERT_DIR}/admin.csr" "${CERT_DIR}/node-cert.conf" "${CERT_DIR}/admin-cert.conf" "${CERT_DIR}/root-ca.srl"

echo "✅ All certificates successfully generated inside: ${CERT_DIR}"