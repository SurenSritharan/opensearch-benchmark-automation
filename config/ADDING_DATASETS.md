# Adding New Datasets - Quick Guide

All dataset configuration lives in **[`config/datasets.yaml`](datasets.yaml)**

## Quick Start

1. **Create workload in repository**: Add workload files to the opensearch-benchmark-workloads repository
2. **Commit and push**: Push your workload to the repository
3. **Configure dataset**: Add entry to [`config/datasets.yaml`](datasets.yaml)
4. **Run benchmark**: `./run-benchmark.sh --dataset your-dataset-name`

---

## How It Works

1. **Workload Repository**: Your workload files must exist in the git repository (opensearch-benchmark-workloads)
2. **Auto-Update**: System pulls latest workload definitions before each run
3. **Data Files**: Auto-downloaded if `data_files` is specified in config
4. **Parameters**: Override workload templates using `default_params`
5. **Test Procedures**: Filtered by scenario (index, search, merge)

---

## Configuration Fields

**Required:**
- `workload_name` - Directory name in workload repository (must exist in repo)

**Optional (Recommended for Display):**
- `dimension` - Vector dimension (e.g., 768, 1024) - shown in dataset list
- `format` - Data format (hdf5, fvec, ivec, bigann) - shown in summary
- `space_type` - Distance metric (innerproduct, l2, cosinesimil) - shown in summary
- `description` - Human-readable description

**Optional (Functionality):**
- `data_files` - Auto-download files (no downloads if omitted)
- `data_dir` - Where to download files (default: `/datasets/{dataset_name}`)
- `default_params` - Override Jinja2 template variables (passed to workload)
- `param_files` - Engine-specific parameter file paths
- `test_procedures` - Filter by scenario, add parameter sweeps

**Note:** `dimension`, `format`, and `space_type` are for display only. Actual benchmark values come from workload parameter files or `default_params`.

---

## Configuration Examples

### Example 1: Dataset with Embedded Data (No Downloads)

**Prerequisites:** Workload `vectorsearch` exists in repository with required files

```yaml
datasets:
  cohere-1m:
    dimension: 768
    format: hdf5
    space_type: "innerproduct"
    description: "Cohere 1M embeddings"
    workload_name: "vectorsearch"        # Must exist in repo
    param_files:
      faiss: "params/faiss-cohere-1m-768-dp.json"
      lucene: "params/lucene-cohere-1m-768-dp.json"
      jvector: "params/jvector-cohere-1m-768-dp.json"
    test_procedures:
      - name: "create-index-only"
      - name: "search-only-no-warmup"
```

### Example 2: Dataset with External Data (Auto-Download)

**Prerequisites:** Workload `msmarco` exists in repository with:
- `workload.json`
- `params/jvector-params.json`
- `test_procedures/search-only.json`

```yaml
datasets:
  msmarco:
    dimension: 1024
    format: fvec
    space_type: "cosinesimil"
    description: "MS MARCO passage ranking"
    workload_name: "msmarco"             # Must exist in repo
    data_dir: "/datasets/msmarco"
    
    # Auto-download these files
    data_files:
      - name: "vectors.fvec"
        url: "https://example.com/vectors.fvec"
        range: "0-4100000000"            # Optional: byte range
    
    # Override template variables
    default_params:
      target_index_primary_shards: 3
      ef_construction: 128
      m: 16
    
    param_files:
      jvector: "params/jvector-params.json"
    
    # Parameter sweeps
    test_procedures:
      - name: "search-only"
        parameter_sweeps:
          - params: { query_clients: 1, query_count: 1000 }
          - params: { query_clients: 50, query_count: 25000 }
```

---

## Advanced Features

### Override Template Variables

Override any Jinja2 variable in the workload:

```yaml
default_params:
  target_index_primary_shards: 3
  target_index_replica_shards: 1
  ef_construction: 128
  m: 16
  query_k: 100
  warmup_iterations: 100
```

### Parameter Sweeps

Run the same test with different parameters:

```yaml
test_procedures:
  - name: "search-only"
    parameter_sweeps:
      - params: { query_clients: 1, query_count: 1000 }
      - params: { query_clients: 50, query_count: 25000 }
      - params: { query_clients: 100, query_count: 50000 }
```

### Test Procedure Filtering

Procedures are filtered by scenario name:

| Scenario | Matches | Example |
|----------|---------|---------|
| `index` | "index" or "ingest" | `create-index-only`, `bulk-ingest-only` |
| `search` | "search" | `search-only`, `search-only-no-warmup` |
| `merge` | "merge" | `force-merge`, `force-merge-index` |
| `all` | All procedures | Runs everything |

### Using a Custom Workload Repository

**Default repository:** `https://github.com/SurenSritharan/opensearch-benchmark-workloads.git`

**To use your own repository:**

1. Edit [`gke-manifest/opensearch-benchmark-client.yaml:53`](../gke-manifest/opensearch-benchmark-client.yaml#L53)
2. Replace the git clone URL with your repository
3. Redeploy: `kubectl delete pod opensearch-benchmark-client -n os-faiss && kubectl apply -f gke-manifest/opensearch-benchmark-client.yaml -n os-faiss`

**Note:** Keep destination path as `/datasets/opensearch-benchmark-workloads`

---

## Reference

**Distance Metrics:**
- `innerproduct` - Inner product (cosine similarity for normalized vectors)
- `l2` - Euclidean distance
- `cosinesimil` - Cosine similarity

**Data Formats:**
- `hdf5` - HDF5 format (common for large datasets)
- `fvec` - Float vector format
- `ivec` - Integer vector format (for indices)
- `bigann` - Big ANN format

**Troubleshooting:**
- Dataset not found → Check spelling/indentation in `datasets.yaml`
- Workload not found → Verify `workload_name` matches repo directory
- Download fails → Check URL, byte range, disk space
- Parameter errors → Validate YAML syntax, match template variables

---

## Summary

**To add a new dataset:**
1. Create workload in opensearch-benchmark-workloads repository
2. Commit and push workload files
3. Add dataset config to [`config/datasets.yaml`](datasets.yaml)
4. Run benchmark

**To use custom repository:**
- Edit [`opensearch-benchmark-client.yaml:53`](../gke-manifest/opensearch-benchmark-client.yaml#L53)
- Redeploy pod

**Remember:** Workload files must exist in the repository before running benchmarks!
