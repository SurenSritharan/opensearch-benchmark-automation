# Cloud-Native OpenSearch Benchmark Service

A REST API service for running OpenSearch benchmarks in Kubernetes without kubectl dependencies.

## Architecture

This service is designed to run inside a Kubernetes cluster and execute benchmarks directly using the `opensearch-benchmark` CLI tool. It reads configuration from the project's `config/datasets.yaml` and workload parameter files.

## Features

- ✅ REST API for triggering benchmarks
- ✅ No kubectl dependencies (cloud-native)
- ✅ Reads dataset configurations from `config/datasets.yaml`
- ✅ Supports multiple engines (jvector, faiss, lucene)
- ✅ Job tracking and status monitoring
- ✅ Web UI for easy benchmark execution
- ✅ Persistent job history

## Directory Structure

```
cloud-service/
├── README.md                 # This file
├── app.py                    # Main Flask application
├── benchmark_runner.py       # Core benchmark execution logic
├── config_loader.py          # Configuration file reader
├── requirements.txt          # Python dependencies
├── Dockerfile               # Container image definition
└── web/
    └── index.html           # Web UI
```

## API Endpoints

### `GET /api/v1/discover`
Discover available datasets and engines from configuration files.

### `POST /api/v1/benchmark`
Trigger a new benchmark job.

### `GET /api/v1/benchmark/<job_id>`
Get status and results of a specific job.

### `GET /api/v1/benchmark`
List all jobs.

## Configuration

The service reads from:
- `/workspace/config/datasets.yaml` - Dataset definitions
- `/workspace/workloads/vectorsearch/params/` - Workload parameters

## Deployment

See `../gke-manifest/deploy-benchmark-api.sh` for deployment instructions.