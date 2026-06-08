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

## 📊 Benchmark Scenarios

### 1. Create Index
- Creates target index with engine-specific configurations
- Validates index mapping and field types

### 2. Bulk Ingestion
- Loads vector data into the index
- Measures ingestion throughput and performance

### 3. Force Merge
- Performs force merge operation on the index
- Optimizes segment structure

### 4. Search Concurrency Matrix
- Tests search performance with varying client counts (10, 20, 30, 40, 50, 60, 70, 80, 90, 100)

## 🔧 Configuration

### Global Configuration

Edit `config/cluster.yaml` to modify default behavior:

### Profiling Features

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

#### Adding a Workload Dataset

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
