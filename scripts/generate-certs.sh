openssl genrsa -out root-ca-key.pem 2048
openssl req -new -x509 -sha256 -key root-ca-key.pem -out root-ca.pem -days 365

# Generate node certificate
openssl genrsa -out esnode-key.pem 2048
openssl req -new -key esnode-key.pem -out esnode.csr
openssl x509 -req -in esnode.csr -CA root-ca.pem -CAkey root-ca-key.pem -CAcreateserial -out esnode.pem -days 365

# Create Kubernetes secret
kubectl create secret generic opensearch-shared-certs -n os-jvector \
  --from-file=esnode.pem=esnode.pem \
  --from-file=esnode-key.pem=esnode-key.pem \
  --from-file=root-ca.pem=root-ca.pem