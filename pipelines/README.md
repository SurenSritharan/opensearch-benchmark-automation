# Pipelines

Each file here defines a complete benchmark run — an ordered list of steps with all the parameters needed. There are no separate scenario files; everything lives in the pipeline.

---

## Schema

```jsonc
{
  "description": "Human-readable summary",
  "engine":      "__ENGINE__",    // replaced at submit time: jvector | faiss | lucene
  "no_profiling": false,          // optional — omit to keep async-profiler enabled
  "no_metrics":   false,          // optional — omit to keep GKE metrics collection enabled
  "log_level":    "debug",        // optional — sets opensearch-benchmark HTTP logging level (debug|info|warning|error)
  "params": {                     // optional — pipeline-level params merged into every step
    "corpus_size": "1m"           // drives auto-derivation of target_index_name + num_vectors
  },
  "steps": [
    {
      "dataset":  "cohere-msmarco-1024",  // must match a key in config/datasets.yaml
      "scenario": "vector-search",         // must match a test_procedure name in datasets.yaml
      "params": {                          // optional — only the overrides you need
        "corpus_size": "1m",
        "query_k": 100,
        "hnsw_ef_search": 256
      }
    }
  ]
}
```

### Available datasets
- `cohere-wiki-en-768`
- `cohere-msmarco-1024`

### Available scenarios (procedures)
| Name | What it does |
|---|---|
| `create-index` | Creates the OpenSearch index with the configured HNSW/DiskANN settings |
| `bulk-ingest-data` | Bulk-loads vectors into the index |
| `refresh-index` | Forces a refresh so all documents are searchable |
| `force-merge` | Merges segments (run after ingest for clean benchmarks) |
| `vector-search` | Runs k-NN search sweeps; use `query_k` to set k (10, 64, 100) |

---

## How params work

**Merge order** (later wins): `pipeline.params` → `step.params`

**Auto-derived from `corpus_size`** (injected unless already present):

| Field | Example |
|---|---|
| `target_index_name` | `cohere-msmarco-1024-1m` |
| `num_vectors` | `1000000` (on `bulk-ingest-data` and `vector-search` steps) |

`corpus_size` can be set at the pipeline level (applied to all steps) or per-step (when mixing sizes in one pipeline, e.g. `complete.json`). It is stripped before being forwarded to OSB — it's only used internally to derive the above fields.

To target a custom index (e.g. HQ), set `target_index_name` in `pipeline.params` — it overrides the auto-derived name for all steps.

**Ground truth file fallback for `query_k`:**
msmarco ground truth files only exist for k=10 and k=100. When `query_k=64` is used, the config loader automatically resolves the ground truth path to the k=100 file (which contains 100 neighbors per query) and OSB slices it to the first 64 when computing recall. No per-step override is needed.

---

## Usage

### Jenkins
Select the pipeline from the `PIPELINE` dropdown, or set `PIPELINE_OVERRIDE` to any pipeline name for ad-hoc runs.

### Command line
```bash
./cloud-service/scripts/run-pipeline.sh --pipeline complete-1m jvector
./cloud-service/scripts/run-pipeline.sh --pipeline search-all faiss
API_URL=http://1.2.3.4 ./cloud-service/scripts/run-pipeline.sh --pipeline msmarco-jvector-hq-build jvector
```

---

## Adding a new pipeline

1. Create `pipelines/<name>.json`
2. Add it to the `PIPELINE` choices in `Jenkinsfile`
3. Set `corpus_size` at the pipeline level (shared) or per step (mixed sizes)
4. List the steps — only include params that differ from `datasets.yaml` defaults

**Example: msmarco-only search at 10M with k=100, 64, 10**
```json
{
  "description": "msmarco search-only at 10M",
  "engine": "__ENGINE__",
  "params": { "corpus_size": "10m" },
  "steps": [
    { "dataset": "cohere-msmarco-1024", "scenario": "vector-search", "params": { "query_k": 100, "hnsw_ef_search": 256 } },
    { "dataset": "cohere-msmarco-1024", "scenario": "vector-search", "params": { "query_k": 64,  "hnsw_ef_search": 256 } },
    { "dataset": "cohere-msmarco-1024", "scenario": "vector-search", "params": { "query_k": 10,  "hnsw_ef_search": 256 } }
  ]
}
```

---

## Files

| File | Corpus | Datasets | Description |
|---|---|---|---|
| `complete.json` | 1M + 5M | wiki + msmarco | Full run at 1M then 5M in a single job: create → ingest → merge → search k=100, 64, 10 |
| `search-all.json` | 1M + 5M | wiki + msmarco | Search only at both 1M and 5M, k=100, 64, 10 (indexes must exist) |
| `complete-1m.json` | 1M | wiki + msmarco | Full run at 1M only |
| `complete-5m.json` | 5M | wiki + msmarco | Full run at 5M only |
| `search-1m.json` | 1M | wiki + msmarco | Search only at 1M (indexes must exist) |
| `search-5m.json` | 5M | wiki + msmarco | Search only at 5M (indexes must exist) |
| `search-compare.json` | 1M + 5M | wiki + msmarco | Version comparison across two OpenSearch versions and two node sizes |
| `msmarco-jvector-hq-build.json` | 1M | msmarco | Build HQ index (ef_construction=512, M=64) |
| `msmarco-jvector-hq-search.json` | 1M | msmarco | Search HQ index, sweep ef_search 128/256/512, k=100, 64, 10 |
| `msmarco-jvector-hq-full.json` | 1M | msmarco | HQ build + ef_search sweep in one job, k=100, 64, 10 |
