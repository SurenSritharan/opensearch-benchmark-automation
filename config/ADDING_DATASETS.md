# Adding New Datasets - Quick Guide

This guide shows you how to add new datasets to the benchmark system in a standardized way.

## Quick Start

All dataset configuration is in `config/datasets.yaml`. Just copy the appropriate template below and fill in your values.

## Template 1: Official Workload Dataset

Use this for datasets from the official opensearch-benchmark-workloads repository.

```yaml
your-dataset-name:
  dimension: 768                    # Vector dimension
  format: hdf5                      # Data format
  space_type: "innerproduct"        # Distance metric
  description: "Your dataset description"
  workload_name: "vectorsearch"     # Always "vectorsearch" for official
  is_official: true                 # Mark as official
  corpus_name: "your-corpus"        # Corpus name in workload
  param_files:                      # Parameter files for each engine
    faiss: "params/corpus/1million/faiss-your-corpus-768-dp.json"
    lucene: "params/corpus/1million/lucene-your-corpus-768-dp.json"
    nmslib: "params/corpus/1million/nmslib-your-corpus-768-dp.json"
```

## Template 2: Custom Workload Dataset

Use this for your own custom datasets.

```yaml
your-dataset-name:
  dimension: 1024                   # Vector dimension
  format: fvec                      # Data format
  space_type: "cosinesimil"         # Distance metric
  description: "Your dataset description"
  workload_name: "your-workload"    # Your workload directory name
  is_official: false                # Mark as custom
  custom_workload: "workloads/your-workload"
  data_dir: "/datasets/your-dataset"
  data_files:                       # Optional: files to download
    - name: "vectors.fvec"
      url: "https://your-url.com/vectors.fvec"
```

## Default Test Procedures

The system uses these test procedures by default:
- **index**: `no-train-test-index-only` (create index + ingest data)
- **bulk**: `no-train-test` (index + force-merge + search)
- **search**: `search-only` (search on existing index)

These work for 99% of cases. Only override if you need special behavior.

## Optional: Override Test Procedures

```yaml
your-dataset-name:
  # ... other config ...
  test_procedures:
    index: "no-train-test-index-with-merge"  # Different index procedure
    bulk: "no-train-test-multisegment-queries"  # Test pre/post merge
    search: "search-only-with-prefetch"  # Enable prefetch
```

Available procedures: see `workloads/vectorsearch/test_procedures/default.json`

## Optional: Custom Test Procedures

Need to modify test behavior (e.g., skip warmup, change schedules)?

1. Create procedure file: `workloads/{workload_name}/test_procedures/your-procedure.json`
2. Reference in dataset config: `test_procedures: { search: "your-procedure" }`
3. System automatically injects it into the workload at runtime

**Test Procedure Format:**
```json
{
    "name": "search-only-no-warmup",
    "default": false,
    "description": "Search without warmup-indices task",
    "schedule": [
        {
            "name": "prod-queries",
            "operation": {
                "name": "prod-queries",
                "operation-type": "vector-search",
                // ... operation parameters
            },
            "clients": 1
        }
    ]
}
```

**Example: Skip Warmup Task**

Create `workloads/vectorsearch/test_procedures/search-only-no-warmup.json` and reference it:

```yaml
your-dataset:
  # ... other config ...
  test_procedures:
    search: "search-only-no-warmup"  # Uses custom procedure
```

This skips the warmup-indices task and goes directly to search queries.

## Optional: Custom Index Templates

To use custom index settings (e.g., different HNSW parameters):

1. Create template file: `workloads/{workload_name}/indices/{engine}-index.json`
2. System automatically injects it into the workload at runtime
3. No additional configuration needed!

Example: `config/index_templates/vectorsearch/jvector-index.json`
```json
{
  "settings": {
    "index": {
      "knn": true,
      "knn.algo_param.m": 16,
      "knn.algo_param.ef_construction": 100
    }
  },
  "mappings": {
    "properties": {
      "embedding": {
        "type": "knn_vector",
        "dimension": {{ vertex_dimension }},
        "method": {
          "name": "hnsw",
          "engine": "jvector"
        }
      }
    }
  }
}
```

## Complete Example: Adding Cohere 100k Dataset

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
    nmslib: "params/corpus/100k/nmslib-cohere-768-dp.json"
```

That's it! Run with:
```bash
./run-benchmark.sh --engine faiss --dataset cohere-100k --scenario all
```

## What Happens Automatically

When you add a dataset, the system automatically:
1. ✅ Loads configuration from datasets.yaml
2. ✅ Uses appropriate test procedures (defaults or custom)
3. ✅ Generates parameter files with custom index templates (if exist)
4. ✅ Passes parameters to all scenarios (index, bulk, search)
5. ✅ Displays OSB command for transparency
6. ✅ Cleans up temporary files

## Summary

**To add a new dataset:**
1. Copy appropriate template above
2. Fill in your values
3. Add to `config/datasets.yaml`
4. Done!

**Optional customizations:**
- Custom index templates: `config/index_templates/vectorsearch/{engine}-index.json`
- Custom test procedures: Add `test_procedures` section to dataset config

Everything else is handled automatically!