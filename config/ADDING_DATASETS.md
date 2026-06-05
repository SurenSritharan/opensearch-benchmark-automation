# Adding New Datasets - Complete Guide

This guide explains how to add new datasets to the OpenSearch Benchmark automation system.

## Table of Contents
1. [Quick Start](#quick-start)
2. [Understanding Dataset Types](#understanding-dataset-types)
3. [Step-by-Step: Adding an Official Dataset](#step-by-step-adding-an-official-dataset)
4. [Step-by-Step: Adding a Custom Dataset](#step-by-step-adding-a-custom-dataset)
5. [Advanced Customization](#advanced-customization)
6. [Reference](#reference)

---

## Quick Start

**All dataset configuration lives in one file: `config/datasets.yaml`**

To add a new dataset:
1. Choose your dataset type (official or custom)
2. Copy the appropriate template below
3. Fill in your values
4. Add to `config/datasets.yaml`
5. Run: `./run-benchmark.sh --dataset your-dataset-name`

---

## Understanding Dataset Types

### Official Datasets
- Use the pre-installed `vectorsearch` workload from opensearch-benchmark-workloads
- Data is already available in the workload
- Examples: cohere-1m, cohere-100k

### Custom Datasets
- Use your own workload directory under `workloads/`
- You provide the data files and workload definition
- Examples: msmarco, your-custom-dataset

---

## Step-by-Step: Adding an Official Dataset

### 1. Identify Your Dataset Properties

Gather this information:
- **Vector dimension**: e.g., 768, 1024
- **Data format**: hdf5, fvec, etc.
- **Distance metric**: innerproduct, l2, cosinesimil
- **Corpus name**: The name used in the vectorsearch workload
- **Parameter files**: Paths to engine-specific params in the workload

### 2. Add Configuration to `config/datasets.yaml`

```yaml
datasets:
  # ... existing datasets ...
  
  your-dataset-name:
    dimension: 768                    # Vector dimension
    format: hdf5                      # Data format (hdf5, fvec, etc.)
    space_type: "innerproduct"        # Distance metric
    description: "Your dataset description"
    workload_name: "vectorsearch"     # Always "vectorsearch" for official
    is_official: true                 # Mark as official workload
    corpus_name: "your-corpus"        # Corpus name in vectorsearch workload
    param_files:                      # Parameter files for each engine
      faiss: "params/corpus/1million/faiss-your-corpus-768-dp.json"
      lucene: "params/corpus/1million/lucene-your-corpus-768-dp.json"
      jvector: "params/corpus/1million/jvector-your-corpus-768-dp.json"
```

### 3. Test Your Configuration

```bash
# Test with a single engine first
./run-benchmark.sh --engine faiss --dataset your-dataset-name --scenario search

# If successful, test all engines
./run-benchmark.sh --engine all --dataset your-dataset-name --scenario all
```

### Example: Cohere 100k Dataset

```yaml
cohere-100k:
  dimension: 768
  format: hdf5
  space_type: "innerproduct"
  description: "Cohere 100k embeddings dataset"
  workload_name: "vectorsearch"
  is_official: true
  corpus_name: "cohere-100k"
  param_files:
    faiss: "params/corpus/100k/faiss-cohere-768-dp.json"
    lucene: "params/corpus/100k/lucene-cohere-768-dp.json"
    jvector: "params/corpus/100k/jvector-cohere-768-dp.json"
```

---

## Step-by-Step: Adding a Custom Dataset

### 1. Create Workload Directory Structure

```bash
mkdir -p workloads/your-workload/{indices,params,test_procedures}
```

### 2. Create Workload Definition

Create `workloads/your-workload/workload.json`:

```json
{
  "description": "Your workload description",
  "indices": [
    {
      "name": "your_index",
      "body": "indices/index.json"
    }
  ],
  "corpora": [
    {
      "name": "your-corpus",
      "documents": [
        {
          "source-file": "/path/to/your/data.json",
          "document-count": 1000000,
          "uncompressed-bytes": 5000000000
        }
      ]
    }
  ]
}
```

### 3. Create Index Templates

Create engine-specific index templates in `workloads/your-workload/indices/`:

**`faiss-index.json`:**
```json
{
  "settings": {
    "index": {
      "knn": true,
      "knn.algo_param.ef_search": 100
    }
  },
  "mappings": {
    "properties": {
      "embedding": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "engine": "faiss",
          "space_type": "l2"
        }
      }
    }
  }
}
```

Repeat for `lucene-index.json` and `jvector-index.json`.

### 4. Create Parameter Files

Create `workloads/your-workload/params/faiss-params.json`:

```json
{
  "target_index_name": "faiss_index",
  "target_field_name": "embedding",
  "target_index_body": "indices/faiss-index.json",
  "target_index_dimension": 1024,
  "target_index_space_type": "l2",
  "target_index_bulk_size": 100,
  "target_index_bulk_index_data_set_format": "hdf5",
  "target_index_bulk_index_data_set_path": "/path/to/data.hdf5",
  "target_index_bulk_index_clients": 1,
  "hnsw_ef_search": 100,
  "hnsw_ef_construction": 512,
  "hnsw_m": 16,
  "query_k": 100,
  "query_clients": 1,
  "query_data_set_format": "hdf5",
  "query_data_set_path": "/path/to/queries.hdf5"
}
```

Repeat for other engines.

### 5. Add Configuration to `config/datasets.yaml`

```yaml
datasets:
  # ... existing datasets ...
  
  your-dataset-name:
    dimension: 1024                   # Vector dimension
    format: hdf5                      # Data format
    space_type: "l2"                  # Distance metric
    description: "Your custom dataset"
    workload_name: "your-workload"    # Your workload directory name
    is_official: false                # Mark as custom
    custom_workload: "workloads/your-workload"
    data_dir: "/datasets/your-dataset"
```

### 6. Test Your Configuration

```bash
./run-benchmark.sh --engine faiss --dataset your-dataset-name --scenario all
```

### Example: MS MARCO Dataset

```yaml
msmarco:
  dimension: 1024
  format: fvec
  space_type: "cosinesimil"
  description: "MS MARCO passage ranking dataset"
  workload_name: "msmarco"
  is_official: false
  custom_workload: "workloads/msmarco"
  data_dir: "/datasets/msmarco"
```

---

## Advanced Customization

### Custom Test Procedures

**When to use:** You need to modify test behavior (skip warmup, change schedules, etc.)

**Steps:**

1. Create procedure file: `workloads/{workload_name}/test_procedures/your-procedure.json`

```json
{
  "name": "search-only-no-warmup",
  "default": false,
  "description": "Search without warmup phase",
  "schedule": [
    {
      "name": "prod-queries",
      "operation": {
        "name": "prod-queries",
        "operation-type": "vector-search",
        "param-source": "custom-vector-source"
      },
      "clients": 1
    }
  ]
}
```

2. Reference in dataset config:

```yaml
your-dataset:
  # ... other config ...
  test_procedures:
    index: "no-train-test-index-only"      # Index creation
    bulk: "no-train-test"                   # Bulk ingestion
    search: "search-only-no-warmup"         # Your custom procedure
```

### Custom Index Settings

**When to use:** You want different HNSW parameters, shard counts, etc.

**Steps:**

1. Create template: `workloads/{workload_name}/indices/{engine}-index.json`

```json
{
  "settings": {
    "index": {
      "knn": true,
      "knn.algo_param.m": 32,              # Custom M value
      "knn.algo_param.ef_construction": 200, # Custom ef_construction
      "number_of_shards": 3,
      "number_of_replicas": 1
    }
  },
  "mappings": {
    "properties": {
      "embedding": {
        "type": "knn_vector",
        "dimension": 768,
        "method": {
          "name": "hnsw",
          "engine": "jvector",
          "space_type": "l2",
          "parameters": {
            "m": 32,
            "ef_construction": 200
          }
        }
      }
    }
  }
}
```

2. No additional configuration needed - the system automatically uses it!

---

## Reference

### Default Test Procedures

The system uses these by default (no configuration needed):

| Scenario | Default Procedure | Description |
|----------|------------------|-------------|
| `index` | `no-train-test-index-only` | Create index + ingest data |
| `bulk` | `no-train-test` | Index + force-merge + search |
| `search` | `search-only` | Search on existing index |

### Available Distance Metrics

- `innerproduct` - Inner product (cosine similarity for normalized vectors)
- `l2` - Euclidean distance
- `cosinesimil` - Cosine similarity

### Supported Data Formats

- `hdf5` - HDF5 format (common for large datasets)
- `fvec` - Float vector format
- `bigann` - Big ANN format

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

When you add a dataset, the system:
1. ✅ Loads configuration from `datasets.yaml`
2. ✅ Selects appropriate test procedures (defaults or custom)
3. ✅ Generates parameter files with runtime values
4. ✅ Injects custom index templates (if they exist)
5. ✅ Copies workload files to benchmark client pod
6. ✅ Passes parameters to all scenarios
7. ✅ Displays OSB command for transparency
8. ✅ Cleans up temporary files

### Troubleshooting

**Dataset not found:**
- Check spelling in `datasets.yaml`
- Ensure proper YAML indentation

**Workload files not found:**
- Verify `workload_name` matches directory name
- Check file paths in workload.json

**Parameter errors:**
- Validate JSON syntax in parameter files
- Ensure all required parameters are present

**Pod connection errors:**
- Ensure benchmark client pod is running: `kubectl get pods`
- Check namespace matches: `kubectl get pods -n os-<engine>`

---

## Summary

**To add a new dataset:**

1. **Official Dataset:**
   - Add entry to `config/datasets.yaml` with `is_official: true`
   - Specify corpus name and parameter file paths
   - Done!

2. **Custom Dataset:**
   - Create workload directory structure
   - Add workload.json, index templates, and parameter files
   - Add entry to `config/datasets.yaml` with `is_official: false`
   - Done!

**Optional customizations:**
- Custom index templates: Add to `workloads/{workload}/indices/`
- Custom test procedures: Add to `workloads/{workload}/test_procedures/`
- Override defaults: Add `test_procedures` section to dataset config

Everything else is handled automatically by the system!