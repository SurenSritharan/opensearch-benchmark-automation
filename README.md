# OpenSearch Benchmark Automation

A modular benchmark automation framework for testing OpenSearch vector search performance across multiple vector engines (FAISS, Lucene, JVector).

## 🚀 Features

- **Multi-Engine Support**: Test FAISS, Lucene, and JVector engines in a single run
- **Parallel Execution**: Run all engines simultaneously with background logging (NEW!)
- **Comprehensive Scenarios**:
  - Index creation and validation
  - Bulk vector ingestion
  - Force merge operations
  - Search concurrency testing with multiple client configurations
- **Organized Results**: All benchmark results stored in timestamped directories under `results/`
- **Enhanced Output**: Clear visual separators and progress indicators for each scenario
- **Real-time Monitoring**: Dedicated log viewer for monitoring parallel execution
- **Profiling Support**: Automatic CPU flame graph generation using async-profiler
- **Telemetry Collection**: Comprehensive cluster health, stats, and performance metrics
- **Kubernetes Integration**: Designed to work with OpenSearch clusters deployed on GKE

## 📋 Prerequisites

- Kubernetes cluster with OpenSearch deployments
- `kubectl` configured with access to your cluster
- `jq` for JSON parsing
- Python 3 with PyYAML (for dataset configuration parsing)
- Bash shell

## 🏗️ Project Structure

```
opensearch-benchmark-automation/
├── run_benchmark.py              # Main Python benchmark orchestrator (sequential)
├── run_benchmark_parallel.py     # Parallel execution wrapper
├── view_logs.py                  # Real-time log viewer
├── run-benchmark.sh              # Shell wrapper script (sequential)
├── run-benchmark-parallel.sh     # Shell wrapper script (parallel)
├── setup-venv.sh                 # Virtual environment setup
├── requirements.txt              # Python dependencies
├── lib/
│   ├── benchmark_executor.py    # Benchmark execution and OSB command management
│   ├── config_manager.py        # Configuration and argument parsing
│   ├── dataset_manager.py       # Dataset and workload management
│   ├── profiling_manager.py     # Profiling orchestration
│   ├── metrics_collector.py     # GKE metrics collection
│   ├── telemetry_collector.py   # Cluster telemetry collection
│   ├── server_log_collector.py  # OpenSearch log collection
│   ├── dashboard_generator.py   # Dashboard generation
│   └── kubectl_helper.py        # Kubernetes helper utilities
├── config/
│   ├── datasets.yaml            # Dataset configurations
│   └── cluster.yaml             # Cluster connection settings
├── gke-manifest/
│   ├── deploy-separated-cluster.sh
│   ├── deploy-namespace-cluster.sh
│   ├── deploy-all-clusters.sh
│   ├── opensearch-jvector-cluster-manager.yaml
│   ├── opensearch-jvector-data-nodes.yaml
│   ├── opensearch-standard-cluster-manager.yaml
│   ├── opensearch-standard-data-nodes.yaml
│   ├── opensearch-benchmark-client.yaml
│   ├── README-SEPARATED-CLUSTER.md
│   └── README-NAMESPACE-DEPLOYMENT.md
├── scripts/
│   ├── check-index-stats.sh
│   ├── check-shard-info.sh
│   ├── check-async-profiler.sh
│   ├── collect-gke-metrics.sh
│   └── test-index-creation.sh
└── results/                     # Benchmark results (gitignored)
```

### Key Directories

- **`lib/`**: Core Python modules for benchmark orchestration
  - `benchmark_executor.py`: Manages OSB command execution and result collection
  - `config_manager.py`: Handles configuration loading and CLI argument parsing
  - `dataset_manager.py`: Manages datasets and workload configurations
  - `profiling_manager.py`: Orchestrates profiling during benchmark runs
  - `metrics_collector.py`: Collects GKE metrics during benchmarks
  - `telemetry_collector.py`: Collects cluster health and performance telemetry
  - `server_log_collector.py`: Captures OpenSearch server logs
  - `dashboard_generator.py`: Generates interactive dashboards
  - `kubectl_helper.py`: Kubernetes operations helper
- **`config/`**: Configuration files
  - `datasets.yaml`: Dataset definitions and test procedures
  - `cluster.yaml`: Cluster connection settings
- **`gke-manifest/`**: Kubernetes deployment manifests for OpenSearch clusters
  - Deployment scripts for separated and multi-namespace architectures
  - StatefulSet manifests for cluster managers and data nodes
  - Benchmark client pod configuration
- **`scripts/`**: Utility scripts for cluster management and diagnostics
- **`results/`**: Timestamped benchmark results and telemetry (gitignored)

