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
├── run_benchmark.py              # Main Python benchmark orchestrator
├── run-benchmark.sh              # Shell wrapper script
├── requirements.txt              # Python dependencies
├── lib/
│   ├── benchmark_executor.py    # Benchmark execution and OSB command management
│   ├── config_manager.py        # Configuration and argument parsing
│   ├── dataset_manager.py       # Dataset and workload management
│   └── profiling_manager.py     # Profiling orchestration
├── config/
│   ├── datasets.yaml            # Dataset configurations
│   └── cluster.yaml             # Cluster connection settings
├── workloads/
│   ├── vectorsearch/            # Official vectorsearch workload extensions
│   │   ├── indices/             # Custom index templates
│   │   │   ├── jvector-index.json
│   │   │   ├── faiss-index.json
│   │   │   └── lucene-index.json
│   │   ├── test_procedures/     # Custom test procedures
│   │   │   └── search-only-no-warmup.json
│   │   └── params/              # Workload parameters
│   │       ├── jvector-cohere-1m-768-dp.json
│   │       ├── faiss-cohere-1m-768-dp.json
│   │       └── lucene-cohere-1m-768-dp.json
│   └── msmarco/                 # Custom workload definition
│       ├── workload.json
│       ├── workload.py
│       ├── indices/             # Custom index templates
│       │   ├── jvector-index.json
│       │   ├── faiss-index.json
│       │   └── lucene-index.json
│       ├── params/              # Workload parameters
│       └── test_procedures/     # Test procedure definitions
├── gke-manifest/
│   ├── deploy-jvector-cluster.sh
│   ├── deploy-separated-cluster.sh
│   ├── opensearch-cluster-faiss.yaml
│   ├── opensearch-cluster-lucene.yaml
│   ├── opensearch-jvector-statefulset.yaml
│   └── opensearch-benchmark-client.yaml
├── scripts/
│   └── check-index-stats.sh    # Utility scripts
└── results/                     # Benchmark results (gitignored)
```

### Key Directories

- **`lib/`**: Core Python modules for benchmark orchestration
  - `benchmark_executor.py`: Manages OSB command execution and result collection
  - `config_manager.py`: Handles configuration loading and CLI argument parsing
  - `dataset_manager.py`: Manages datasets, workloads, and template injection
  - `profiling_manager.py`: Orchestrates profiling during benchmark runs
- **`config/`**: Configuration files
  - `datasets.yaml`: Dataset definitions (official and custom workloads)
  - `cluster.yaml`: Cluster connection settings
- **`workloads/`**: Workload definitions, parameters, and custom index templates
  - `{workload_name}/indices/`: Engine-specific index templates (jvector, faiss, lucene)
  - `{workload_name}/params/`: Workload parameter files
  - `{workload_name}/test_procedures/`: Custom test procedure definitions
  - For official workloads: Contains extensions (indices, params) to pre-installed workloads
  - For custom workloads: Contains complete workload definition (workload.json, workload.py, etc.)
- **`gke-manifest/`**: Kubernetes deployment manifests for OpenSearch clusters
- **`results/`**: Timestamped benchmark results and telemetry (gitignored)

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
- **cohere-1m**: 768-dimensional vectors (default) - Uses OpenSearch Benchmark's official vectorsearch workload with custom index templates
- **msmarco**: 1024-dimensional vectors - Custom workload with automatic data download

#### Dataset Types

The framework supports two types of datasets:

1. **Official Workloads** (`is_official: true`)
   - Uses pre-installed workloads from `/root/opensearch-benchmark-workloads/`
   - Supports custom index templates for engine-specific configurations (e.g., jvector)
   - Example: cohere-1m dataset

2. **Custom Workloads** (`is_official: false`)
   - Uses custom workload definitions from `workloads/` directory
   - Supports automatic data file downloads
   - Example: msmarco dataset

#### Adding an Official Workload Dataset

For datasets using OpenSearch Benchmark's official workloads:

```yaml
datasets:
  your-dataset:
    dimension: 768
    format: hdf5
    space_type: "innerproduct"
    description: "Your dataset description"
    workload_name: "vectorsearch"  # Official workload name
    is_official: true
    corpus_name: "your-corpus"     # Corpus name in the workload
    param_files:
      faiss: "params/faiss-your-dataset.json"
      lucene: "params/lucene-your-dataset.json"
      jvector: "params/jvector-your-dataset.json"
    test_procedures:  # Optional - override defaults
      index: "no-train-test-index-only"
      bulk: "no-train-test"
      search: "search-only"
