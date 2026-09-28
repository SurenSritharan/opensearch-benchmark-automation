# Local Benchmark Runner

The `local/` folder contains scripts for running benchmark pipelines directly on local machines, VMs, or Jenkins agents without requiring Kubernetes, GKE manifests, or the Flask cloud service.

## Running the shared worker locally

The local service uses the same `cloud-service/app.py` worker, queue, `BenchmarkRunner`, API, and result format as the GKE worker pod. It only changes the deployment settings through environment variables.

```bash
chmod +x local/run-service.sh
WORKLOADS_DIR=../opensearch-benchmark-workloads \
TARGET_HOST=127.0.0.1:9200 \
USE_SSL=false \
AUTH_PASS=admin \
local/run-service.sh
```

Open `http://127.0.0.1:8080` or submit a job through the API. The defaults use `jvector`, `./.benchmark`, and `results/local-service`; set `WORKER_ENGINES` for another engine. GKE still uses the manifest-provided defaults, including worker DNS, `/workspace`, `/datasets`, HTTPS, and mounted certificates.

The direct `local/run_pipeline.py` command remains available for Jenkins or one-shot runs that do not need the REST queue.

---

## Prerequisites

- Python 3.9+
- OpenSearch instance reachable via HTTP/HTTPS
- Google Cloud SDK (`gcloud` or `gsutil`) if running datasets with GCS pre-seeded corpus files or cached ground truth

---

## Setup & Installation

```bash
cd opensearch-benchmark-automation

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install -r cloud-service/requirements.txt
# Install forked OSB (matches GKE worker pod setup)
git clone --depth 1 https://github.com/SurenSritharan/opensearch-benchmark /tmp/osb-src
pip install -e /tmp/osb-src
pip install faiss-cpu pyarrow numpy datasets
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
| `--use-ssl` | `true` | Use HTTPS (`true` / `false`) |
| `--auth-user` | `admin` | Basic auth username |
| `--auth-pass` | `admin` | Basic auth password |
| `--timeout` | `300` | Request timeout in seconds |
