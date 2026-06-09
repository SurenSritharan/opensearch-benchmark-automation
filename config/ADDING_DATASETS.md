# Adding New Datasets - Complete Guide

This guide explains how to add new datasets to the OpenSearch Benchmark automation system.

## Table of Contents
1. [Quick Start](#quick-start)
2. [Understanding the System](#understanding-the-system)
3. [Understanding Dataset Types](#understanding-dataset-types)
4. [Step-by-Step: Adding a Dataset from Workload Repository](#step-by-step-adding-a-dataset-from-workload-repository)
5. [Advanced Customization](#advanced-customization)
6. [Reference](#reference)

---

## Quick Start

**All dataset configuration lives in one file: `config/datasets.yaml`**

To add a new dataset:
1. Ensure the workload exists in the opensearch-benchmark-workloads repository
2. Copy the appropriate template below
3. Fill in your values
4. Add to `config/datasets.yaml`
5. Run: `./run-benchmark.sh --dataset your-dataset-name`

---

## Understanding the System

### How Workloads Are Managed

The system uses a **cloned git repository** approach:

1. **Workload Repository**: All workloads are stored in a git repository (default: `opensearch-benchmark-workloads`)
2. **Automatic Updates**: The system automatically pulls the latest workload definitions from the repository before each benchmark run
3. **No Local Copies**: You don't need to manually copy workload files - they're fetched from the repository

**Using a Custom Repository:**

To use your own workload repository instead of the default:

1. Edit `gke-manifest/opensearch-benchmark-client.yaml`
2. Find line 53 with the git clone command:
   ```yaml
   git clone --depth 1 https://github.com/SurenSritharan/opensearch-benchmark-workloads.git /datasets/opensearch-benchmark-workloads
   ```
3. Replace the URL with your repository:
   ```yaml
   git clone --depth 1 https://github.com/your-org/your-workloads-repo.git /datasets/opensearch-benchmark-workloads
   ```
4. Redeploy the benchmark client pod

**Note:** The repository must be cloned to `/datasets/opensearch-benchmark-workloads` as this path is used throughout the system.

### How Data Files Are Handled

Data files are **automatically downloaded** when needed:

- If your dataset configuration includes a `data_files` section, the system will automatically download the files
- Files are downloaded to the specified `data_dir` location
- The system checks if files already exist and have the correct size before downloading
- You can specify byte ranges for partial downloads

### How Configuration Works

The `datasets.yaml` file provides three key capabilities:

1. **Template Variable Overrides**: Use `default_params` to override Jinja2 template variables in the workload
2. **Test Procedure Selection**: Use `test_procedures` to specify which test procedures to run and filter by scenario
3. **Parameter Sweeps**: Define multiple parameter combinations for a single test procedure

---

## Understanding Dataset Types

### Datasets with Embedded Data
- Use workloads where data is already included in the repository
- No `data_files` section needed
- Examples: cohere-1m (if data is in the workload)

### Datasets with External Data
- Use workloads that reference external data files
- Include `data_files` section with download URLs
- System automatically downloads data before benchmarking
- Examples: msmarco with S3-hosted data files

---

## Step-by-Step: Adding a Dataset from Workload Repository

### 1. Ensure Workload Exists in Repository

First, verify that your workload exists in the `opensearch-benchmark-workloads` repository:
- Check the repository for your workload directory
- Ensure it has the required structure (workload.json, test procedures, parameter files)
- Note the workload name (directory name)

### 2. Identify Your Dataset Properties

Gather this information:
- **Vector dimension**: e.g., 768, 1024
- **Data format**: hdf5, fvec, etc.
- **Distance metric**: innerproduct, l2, cosinesimil
- **Workload name**: The directory name in opensearch-benchmark-workloads
- **Parameter files**: Paths to engine-specific params in the workload
- **Test procedures**: Names of test procedures you want to run
- **Data files** (if needed): URLs and download information

### 3. Add Configuration to `config/datasets.yaml`

#### Example 1: Dataset with Embedded Data (No Downloads)

```yaml
datasets:
  cohere-1m:
    dimension: 768
    format: hdf5
    space_type: "innerproduct"
    description: "Cohere 1M embeddings dataset"
    workload_name: "vectorsearch"        # Must match repository folder name
    corpus_name: "cohere-1m"
    param_files:
      faiss: "params/faiss-cohere-1m-768-dp.json"
      lucene: "params/lucene-cohere-1m-768-dp.json"
      jvector: "params/jvector-cohere-1m-768-dp.json"
    test_procedures:
      - name: "create-index-only"
      - name: "bulk-ingest-only"
      - name: "search-only-no-warmup"
```

#### Example 2: Dataset with External Data (Automatic Downloads)

```yaml
datasets:
  msmarco:
    dimension: 1024
    format: fvec
    space_type: "cosinesimil"
    description: "MS MARCO passage ranking dataset"
    workload_name: "msmarco"             # Must match repository folder name
    data_dir: "/datasets/msmarco"        # Where to download data files
    
    # Data files to download automatically
    data_files:
      - name: "cohere_msmarco_base.fvec"
        url: "https://example.com/data.fvec"
        range: "0-4100000000"            # Optional: byte range for partial download
        size: "~4.1GB"                   # Optional: human-readable size
      - name: "cohere_msmarco_queries.fvec"
        url: "https://example.com/queries.fvec"
    
    # Override Jinja2 template variables in the workload
    default_params:
      target_index_primary_shards: 3
      target_index_replica_shards: 1
      ef_construction: 128
      m: 16
    
    param_files:
      jvector: "params/jvector-params.json"
      faiss: "params/faiss-params.json"
      lucene: "params/lucene-params.json"
    
    # Test procedures with parameter sweeps
    test_procedures:
      - name: "create-index-only"
      - name: "bulk-ingest-only"
      - name: "search-only"
        parameter_sweeps:
          - params:
              query_clients: 1
              query_count: 1000
          - params:
              query_clients: 50
              query_count: 25000
```

### 4. Understanding the Configuration

**Required Fields:**
- `dimension`: Vector dimension
- `format`: Data format (hdf5, fvec, etc.)
- `space_type`: Distance metric
- `description`: Human-readable description
- `workload_name`: Directory name in opensearch-benchmark-workloads repository

**Optional Fields:**
- `data_files`: List of files to download automatically (if not present, no downloads occur)
- `data_dir`: Directory where data files will be downloaded
- `default_params`: Override Jinja2 template variables in the workload
- `param_files`: Engine-specific parameter file paths
- `test_procedures`: List of test procedures to run (can include parameter sweeps)

### 5. Test Your Configuration

```bash
# Test with a single engine and scenario
./run-benchmark.sh --engine faiss --dataset your-dataset-name --scenario search

# Test all engines and scenarios
./run-benchmark.sh --engine all --dataset your-dataset-name --scenario all
```

### 6. What Happens Automatically

When you run a benchmark:

1. **Repository Update**: System pulls latest workload definitions from git
2. **Data Download** (if `data_files` exists): System downloads missing or outdated files
3. **Parameter Merging**: `default_params` are merged with workload parameters
4. **Procedure Filtering**: Only test procedures matching your scenario are executed
5. **Parameter Sweeps**: If defined, multiple runs with different parameters are executed

---

## Advanced Customization

### Overriding Workload Template Variables

The workload repository uses Jinja2 templates for flexibility. You can override any template variable using `default_params`:

```yaml
your-dataset:
  workload_name: "vectorsearch"
  default_params:
    # Index settings
    target_index_primary_shards: 3
    target_index_replica_shards: 1
    
    # HNSW parameters
    ef_construction: 128
    m: 16
    ef_search: 100
    
    # Bulk ingestion
    target_index_bulk_size: 100
    target_index_bulk_index_clients: 1
    
    # Query parameters
    query_k: 100
    query_clients: 1
    warmup_iterations: 100
```

**How it works:**
- These parameters are merged with the workload's parameter files
- They override any matching variables in the Jinja2 templates
- You can override any template variable defined in the workload

### Selecting and Filtering Test Procedures

Test procedures are defined in the workload repository. You can select which ones to run:

```yaml
your-dataset:
  workload_name: "msmarco"
  test_procedures:
    - name: "create-index-only"        # Runs when scenario=index
    - name: "bulk-ingest-only"         # Runs when scenario=index
    - name: "force-merge"              # Runs when scenario=merge
    - name: "search-only"              # Runs when scenario=search
```

**Filtering by scenario:**
- When you run `--scenario index`, only procedures with "index" or "ingest" in the name run
- When you run `--scenario search`, only procedures with "search" in the name run
- When you run `--scenario merge`, only procedures with "merge" in the name run
- When you run `--scenario all`, all procedures run

### Parameter Sweeps

Run the same test procedure multiple times with different parameters:

```yaml
your-dataset:
  test_procedures:
    - name: "search-only"
      parameter_sweeps:
        - params:
            query_clients: 1
            query_count: 1000
            warmup_iterations: 100
        - params:
            query_clients: 50
            query_count: 25000
            warmup_iterations: 100
        - params:
            query_clients: 100
            query_count: 50000
            warmup_iterations: 100
```

**How it works:**
- Each entry in `parameter_sweeps` creates a separate benchmark run
- Parameters are merged with `default_params` and workload parameters
- Useful for testing different client counts, query volumes, etc.

### Automatic Data Downloads

Specify data files to download automatically before benchmarking:

```yaml
your-dataset:
  data_dir: "/datasets/your-dataset"
  data_files:
    - name: "vectors.fvec"
      url: "https://example.com/vectors.fvec"
      range: "0-4100000000"              # Optional: download specific byte range
      size: "~4.1GB"                     # Optional: human-readable size for display
    - name: "queries.fvec"
      url: "https://example.com/queries.fvec"
```

**How it works:**
- System checks if files exist and have correct size
- Only downloads if file is missing or size doesn't match
- Supports byte range downloads for partial files
- Shows progress and file sizes during download

---

## Reference

### How Test Procedure Filtering Works

Test procedures are filtered based on their names:

| Scenario | Procedure Name Pattern | Examples |
|----------|----------------------|----------|
| `index` | Contains "index" or "ingest" | `create-index-only`, `bulk-ingest-only` |
| `search` | Contains "search" | `search-only`, `search-only-no-warmup` |
| `merge` | Contains "merge" or "force-merge" | `force-merge`, `force-merge-index` |
| `all` | All procedures | Runs everything |

### Available Distance Metrics

- `innerproduct` - Inner product (cosine similarity for normalized vectors)
- `l2` - Euclidean distance
- `cosinesimil` - Cosine similarity

### Supported Data Formats

- `hdf5` - HDF5 format (common for large datasets)
- `fvec` - Float vector format
- `bigann` - Big ANN format
- `ivec` - Integer vector format (for indices)

### Command Line Options

```bash
# Basic usage
./run-benchmark.sh --engine <engine> --dataset <dataset> --scenario <scenario>

# With custom parameters
./run-benchmark.sh \
  --engine faiss \
  --dataset your-dataset \
  --scenario search \
  --clients 10,50,100 \
  --queries 10000 \
  --disable-profiling
```

### What Happens Automatically

When you run a benchmark, the system:
1. ✅ Pulls latest workload definitions from git repository
2. ✅ Shows current commit for verification
3. ✅ Downloads data files (if `data_files` is specified)
4. ✅ Checks file sizes and skips if already downloaded
5. ✅ Loads configuration from `datasets.yaml`
6. ✅ Filters test procedures based on scenario
7. ✅ Merges `default_params` with workload parameters
8. ✅ Executes parameter sweeps (if defined)
9. ✅ Displays OSB command for transparency
10. ✅ Collects metrics and generates reports

### Troubleshooting

**Dataset not found:**
- Check spelling in `datasets.yaml`
- Ensure proper YAML indentation

**Workload not found in repository:**
- Verify `workload_name` matches directory name in opensearch-benchmark-workloads
- Check that repository was cloned correctly
- Look for git pull errors in output

**Data download failures:**
- Check URL accessibility
- Verify byte range is correct (if specified)
- Ensure sufficient disk space in `data_dir`
- Check network connectivity from benchmark pod

**Parameter errors:**
- Validate YAML syntax in `datasets.yaml`
- Ensure parameter names match Jinja2 template variables in workload
- Check that all required parameters are present

**Pod connection errors:**
- Ensure benchmark client pod is running: `kubectl get pods`
- Check namespace matches: `kubectl get pods -n os-<engine>`

---

## Using a Custom Workload Repository

If you want to use your own workload repository instead of the default:

### Step 1: Prepare Your Repository

Your repository should have this structure:
```
your-workloads-repo/
├── workload1/
│   ├── workload.json
│   ├── params/
│   ├── test_procedures/
│   └── indices/
├── workload2/
│   ├── workload.json
│   ├── params/
│   ├── test_procedures/
│   └── indices/
└── ...
```

### Step 2: Update Benchmark Client Configuration

Edit `gke-manifest/opensearch-benchmark-client.yaml`:

```yaml
# Find this line (around line 53):
git clone --depth 1 https://github.com/SurenSritharan/opensearch-benchmark-workloads.git /datasets/opensearch-benchmark-workloads

# Replace with your repository:
git clone --depth 1 https://github.com/your-org/your-workloads-repo.git /datasets/opensearch-benchmark-workloads
```

**Important:** Keep the destination path as `/datasets/opensearch-benchmark-workloads` - this is hardcoded in the system.

### Step 3: Redeploy Benchmark Client

```bash
# Delete existing benchmark client pod
kubectl delete pod opensearch-benchmark-client -n os-faiss

# Redeploy with new configuration
kubectl apply -f gke-manifest/opensearch-benchmark-client.yaml -n os-faiss
```

### Step 4: Configure Your Datasets

Add your datasets to `config/datasets.yaml` referencing workloads in your repository:

```yaml
datasets:
  my-custom-dataset:
    dimension: 1024
    format: hdf5
    space_type: "l2"
    description: "My custom dataset"
    workload_name: "my-workload"  # Must match directory name in your repo
    param_files:
      faiss: "params/faiss-params.json"
      lucene: "params/lucene-params.json"
      jvector: "params/jvector-params.json"
    test_procedures:
      - name: "create-index-only"
      - name: "search-only"
```

### Step 5: Run Benchmarks

```bash
./run-benchmark.sh --engine faiss --dataset my-custom-dataset --scenario all
```

The system will automatically pull the latest changes from your repository before each run.

---

## Summary

**To add a new dataset:**

1. **Ensure workload exists** in your workload repository (default or custom)
2. **Add entry to `config/datasets.yaml`** with:
   - Basic properties (dimension, format, space_type, description)
   - `workload_name` matching the repository directory
   - `param_files` for each engine
   - `test_procedures` list (optional - for filtering)
3. **Add data downloads** (optional):
   - Include `data_files` section if data needs to be downloaded
   - System automatically downloads missing files
4. **Override parameters** (optional):
   - Use `default_params` to override Jinja2 template variables
   - Use `parameter_sweeps` for multiple test runs
5. **Run benchmark**: `./run-benchmark.sh --dataset your-dataset-name`

**To use a custom workload repository:**
1. Edit `gke-manifest/opensearch-benchmark-client.yaml` to change the git clone URL
2. Redeploy the benchmark client pod
3. Configure datasets referencing workloads in your repository

**Key concepts:**
- ✅ Workloads are cloned from git repository (no manual copying)
- ✅ You can use your own repository by editing the benchmark client YAML
- ✅ Data downloads are automatic (if `data_files` is specified)
- ✅ Template variables can be overridden via `default_params`
- ✅ Test procedures are filtered by scenario type
- ✅ Parameter sweeps enable multiple runs with different settings

Everything else is handled automatically by the system!