```

**Custom Index Templates for Official Workloads:**

To use custom index templates with official workloads (e.g., to add jvector support):

1. Create indices directory: `workloads/{workload_name}/indices/`
2. Add engine-specific templates: `jvector-index.json`, `faiss-index.json`, `lucene-index.json`
3. Templates are automatically injected into the official workload at runtime

**Custom Test Procedures for Official Workloads:**

To customize test procedures (e.g., skip warmup tasks, modify schedules):

1. Create test_procedures directory: `workloads/{workload_name}/test_procedures/`
2. Add custom procedure files: `your-custom-procedure.json`
3. Reference in `config/datasets.yaml` under `test_procedures`
4. Procedures are automatically injected into the official workload at runtime

Example structure:
```
workloads/
└── vectorsearch/
    ├── indices/
    │   ├── jvector-index.json
    │   ├── faiss-index.json
    │   └── lucene-index.json
    ├── test_procedures/
    │   └── search-only-no-warmup.json
    └── params/
        ├── jvector-cohere-1m-768-dp.json
        ├── faiss-cohere-1m-768-dp.json
        └── lucene-cohere-1m-768-dp.json
```

**Custom Test Procedure Format:**

Test procedures must follow OpenSearch Benchmark's schema:

```json
{
    "name": "your-procedure-name",
    "default": false,
    "description": "Description of what this procedure does",
    "schedule": [
        {
            "name": "task-name",
            "operation": {
                "name": "operation-name",
                "operation-type": "vector-search",
                // ... operation parameters
            },
            "clients": 1
        }
    ]
}
```

**Example: Skip Warmup Task**

The `search-only-no-warmup.json` procedure demonstrates skipping the warmup-indices task:

```yaml
# In config/datasets.yaml
test_procedures:
  search: "search-only-no-warmup"  # Uses custom procedure
```

This allows you to go directly to search queries without warming up the index first.

The framework will:
- Use the official workload from `/root/opensearch-benchmark-workloads/vectorsearch/`
- Inject custom templates from `workloads/vectorsearch/indices/` into the official workload's `indices/` directory
- Inject custom test procedures from `workloads/vectorsearch/test_procedures/` into the official workload's `test_procedures/` directory
- Run benchmarks with `--workload-path=/root/opensearch-benchmark-workloads/vectorsearch`

#### Adding a Custom Workload Dataset

For datasets requiring custom workloads and data files:

1. Create workload directory: `workloads/your-dataset/`
2. Add workload files: `workload.json`, `workload.py`, test procedures
3. Configure in `config/datasets.yaml`:

```yaml
datasets:
  your-dataset:
    dimension: 1024
    format: fvec
    space_type: "cosinesimil"
    description: "Your custom dataset"
    workload_name: "your-dataset"
    is_official: false
    custom_workload: "workloads/your-dataset"
    data_dir: "/datasets/your-dataset"
    data_files:
      - name: "vectors.fvec"
        url: "https://example.com/vectors.fvec"
        range: "0-4100000000"  # Optional: byte range for partial download
        size: "~4GB"           # Optional: human-readable size
      - name: "distances.fvec"
        url: "https://example.com/distances.fvec"
    param_files:
      jvector: "params/jvector-params.json"
      faiss: "params/faiss-params.json"
      lucene: "params/lucene-params.json"
    test_procedures:
      index: "index-only"
      bulk: "bulk-ingest"
      search: "search-only"
```

**How Custom Workloads Work:**
1. Framework detects custom workload configuration (`is_official: false`)
2. Downloads data files to benchmark client pod (if not already present)
3. Copies workload directory to `/root/custom-workloads/{workload_name}/` on pod
4. Runs benchmark using `--workload-path=/root/custom-workloads/{workload_name}`

**Example: MS MARCO Dataset**
The msmarco dataset demonstrates custom workload usage:
- Automatically downloads 3 data files (~4.1GB total)
- Uses custom workload in `workloads/msmarco/`
- Data stored in `/datasets/msmarco/` on benchmark client pod
- Custom index templates in `config/index_templates/msmarco/`

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