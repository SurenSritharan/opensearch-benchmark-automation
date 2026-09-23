# Local Benchmark Runner

The `local/` folder contains scripts for running benchmark pipelines directly on local machines, VMs, or Jenkins agents without requiring Kubernetes, GKE manifests, or the Flask cloud service.

---

## Prerequisites

- Python 3.9+
- OpenSearch instance reachable via HTTP/HTTPS

---

## Setup & Installation

```bash
cd opensearch-benchmark-automation

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install -r cloud-service/requirements.txt
pip install opensearch-benchmark faiss-cpu pyarrow numpy datasets
```

---

## Running a Pipeline

```bash
python3 local/run_pipeline.py \
    --pipeline parquet-50k \
    --engine jvector \
    --target-host at-opensearch.dev.fyre.ibm.com:9200 \
    --workloads-dir ../opensearch-benchmark-workloads \
    --results-dir ./results/run-1 \
    --auth-user admin \
    --auth-pass <password> \
    --use-ssl true
```

---

## CLI Options

| Option | Default | Description |
| :--- | :--- | :--- |
| `--pipeline` | *(required)* | Name of pipeline (e.g., `parquet-50k` or path `pipelines/parquet-50k.json`) |
| `--engine` | `jvector` | Target engine (`jvector`, `faiss`, `lucene`) |
| `--target-host` | `127.0.0.1:9200` | OpenSearch host and port |
| `--workloads-dir` | `../opensearch-benchmark-workloads` | Path to workloads directory |
| `--results-dir` | `./results/local` | Directory where per-step `test_run.json` and logs will be saved |
| `--corpus-size` | `50k` | Default corpus size (e.g. `50k`, `1m`, `5m`) |
| `--use-ssl` | `true` | Use HTTPS (`true` / `false`) |
| `--auth-user` | `admin` | Basic auth username |
| `--auth-pass` | `admin` | Basic auth password |
| `--timeout` | `300` | Request timeout in seconds |