**Note**: Workload definitions are stored in the [opensearch-benchmark-workloads](https://github.com/SurenSritharan/opensearch-benchmark-workloads) repository, which is automatically cloned to `/datasets/opensearch-benchmark-workloads/` on the benchmark client pod.

## 🎯 Usage

### Installation

First, install Python dependencies:
```bash
pip install -r requirements.txt
```

### Quick Start

**Option 1: Parallel Execution (Recommended for multiple engines)**

Run all engines simultaneously with background logging:
```bash
# Run all engines in parallel
./run-benchmark-parallel.sh

# Monitor progress in another terminal
./view_logs.py --follow-all
```

**Option 2: Sequential Execution (Original)**

Run benchmarks sequentially with live stdout:
```bash
./run-benchmark.sh
```

### Parallel Execution (NEW!)

For detailed parallel execution guide, see [PARALLEL_EXECUTION_GUIDE.md](PARALLEL_EXECUTION_GUIDE.md)

**Run benchmarks in parallel:**
```bash
# All engines with all scenarios
./run-benchmark-parallel.sh --engine all --scenario all

# Specific engines only
./run-benchmark-parallel.sh --engine jvector,faiss --scenario search

# With custom settings
./run-benchmark-parallel.sh --dataset msmarco --clients 50,100 --disable-profiling
```

**Monitor progress:**
```bash
# Show status
./view_logs.py

# Follow specific engine
./view_logs.py --tail faiss --follow

# Follow all engines
./view_logs.py --follow-all

# Check for errors
./view_logs.py --errors
```

### Sequential Execution (Original)

**Command-Line Options:**

```bash
./run-benchmark.sh [OPTIONS]

Options:
  --engine <engine>       Specify engine(s): jvector, faiss, lucene, or all
  --dataset <dataset>     Specify dataset: cohere-1m, msmarco (default: cohere-1m)
  --list-datasets         List all available datasets and exit
  --scenario <scenario>   Specify scenario(s): index, merge, search, or all
  --help, -h              Show this help message
```

### Example Commands

Test FAISS with MS MARCO dataset:
```bash
./run-benchmark.sh --engine faiss --dataset msmarco --scenario all
```

List available datasets:
```bash
./run-benchmark.sh --list-datasets
```

Test all engines with Cohere dataset (default):
```bash
./run-benchmark.sh --engine all --dataset cohere-1m --scenario search
```

Run full benchmark suite with default dataset:
```bash
./run-benchmark.sh --engine all --scenario all
```

## 📊 Benchmark Scenarios

### 1. Create Index
- Creates target index with engine-specific configurations
- Validates index mapping and field types
- Output: `results/{timestamp}/{engine}-metrics/scenario-1-create-index/`

### 2. Bulk Ingestion
- Loads vector data into the index
- Measures ingestion throughput and performance
- Output: `results/{timestamp}/{engine}-metrics/scenario-2-custom-vector-bulk/`

### 3. Force Merge
- Performs force merge operation on the index
- Optimizes segment structure
- Output: `results/{timestamp}/{engine}-metrics/scenario-force-merge-index/`

### 4. Search Concurrency Matrix
- Tests search performance with varying client counts (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
- Collects comprehensive metrics:
  - Throughput (ops/s)
  - Latency percentiles (p50, p90, p99, p99.9, p99.99, max)
  - Service time percentiles
  - Error rates
  - Recall metrics (recall@k, recall@1)
  - GC statistics
- Output: `results/{timestamp}/{engine}-metrics/scenario-search-only/`
  - Individual run logs in `clients-{N}/` subdirectories
  - Aggregated metrics in `summary.csv`

## 📈 Results Structure

```
results/
└── 20260605-145009/              # Timestamp-based directory
    ├── faiss-metrics/            # Per-engine results
    │   ├── scenario-1-create-index/
    │   │   ├── console.log
    │   │   └── index-mapping-validation.json
    │   ├── scenario-2-custom-vector-bulk/
    │   │   ├── console.log
    │   │   ├── test_run.json
    │   │   └── profiles/
    │   │       ├── cpu_flame_graph_bulk-node-0.html
    │   │       ├── disk_io_bulk-node-0.log
    │   │       └── jvm_memory_bulk-node-0.log
    │   ├── scenario-force-merge-index/
    │   ├── scenario-search-only/
    │   │   ├── clients-10/
    │   │   ├── clients-50/
    │   │   │   ├── console.log
    │   │   │   ├── test_run.json
    │   │   │   └── profiles/
    │   │   │       ├── cpu_flame_graph_search-50c-node-0.html
    │   │   │       ├── disk_io_search-50c-node-0.log
    │   │   │       └── jvm_memory_search-50c-node-0.log
    │   │   ├── ...
    │   │   └── summary.csv
    │   └── cluster-telemetry-state/
    ├── lucene-metrics/           # Lucene engine results
    └── jvector-metrics/          # JVector engine results
```

## 🔧 Configuration

### Global Configuration

Edit `run-benchmark.sh` to modify default behavior:
```bash
# Enable/disable profiling (CPU flame graphs, memory, disk I/O)
ENABLE_PROFILING=true
```

### Profiling Features

When `ENABLE_PROFILING=true`, the framework automatically:
- **Bulk Ingestion**: Profiles all OpenSearch nodes during vector ingestion (300s duration)
- **Search Tests**: Profiles nodes during high-concurrency tests (≥50 clients, 120s duration)

**Profiling Artifacts Generated:**
- `cpu_flame_graph_*.html` - Interactive CPU flame graphs (async-profiler)
- `disk_io_*.log` - Disk I/O statistics before/after
- `jvm_memory_*.log` - JVM heap and native memory snapshots

**Viewing Flame Graphs:**
Open the HTML files in any web browser to analyze CPU hotspots and call stacks.

### Dataset Configuration

Datasets are configured in `config/datasets.yaml`.

**Available Datasets:**
- **cohere-1m**: 768-dimensional vectors - Cohere embeddings dataset
- **msmarco**: 1024-dimensional vectors (default) - MS MARCO passage ranking dataset with automatic data download

All datasets use workloads from the [opensearch-benchmark-workloads](https://github.com/SurenSritharan/opensearch-benchmark-workloads) repository, which is automatically cloned to `/datasets/opensearch-benchmark-workloads/` on the benchmark client pod.

#### Adding a New Dataset

To add a new dataset, configure it in `config/datasets.yaml`:

```yaml
datasets:
  your-dataset:
    dimension: 768
    format: hdf5  # or fvec, etc.
    space_type: "innerproduct"  # or "cosinesimil", "l2", etc.
    description: "Your dataset description"
    workload_name: "vectorsearch"  # Must match folder name in opensearch-benchmark-workloads
    index_name: "your-index-name"  # Optional: custom index name
    data_dir: "/datasets/your-dataset"  # Where data files are stored
    data_files:  # Optional: files to download
      - name: "vectors.hdf5"
        url: "https://example.com/vectors.hdf5"
        size: "~1GB"
    param_files:
      faiss: "params/faiss-your-dataset.json"
      lucene: "params/lucene-your-dataset.json"
      jvector: "params/jvector-your-dataset.json"
    test_procedures:
      - name: "create-index-only"
      - name: "bulk-ingest-only"
      - name: "search-only"
        parameter_sweeps:  # Optional: parameter variations
          - params:
              query_clients: 50
              query_count: 25000
```

**Key Configuration Fields:**
- `workload_name`: Must match a workload directory in the opensearch-benchmark-workloads repository
- `data_files`: Optional list of files to download before running benchmarks
- `param_files`: Engine-specific parameter files in the workload's `params/` directory
- `test_procedures`: List of test procedures to run, with optional parameter sweeps
- `default_params`: Default parameters passed to all test procedures (can be overridden by parameter sweeps)

**Example: MS MARCO Dataset**
The msmarco dataset demonstrates the configuration:
- Automatically downloads 3 data files (~4.1GB total) to `/datasets/msmarco/`
- Uses the `msmarco` workload from opensearch-benchmark-workloads
- Defines parameter sweeps for search tests with different client counts
- Stores engine-specific parameters in `workloads/msmarco/params/`

### Engine-Specific Parameters

Workload parameters are defined in the parameter files under `workloads/{workload_name}/params/`:
- Index settings (shards, replicas)
- Vector dimensions (automatically set based on selected dataset)
- Engine-specific configurations (ef_construction, m, etc.)
- Search parameters

Each engine has its own parameter file (e.g., `faiss-cohere-1m-768-dp.json`, `jvector-cohere-1m-768-dp.json`)

## 🚢 Deployment

### GKE Deployment

#### Create GKE Clusters
Deploy separate clusters for each engine across three namespaces:

```bash
# Deploy to specific namespace
./gke-manifest/deploy-namespace-cluster.sh os-jvector
./gke-manifest/deploy-namespace-cluster.sh os-faiss
./gke-manifest/deploy-namespace-cluster.sh os-lucene

# Or deploy all at once
./gke-manifest/deploy-all-clusters.sh
```

See [GKE-MANIFEST](gke-manifest/README.md) for details.

#### Available Manifests
- `opensearch-jvector-cluster-manager.yaml` - JVector cluster manager
- `opensearch-jvector-data-nodes.yaml` - JVector data nodes
- `opensearch-standard-cluster-manager.yaml` - Standard cluster manager (FAISS/Lucene)
- `opensearch-standard-data-nodes.yaml` - Standard data nodes (FAISS/Lucene)
- `opensearch-benchmark-client.yaml` - Benchmark client pod

## 📝 Output Format

The benchmark runner provides clear, formatted output:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 SCENARIO: Create Index [faiss]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ▶ Running index creation...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 SCENARIO: Search Concurrency Matrix Sweeps [faiss]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ▶ Running search test with 10 concurrent clients...
  ▶ Running search test with 20 concurrent clients...
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## 📄 License

This project is open source and available under the MIT License.

## 👤 Author

Suren Sritharan

## 🔗 Repository

https://github.com/SurenSritharan/opensearch-benchmark-automation