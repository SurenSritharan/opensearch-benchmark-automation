# OpenSearch 4-Node Separated Cluster Architecture

## Overview

This deployment uses **two separate StatefulSets** for optimal performance testing:
- 1 dedicated Cluster Manager node
- 3 Data + Ingest nodes

## Architecture

```
┌─────────────────────────────────────────────┐
│  Cluster Manager StatefulSet                │
│  - opensearch-cluster-manager-0             │
│  - Role: cluster_manager only               │
│  - Resources: 4Gi RAM, 1-2 CPU              │
│  - Storage: 100Gi                           │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│  Data Nodes StatefulSet                     │
│  - opensearch-data-0                        │
│  - opensearch-data-1                        │
│  - opensearch-data-2                        │
│  - Role: data, ingest                       │
│  - Resources: 27Gi RAM, 7-8 CPU each        │
│  - Storage: 600Gi each                      │
└─────────────────────────────────────────────┘
```

## Quick Start

```bash
# Deploy single namespace
./deploy-namespace-cluster.sh os-jvector
./deploy-namespace-cluster.sh os-faiss
./deploy-namespace-cluster.sh os-lucene

# Deploy all namespaces
./deploy-all-clusters.sh

# Destroy single namespace
./destroy-namespace-cluster.sh os-jvector

# Destroy all namespaces
./destroy-all-clusters.sh
```

### Why FAISS and Lucene Share Configuration

Both FAISS and Lucene use the **same OpenSearch KNN plugin**. The difference is in the **index configuration** (specified when creating indices), not in the cluster setup. 

- ✅ **One set of manifests** works for both os-faiss and os-lucene
- ✅ **Namespace substitution** handles the deployment differences
- ✅ **Easier maintenance** - update once, deploy to both

## File Structure

```
gke-manifest/
├── opensearch-jvector-cluster-manager.yaml    # JVector cluster manager
├── opensearch-jvector-data-nodes.yaml         # JVector data nodes
├── opensearch-standard-cluster-manager.yaml   # Standard cluster manager (template for FAISS/Lucene)
├── opensearch-standard-data-nodes.yaml        # Standard data nodes (template for FAISS/Lucene)
├── opensearch-benchmark-client.yaml           # Benchmark client pod
├── deploy-namespace-cluster.sh                # Deploy single namespace
├── deploy-all-clusters.sh                     # Deploy all namespaces
├── destroy-namespace-cluster.sh               # Destroy single namespace
├── destroy-all-clusters.sh                    # Destroy all namespaces
├── generate-certs.sh                          # Generate SSL certificates
└── README.md                                  # This file
```

**Note**: The "standard" manifests use `${NAMESPACE}` as a template variable that gets substituted during deployment.

The script will:
1. Clean up any existing cluster
2. Deploy cluster manager first
3. Wait for cluster manager to be ready
4. Deploy data nodes
5. Wait for all data nodes to be ready
6. Verify cluster health and node roles


## Services

| Service Name | Type | Selector | Purpose |
|-------------|------|----------|---------|
| `opensearch-cluster` | ClusterIP | `app=opensearch-data` | **For benchmarking** - Routes to data nodes only |
| `opensearch-data` | Headless | `app=opensearch-data` | Data node discovery |
| `opensearch-cluster-manager` | Headless | `app=opensearch-cluster-manager` | Cluster manager discovery |

## Verification

Check cluster health:
```bash
kubectl exec opensearch-cluster-manager-0 -n os-jvector -- \
  curl -sk -u admin:admin https://localhost:9200/_cluster/health?pretty
```

Check node roles:
```bash
kubectl exec opensearch-cluster-manager-0 -n os-jvector -- \
  curl -sk -u admin:admin https://localhost:9200/_cat/nodes?v
```

Expected output:
```
ip          heap.percent ram.percent cpu load_1m load_5m load_15m node.role node.roles      cluster_manager name
10.x.x.x    10          30          5   0.50    0.40    0.30     m         cluster_manager *               opensearch-cluster-manager-0
10.x.x.x    15          50          10  1.20    1.00    0.80     di        data,ingest     -               opensearch-data-0
10.x.x.x    15          50          10  1.20    1.00    0.80     di        data,ingest     -               opensearch-data-1
10.x.x.x    15          50          10  1.20    1.00    0.80     di        data,ingest     -               opensearch-data-2
```

## Benchmarking

Use `opensearch-cluster` service (routes to data nodes only):
```bash
opensearch-benchmark run \
  --target-host=opensearch-cluster:9200 \
  --client-options="timeout:300,use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin" \
  ...
```

The `opensearch-cluster` service automatically routes **only to data nodes**, ensuring:
- Cluster manager is isolated from query load
- Accurate performance metrics
- No cluster coordination overhead in benchmark results

## Scaling

Scale data nodes independently:
```bash
kubectl scale statefulset opensearch-data -n os-jvector --replicas=3
```

The cluster manager remains unaffected.

## Benefits

✅ **Clean separation** - Each StatefulSet has dedicated configuration
✅ **Independent scaling** - Scale data nodes without touching cluster manager
✅ **Resource optimization** - Cluster manager uses minimal resources
✅ **Production-ready** - Industry standard architecture
✅ **Better performance testing** - Isolated query workload from cluster management

## Troubleshooting

View cluster manager logs:
```bash
kubectl logs opensearch-cluster-manager-0 -n os-jvector
```

View data node logs:
```bash
kubectl logs opensearch-data-0 -n os-jvector
```

Check pod status:
```bash
kubectl get pods -n os-jvector -l app=opensearch-cluster-manager
kubectl get pods -n os-jvector -l app=opensearch-data
```

## Benefits

✅ Separated cluster manager and data nodes  
✅ Independent data node scaling  
✅ Template-based deployment (FAISS/Lucene share config)  
✅ Automated certificate management  
✅ Production-ready architecture  

---

**Made with Bob**