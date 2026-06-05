# OpenSearch Benchmark Automation

A modular benchmark automation framework for testing OpenSearch vector search performance across multiple vector engines (FAISS, Lucene, JVector).

## 🚀 Features

- **Multi-Engine Support**: Test FAISS, Lucene, and JVector engines in a single run
- **Comprehensive Scenarios**: 
  - Index creation and validation
  - Bulk vector ingestion
  - Force merge operations
  - Search concurrency testing with multiple client configurations
- **Organized Results**: All benchmark results stored in timestamped directories under `results/`
- **Enhanced Output**: Clear visual separators and progress indicators for each scenario
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
├── run-benchmark.sh              # Main benchmark runner
├── lib/
│   ├── cli-menu.sh              # Command-line argument parsing
│   ├── inject-templates.sh      # Index template injection
│   ├── k8s-utils.sh             # Kubernetes utilities
│   ├── profiling.sh             # Profiling utilities
│   ├── scenarios.sh             # Benchmark scenario implementations
│   └── workload-params.sh       # Workload parameter builders
├── gke-manifest/
│   ├── deploy-jvector-cluster.sh
│   ├── opensearch-cluster-faiss.yaml
│   ├── opensearch-cluster-lucene.yaml
│   └── opensearch-jvector-statefulset.yaml
└── results/                     # Benchmark results (gitignored)
```

## 🎯 Usage

### Basic Usage

Run benchmarks for all engines:
```bash
./run-benchmark.sh
```

### Command-Line Options

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
- Output: `results/multi-engine-results-{timestamp}/{engine}-metrics/scenario-1-create-index/`

### 2. Bulk Ingestion
- Loads vector data into the index
- Measures ingestion throughput and performance
- Output: `results/multi-engine-results-{timestamp}/{engine}-metrics/scenario-2-custom-vector-bulk/`

### 3. Force Merge
- Performs force merge operation on the index
- Optimizes segment structure
- Output: `results/multi-engine-results-{timestamp}/{engine}-metrics/scenario-force-merge-index/`

### 4. Search Concurrency Matrix
- Tests search performance with varying client counts (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
- Collects comprehensive metrics:
  - Throughput (ops/s)
  - Latency percentiles (p50, p90, p99, p99.9, p99.99, max)
  - Service time percentiles
  - Error rates
  - Recall metrics (recall@k, recall@1)
  - GC statistics
- Output: `results/multi-engine-results-{timestamp}/{engine}-metrics/scenario-search-only/`
  - Individual run logs in `clients-{N}/` subdirectories
  - Aggregated metrics in `summary.csv`

## 📈 Results Structure

```
results/
└── multi-engine-results-20260604-145009/
    ├── faiss-metrics/
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
    ├── lucene-metrics/
    └── jvector-metrics/
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
- **cohere-1m**: 768-dimensional vectors (default) - Uses OpenSearch Benchmark's built-in vectorsearch workload
- **msmarco**: 1024-dimensional vectors - Custom workload with automatic data download

#### Adding a New Dataset

To add a standard dataset (using built-in workload):
```yaml
datasets:
  your-dataset-name:
    dimension: 512
    format: hdf5
    description: "Your dataset description"
```

#### Adding a Custom Workload Dataset

For datasets requiring custom workloads and data files:

1. Create workload directory: `workloads/your-dataset/`
2. Add workload files: `workload.json`, `index.json`, `workload.py`
3. Configure in `config/datasets.yaml`:

```yaml
datasets:
  your-dataset:
    dimension: 1024
    format: fvec
    description: "Your custom dataset"
    custom_workload: "workloads/your-dataset"
    data_dir: "/datasets/your-dataset"
    data_files:
      - name: "vectors.fvec"
        url: "https://example.com/vectors.fvec"
        range: "0-4100000000"  # Optional: byte range for partial download
        size: "~4GB"           # Optional: human-readable size
      - name: "distances.fvec"
        url: "https://example.com/distances.fvec"
```

**How Custom Workloads Work:**
1. Framework detects custom workload configuration
2. Downloads data files to benchmark client pod (if not already present)
3. Copies workload directory to pod
4. Runs benchmark using custom workload path

**Example: MS MARCO Dataset**
The msmarco dataset demonstrates custom workload usage:
- Automatically downloads 3 data files (~4.1GB total)
- Uses custom workload in `workloads/msmarco/`
- Data stored in `/datasets/msmarco/` on benchmark client pod

### Engine-Specific Parameters

Workload parameters are defined in `lib/workload-params.sh`:
- Index settings (shards, replicas)
- Vector dimensions (automatically set based on selected dataset)
- Engine-specific configurations (ef_construction, m, etc.)
- Search parameters

## 🚢 Deployment

### GKE Deployment

Use the provided manifest files to deploy OpenSearch clusters:

```bash
# Deploy JVector cluster
./gke-manifest/deploy-jvector-cluster.sh

# Or apply manifests directly
kubectl apply -f gke-manifest/opensearch-cluster-faiss.yaml
kubectl apply -f gke-manifest/opensearch-cluster-lucene.yaml
kubectl apply -f gke-manifest/opensearch-jvector-statefulset.yaml
```

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