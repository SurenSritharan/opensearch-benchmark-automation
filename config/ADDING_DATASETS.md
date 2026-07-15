# Adding New Datasets

All dataset configuration lives in **[`config/datasets.yaml`](datasets.yaml)**.

The API server reads this file at startup and whenever `/api/v1/sync` is called, so changes take effect immediately after a Git push + sync — no pod restart needed.

---

## Quick Start

1. Add workload files to the [opensearch-benchmark-workloads](https://github.com/SurenSritharan/opensearch-benchmark-workloads) repository
2. Add a dataset entry to [`config/datasets.yaml`](datasets.yaml)
3. `git push origin main`
4. Click **"🔄 Sync from Git"** in the Web UI (or `POST /api/v1/sync`)
5. The new dataset appears in the dropdown immediately

To trigger a benchmark from Jenkins, use the [benchmark-service-runner](http://os-perf-jenkins.dev.fyre.ibm.com:8080/job/benchmark-service-runner/) pipeline and select the dataset.

---

## Configuration Fields

### Top-level fields

| Field | Required | Description |
|-------|----------|-------------|
| `workload` | ✅ | Directory name in the workloads repository (must match exactly) |
| `dimension` | | Vector dimension — display only |
| `format` | | Data format (`hdf5`, `fvec`, etc.) — display only |
| `space_type` | | Distance metric (`innerproduct`, `l2`, `cosinesimil`) — display only |
| `description` | | Human-readable description |
| `supported_corpus_sizes` | | List of valid corpus size strings (e.g. `["1m", "5m"]`) |
| `supported_k_values` | | Valid `k` values for ground-truth recall (e.g. `[10, 100]`) |
| `data_dir` | | Where data files are stored on the worker pod |
| `base_url` | | Base URL template for `data_files` entries |
| `data_files` | | Files to download to `data_dir` before benchmarking |
| `gcs_cache_files` | | GCS-hosted corpus files pre-seeded by Jenkins before each run |
| `common_params` | | Parameters merged into every test procedure for this dataset |
| `engine_params` | | Per-engine parameters merged in alongside `common_params` |
| `test_procedures` | | List of test procedure definitions (see below) |

### `test_procedures` fields

| Field | Description |
|-------|-------------|
| `name` | Test procedure name — must match a procedure in the workload |
| `params` | Parameters passed to this procedure (merged with `common_params` and `engine_params`) |
| `engine_params` | Per-engine parameter overrides for this specific procedure |
| `parameter_sweeps` | List of `{ params: {...} }` objects — runs the procedure once per sweep |

### `gcs_cache_files` fields

| Field | Description |
|-------|-------------|
| `corpus_size` | Corpus size this file belongs to (must match a value in `supported_corpus_sizes`) |
| `gcs_path` | Full `gs://` path to the object in GCS |
| `target_path` | Absolute path on the worker pod where the file should be placed |

The Jenkins **Seed Dataset Cache** stage reads these entries and downloads only the files needed for the selected `CORPUS_SIZE` — skipping the download if the file already exists on the worker PVC.

---

## Parameter Merging

For each benchmark run, parameters are merged in this order (later entries win):

```
common_params
  + engine_params[engine]          (dataset-level)
    + test_procedure.params
      + test_procedure.engine_params[engine]
        + parameter_sweep.params   (if applicable)
```

Template variables like `{{corpus_size}}` and `{{corpus_name}}` are substituted at runtime by the benchmark runner.

---

## Examples

### Example 1: HDF5 dataset with GCS-hosted corpus

Corpus files are too large for CloudFront so they live in GCS and are pre-seeded to worker PVCs by Jenkins.

```yaml
datasets:
  my-dataset-768:
    dimension: 768
    format: hdf5
    space_type: "innerproduct"
    description: "My 768-dim dataset"
    workload: "vectorsearch"          # must match opensearch-benchmark-workloads/vectorsearch/

    supported_corpus_sizes: ["1m", "5m"]

    gcs_cache_files:
      - corpus_size: "5m"
        gcs_path: "gs://opensearch-benchmark-datasets/my-dataset/my-dataset-5m.hdf5"
        target_path: "/datasets/opensearch-benchmark/.osb/benchmarks/data/my-dataset-5m/my-dataset-5m.hdf5"

    common_params:
      target_index_name: "my-dataset-768-{{corpus_size}}"
      target_field_name: "target_field"
      corpus_name: "my-dataset-{{corpus_size}}"

    engine_params:
      faiss:
        engine: "faiss"
      lucene:
        engine: "lucene"
      jvector:
        engine: "jvector"

    test_procedures:
      - name: "create-index"
        params:
          target_index_body: "indices/index.json"
          target_index_primary_shards: 3
          target_index_replica_shards: 1
          target_index_dimension: 768
          target_index_space_type: "innerproduct"
          hnsw_ef_construction: 256
          hnsw_m: 32
        engine_params:
          jvector:
            method_name: "disk_ann"
            mode: "in_memory"
          faiss:
            method_name: "hnsw"
          lucene:
            method_name: "hnsw"

      - name: "bulk-ingest-data"
        params:
          target_index_bulk_size: 500
          target_index_bulk_index_data_set_format: "hdf5"
          target_index_bulk_index_data_set_corpus: "{{corpus_name}}"
          target_index_bulk_indexing_clients: 8

      - name: "force-merge"
        params:
          target_index_max_num_segments: 1

      - name: "vector-search"
        params:
          query_data_set_format: "hdf5"
          query_data_set_corpus: "{{corpus_name}}"
          neighbors_data_set_format: "hdf5"
          neighbors_data_set_corpus: "{{corpus_name}}"
          warmup_time_period: 60
          time_period: 300
          hnsw_ef_search: 128
        parameter_sweeps:
          - params: { search_clients: 4 }
          - params: { search_clients: 8 }
          - params: { search_clients: 10 }
          - params: { search_clients: 16 }
```

### Example 2: fvec dataset with downloadable data files

Data files are downloaded from a URL to the worker pod's `data_dir` before benchmarking starts.

```yaml
datasets:
  my-dataset-1024:
    dimension: 1024
    format: fvec
    space_type: "cosinesimil"
    description: "My 1024-dim fvec dataset"
    workload: "msmarco"               # must match opensearch-benchmark-workloads/msmarco/
    data_dir: "/datasets/my-dataset"

    supported_corpus_sizes: ["1m", "5m"]

    base_url: "https://my-bucket.s3.amazonaws.com/my-dataset"

    data_files:
      - name: "base.fvec"
        url: "{{base_url}}/base.fvec"
      - name: "queries.fvec"
        url: "{{base_url}}/queries.fvec"

    common_params:
      target_index_name: "my-dataset-1024-{{corpus_size}}"

    engine_params:
      faiss:
        engine: "faiss"
      lucene:
        engine: "lucene"
      jvector:
        engine: "jvector"

    test_procedures:
      - name: "create-index"
        params:
          target_index_body: "indices/index.json"
          target_index_primary_shards: 3
          target_index_replica_shards: 1
          target_index_dimension: 1024
          target_field_name: "vector"
          target_index_space_type: "cosinesimil"
          hnsw_ef_construction: 256
          hnsw_m: 32
        engine_params:
          jvector:
            method_name: "disk_ann"
            mode: "in_memory"
          faiss:
            method_name: "hnsw"
          lucene:
            method_name: "hnsw"

      - name: "bulk-ingest-data"
        params:
          target_index_bulk_size: 5000
          target_index_bulk_index_data_set_path: "/datasets/my-dataset/base_{{corpus_size}}.fvec"
          target_index_bulk_indexing_clients: 8

      - name: "force-merge"
        params:
          target_index_max_num_segments: 1

      - name: "vector-search"
        params:
          queries_file: "/datasets/my-dataset/queries.fvec"
          ground_truth_file: "/datasets/my-dataset/indices_d1024_k{{query_k}}_{{corpus_size}}.ivec"
          target_field_name: "vector"
          warmup_time_period: 60
          time_period: 300
          hnsw_ef_search: 128
        parameter_sweeps:
          - params: { search_clients: 4 }
          - params: { search_clients: 8 }
          - params: { search_clients: 10 }
          - params: { search_clients: 16 }
```

---

## Test Procedure Filtering

The Jenkins pipeline's `PIPELINE` parameter controls which procedures run:

| `PIPELINE` value | Procedures executed |
|-----------------|---------------------|
| `complete` | `create-index` → `bulk-ingest-data` → `refresh-index` → `force-merge` → `vector-search` |
| `search-only` | `vector-search` only (index must already exist) |

Procedure names are matched by substring: procedures containing `"index"` or `"ingest"` run in the index phase; `"search"` in the search phase; `"merge"` in the merge phase.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dataset not in dropdown after sync | Check YAML indentation — datasets must be under the `datasets:` key |
| `workload not found` error | Verify `workload` value matches the directory name in opensearch-benchmark-workloads |
| GCS seed step fails | Confirm `gcs_path` object exists and the worker pod's service account has `storage.objects.get` |
| Data file download fails | Check `url` is accessible from the worker pod; inspect worker logs |
| Parameter template error | Ensure `{{corpus_size}}` or `{{corpus_name}}` are defined in `common_params` before referencing them in `params` |
| YAML parse error | Run `python3 -c "import yaml; yaml.safe_load(open('config/datasets.yaml'))"` locally to validate |
