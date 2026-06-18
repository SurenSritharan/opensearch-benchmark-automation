# Cloud-Native Benchmark Service Deployment Guide

## Overview

This cloud-native service runs OpenSearch benchmarks directly using the `opensearch-benchmark` CLI without kubectl dependencies. It reads configuration from your project's `config/datasets.yaml` and executes benchmarks against OpenSearch clusters via Kubernetes service DNS.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Kubernetes Cluster                        │
│                                                              │
│  ┌──────────────────┐         ┌─────────────────────────┐  │
│  │  Benchmark API   │         │  OpenSearch Clusters    │  │
│  │  (StatefulSet)   │────────▶│  - os-jvector           │  │
│  │                  │         │  - os-faiss             │  │
│  │  - Flask API     │         │  - os-lucene            │  │
│  │  - Web UI        │         └─────────────────────────┘  │
│  │  - Job Executor  │                                       │
│  └──────────────────┘                                       │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────────┐                                       │
│  │  Persistent      │                                       │
│  │  Storage (PVCs)  │                                       │
│  │  - Workspace     │                                       │
│  │  - Results       │                                       │
│  │  - Datasets      │                                       │
│  └──────────────────┘                                       │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. `config_loader.py`
- Reads `config/datasets.yaml`
- Parses dataset configurations
- Provides engine-to-cluster mappings
- Returns workload parameter file paths

### 2. `benchmark_runner.py`
- Executes `opensearch-benchmark` CLI directly
- Validates benchmark requests
- Manages job execution
- Captures results and errors

### 3. `app.py`
- Flask REST API server
- Job queue management
- Thread pool for concurrent execution
- Web UI serving

### 4. `web/index.html`
- User-friendly web interface
- Dataset/engine selection
- Job monitoring
- Real-time status updates

## Deployment

### Using Existing Infrastructure

The service is integrated into the existing deployment:

```bash
# Deploy the benchmark API
./gke-manifest/deploy-benchmark-api.sh

# The pod will automatically:
# 1. Clone the workspace repository
# 2. Detect cloud-service/ directory
# 3. Install cloud-service requirements
# 4. Start the cloud-native service
```

### Manual Testing (Local)

```bash
cd cloud-service

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export WORKSPACE_DIR=/path/to/opensearch-benchmark-automation
export RESULTS_DIR=/path/to/results

# Run the service
python app.py
```

## Configuration

### Dataset Configuration (`config/datasets.yaml`)

```yaml
datasets:
  cohere-1m-768-dp:
    workload: vectorsearch
    dimension: 768
    space_type: "l2"
    param_files:
      jvector: workloads/vectorsearch/params/1million/jvector-cohere-768-dp.json
      faiss: workloads/vectorsearch/params/1million/faiss-cohere-768-dp.json
      lucene: workloads/vectorsearch/params/1million/lucene-cohere-768-dp.json
```

### Cluster Endpoints

The service automatically resolves cluster endpoints using Kubernetes DNS:
- `opensearch-cluster.os-jvector.svc.cluster.local:9200`
- `opensearch-cluster.os-faiss.svc.cluster.local:9200`
- `opensearch-cluster.os-lucene.svc.cluster.local:9200`

## API Endpoints

### `GET /api/v1/discover`
Discover available datasets and engines from configuration.

**Response:**
```json
{
  "datasets": [
    {
      "name": "cohere-1m-768-dp",
      "engines": ["jvector", "faiss", "lucene"],
      "workload": "vectorsearch",
      "dimension": 768
    }
  ],
  "max_concurrent_jobs": 3
}
```

### `POST /api/v1/benchmark`
Trigger a new benchmark job.

**Request:**
```json
{
  "dataset": "cohere-1m-768-dp",
  "engines": "jvector",
  "scenarios": "search"
}
```

**Response:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "dataset": "cohere-1m-768-dp",
  "engine": "jvector",
  "scenario": "search",
  "status_url": "/api/v1/benchmark/550e8400-e29b-41d4-a716-446655440000"
}
```

### `GET /api/v1/benchmark/<job_id>`
Get job status and results.

### `GET /api/v1/benchmark`
List all jobs (last 50).

## Features

✅ **Cloud-Native**: No kubectl dependencies
✅ **Configuration-Driven**: Reads from `config/datasets.yaml`
✅ **Kubernetes-Native**: Uses service DNS for cluster discovery
✅ **Concurrent Execution**: Thread pool for parallel jobs
✅ **Web UI**: User-friendly interface
✅ **Job Tracking**: Persistent job history
✅ **Error Handling**: Detailed error messages and logs
✅ **Validation**: Pre-flight checks before execution

## Advantages Over Previous Approach

| Feature | Old (kubectl-based) | New (cloud-native) |
|---------|---------------------|-------------------|
| Dependencies | kubectl, cluster provisioner | opensearch-benchmark only |
| Cluster Access | kubectl exec | Kubernetes service DNS |
| Configuration | Hardcoded | config/datasets.yaml |
| Deployment | Complex | Simple |
| Portability | Kubernetes-specific | Cloud-agnostic |
| Maintenance | High | Low |

## Troubleshooting

### Check Pod Logs
```bash
kubectl logs -n benchmark-api opensearch-benchmark-client-0 -f
```

### Verify Configuration
```bash
kubectl exec -n benchmark-api opensearch-benchmark-client-0 -- cat /workspace/config/datasets.yaml
```

### Test Cluster Connectivity
```bash
kubectl exec -n benchmark-api opensearch-benchmark-client-0 -- \
  curl -k -u admin:admin https://opensearch-cluster.os-jvector.svc.cluster.local:9200
```

### View Job Results
```bash
kubectl exec -n benchmark-api opensearch-benchmark-client-0 -- ls -lh /results
```

## Next Steps

1. Commit the `cloud-service/` directory to your repository
2. Deploy using `./gke-manifest/deploy-benchmark-api.sh`
3. Access the web UI at the LoadBalancer IP
4. Start running benchmarks!

## Support

For issues or questions, check:
- Pod logs for execution errors
- `/results` directory for benchmark outputs
- Configuration files in `/workspace/config/`