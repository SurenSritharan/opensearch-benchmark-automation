# OpenSearch Benchmark Automation

A cloud-native benchmark automation framework for testing OpenSearch vector search performance across multiple vector engines (FAISS, Lucene, JVector) on Google Kubernetes Engine (GKE).

## 🚀 Quick Start — Jenkins

The primary way to run benchmarks is via the Jenkins pipeline:

**[http://os-perf-jenkins.dev.fyre.ibm.com:8080/job/benchmark-service-runner/](http://os-perf-jenkins.dev.fyre.ibm.com:8080/job/benchmark-service-runner/)**

> Login with your **IBM ID credentials**.

The pipeline handles everything: scaling up OpenSearch clusters and benchmark workers, seeding dataset caches from GCS, running benchmarks in parallel across engines, collecting results and telemetry, and scaling everything back down.

A nightly run is triggered automatically at **07:00 UTC** every day.

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Jenkins (IBM VPN)                                                   │
│  os-perf-jenkins.dev.fyre.ibm.com:8080                              │
│  Jenkinsfile → orchestrates all stages                               │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ kubectl + HTTP
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  GKE Cluster                                                         │
│                                                                      │
│  namespace: benchmark-api                                            │
│  ├── opensearch-benchmark-api-server  (Flask REST API + Web UI)      │
│  ├── opensearch-benchmark-worker-jvector  (runs osb CLI)            │
│  ├── opensearch-benchmark-worker-faiss    (runs osb CLI)            │
│  └── opensearch-benchmark-worker-lucene   (runs osb CLI)            │
│                                                                      │
│  namespace: os-jvector   ──► 1 cluster-manager + 3 data nodes       │
│  namespace: os-faiss     ──► 1 cluster-manager + 3 data nodes       │
│  namespace: os-lucene    ──► 1 cluster-manager + 3 data nodes       │
└──────────────────────────────────────────────────────────────────────┘
```

Each engine namespace contains a dedicated 4-node OpenSearch cluster (1 cluster manager + 3 data/ingest nodes). A single shared API server in `benchmark-api` receives job requests; engine-specific worker pods execute `opensearch-benchmark` against their respective cluster.

Clusters are **scaled to zero** between runs to save cost and scaled back up on demand by Jenkins.

---

## 🎛️ Jenkins Pipeline Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PIPELINE` | `complete` | `complete` (index → ingest → merge → search) or `search-only` (skip indexing) |
| `CORPUS_SIZE` | `all` | `1m`, `5m`, or `all` (runs both sequentially) |
| `DATASET` | `all` | `cohere-wiki-en-768`, `cohere-msmarco-1024`, or `all` |
| `ENGINE_TARGET` | `all` | `os-jvector`, `os-faiss`, `os-lucene`, or `all` |
| `SCALE_CLUSTERS` | `true` | Scale up clusters and workers before the run, scale down after |
| `REDEPLOY_CLUSTERS` | `false` | Re-deploy OpenSearch clusters (applies `OPENSEARCH_VERSION`) |
| `OPENSEARCH_VERSION` | `3.7.0` | OpenSearch version to deploy |
| `DELETE_PVCS` | `false` | Delete PVCs during re-deploy (destroys all indexed data) |
| `SKIP_SCALE_DOWN` | `false` | Keep clusters and workers running after the run for investigation |

---

## 📋 Pipeline Stages

1. **Prepare** — writes the GKE kubeconfig from the Jenkins credential
2. **Re-deploy Clusters** *(optional)* — deploys fresh OpenSearch clusters at a specific version
3. **Scale Up** — scales OpenSearch clusters and benchmark workers to 1 replica each (in parallel); waits for green/yellow cluster health
4. **Verify Health** — checks pod status and API health endpoint
5. **Seed Dataset Cache** — downloads required GCS corpus files to each worker PVC (skips if already present)
6. **Per-Engine** *(parallel)* — for each engine: runs the benchmark pipeline, fetches results JSON, collects server logs and OpenSearch telemetry
7. **Build Summary** — writes `results/<build_id>/BUILD_SUMMARY.txt`
8. **Archive Results** — archives all result artifacts in Jenkins
9. **Post / Scale Down** — scales all workers and clusters back to zero (unless `SKIP_SCALE_DOWN=true`)

---

## 🌐 Cloud Service API

The benchmark API server exposes a REST API and a Web UI accessible at the `API_URL`.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/v1/discover` | List available datasets and engines from `config/datasets.yaml` |
| `POST` | `/api/v1/sync` | `git pull` both repos and reload configuration |
| `POST` | `/api/v1/benchmark` | Submit a new benchmark job |
| `GET` | `/api/v1/benchmark` | List all jobs |
| `GET` | `/api/v1/benchmark/<job_id>` | Get job status and results |
| `GET` | `/api/v1/benchmark/<job_id>/results` | Get structured results (sweeps, metrics) |
| `POST` | `/api/v1/benchmark/<job_id>/cancel` | Cancel a queued or running job |
| `DELETE` | `/api/v1/benchmark/<job_id>` | Delete a job (`?cleanup_results=true` also removes result files) |

### Web UI

Open the `API_URL` in a browser for the full Web UI: submit jobs, monitor status, view results, and sync configuration from Git.

---

## 🔧 Configuration

### Datasets — `config/datasets.yaml`

All dataset and workload configuration lives in [`config/datasets.yaml`](config/datasets.yaml). The API server reads this file at startup and on every `/api/v1/sync` call.

Key fields per dataset:

| Field | Description |
|-------|-------------|
| `workload_name` | Directory name in the workloads repository |
| `dimension` | Vector dimension |
| `format` | Data format (`hdf5`, `fvec`, etc.) |
| `space_type` | Distance metric (`innerproduct`, `l2`, `cosinesimil`) |
| `default_params` | Jinja2 template variable overrides |
| `param_files` | Per-engine workload parameter file paths |
| `test_procedures` | List of test procedures (supports parameter sweeps) |
| `gcs_cache_files` | GCS objects to pre-seed to worker PVCs before each run |

See [`config/ADDING_DATASETS.md`](config/ADDING_DATASETS.md) for a step-by-step guide and full field reference.

### Updating Config Without Redeploying

Push changes to `config/datasets.yaml` or workload parameter files, then call the sync endpoint:

```bash
# Via API
curl -X POST http://<API_URL>/api/v1/sync

# Via Web UI — click the "🔄 Sync from Git" button
```

This performs `git pull` on both `opensearch-benchmark-automation` and `opensearch-benchmark-workloads`, then reloads configuration into memory. Changes are live within seconds.

---

## 🚢 GKE Deployment

### Cluster Namespaces

| Namespace | Engine | Notes |
|-----------|--------|-------|
| `os-jvector` | JVector | Dedicated manifests |
| `os-faiss` | FAISS | Shares manifests with `os-lucene` via `${NAMESPACE}` substitution |
| `os-lucene` | Lucene | Shares manifests with `os-faiss` |
| `benchmark-api` | — | API server + per-engine workers |

### Deploy / Scale Scripts

```bash
# Deploy a single cluster namespace
gke-manifest/deploy-namespace-cluster.sh os-jvector

# Deploy all three engine clusters
gke-manifest/deploy-all-clusters.sh

# Scale up a single namespace
gke-manifest/scale-up-clusters.sh os-faiss

# Scale down all clusters
gke-manifest/scale-down-clusters.sh all

# Destroy a single namespace
gke-manifest/destroy-namespace-cluster.sh os-jvector
```

### Deploy the Benchmark API Server

```bash
cd gke-manifest
./deploy-benchmark-api.sh
```

### First-time Setup

If a namespace has no StatefulSets yet, the Jenkins Scale Up stage automatically runs `deploy-namespace-cluster.sh` with the configured `OPENSEARCH_VERSION`.

---

## 🗂️ Repository Structure

```
opensearch-benchmark-automation/
├── Jenkinsfile                          # CI/CD pipeline definition
├── config/
│   ├── datasets.yaml                    # Dataset and workload configuration
│   └── ADDING_DATASETS.md               # Guide for adding new datasets
├── cloud-service/
│   ├── app.py                           # Flask REST API
│   ├── benchmark_runner.py              # Benchmark execution logic
│   ├── config_loader.py                 # datasets.yaml parser
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── scripts/
│   │   └── run-pipeline.sh             # Pipeline driver (called by Jenkins)
│   └── web/
│       └── index.html                  # Web UI
├── gke-manifest/
│   ├── opensearch-jvector-cluster-manager.yaml
│   ├── opensearch-jvector-data-nodes.yaml
│   ├── opensearch-standard-cluster-manager.yaml
│   ├── opensearch-standard-data-nodes.yaml
│   ├── opensearch-benchmark-api-server.yaml
│   ├── opensearch-benchmark-worker-template.yaml
│   ├── deploy-namespace-cluster.sh
│   ├── deploy-all-clusters.sh
│   ├── deploy-benchmark-api.sh
│   ├── scale-up-clusters.sh
│   ├── scale-down-clusters.sh
│   ├── destroy-namespace-cluster.sh
│   └── destroy-all-clusters.sh
```

---

## 📊 Benchmark Scenarios

| Scenario | Pipeline steps | Description |
|----------|---------------|-------------|
| `create-index` | index | Creates the vector index with engine-specific settings |
| `bulk-ingest` | index | Loads vector corpus data |
| `refresh` | index | Triggers an index refresh |
| `force-merge` | merge | Force-merges to 1 segment for stable search performance |
| `search` | search | Runs search sweeps across configured client/query combinations |

The `complete` pipeline runs all steps in order. The `search-only` pipeline skips to search (indexes must already exist on the clusters).

---

## 🤝 Contributing

1. Make changes to workload parameters or dataset config
2. `git push origin main`
3. Click **"🔄 Sync from Git"** in the Web UI (or `POST /api/v1/sync`) — no pod restart needed

For cluster manifest changes, run `gke-manifest/deploy-benchmark-api.sh` to redeploy the API server.

---

## 👤 Author

Suren Sritharan

## 🔗 Repositories

- Automation: https://github.com/SurenSritharan/opensearch-benchmark-automation
- Workloads: https://github.com/SurenSritharan/opensearch-benchmark-workloads
