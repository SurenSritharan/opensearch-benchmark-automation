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
  "params": {                     // pipeline-level params merged into every step
    "corpus_size": "1m"           // drives auto-derivation of target_index_name + num_vectors
  },
  "steps": [
    {
      "dataset":  "cohere-msmarco-1024",  // must match a key in config/datasets.yaml
      "scenario": "vector-search",         // must match a test_procedure name in datasets.yaml
      "params": {                          // optional — only the overrides you need
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
| `vector-search` | Runs k-NN search sweeps; use `query_k` to set k |

---

## How params work

**Merge order** (later wins): `pipeline.params` → `step.params`

**Auto-derived from `corpus_size`** (injected unless already present):

| Field | Example |
|---|---|
| `target_index_name` | `cohere-msmarco-1024-1m` |
| `num_vectors` | `1000000` (on `bulk-ingest-data` and `vector-search` steps) |

To target a different index (e.g. HQ), set `target_index_name` in `pipeline.params` — it overrides the auto-derived name for all steps.

---

## Usage

### Jenkins
Set `PIPELINE_FILE` to the pipeline path and `ENGINE_TARGET` to the engine cluster(s).

### Command line
```bash
./cloud-service/scripts/run-pipeline.sh --pipeline-file pipelines/complete-1m.json jvector
```

---

## Adding a new pipeline

1. Create `pipelines/<name>.json`
2. Set `"params": { "corpus_size": "..." }` (or any other shared params)
3. List the steps — only include params that differ from `datasets.yaml` defaults

**Example: msmarco-only search at 10M**
```json
{
  "description": "msmarco search-only at 10M",
  "engine": "__ENGINE__",
  "params": { "corpus_size": "10m" },
  "steps": [
    { "dataset": "cohere-msmarco-1024", "scenario": "vector-search", "params": { "query_k": 100, "hnsw_ef_search": 256 } },
    { "dataset": "cohere-msmarco-1024", "scenario": "vector-search", "params": { "query_k": 10,  "hnsw_ef_search": 256 } }
  ]
}
```

---

## Files

| File | Corpus | Datasets | Description |
|---|---|---|---|
| `complete-1m.json` | 1M | wiki + msmarco | Full run: create → ingest → merge → search k=100 + k=10 |
| `complete-5m.json` | 5M | wiki + msmarco | Same at 5M |
| `search-only-1m.json` | 1M | wiki + msmarco | Search only (indexes must exist) |
| `search-only-5m.json` | 5M | wiki + msmarco | Same at 5M |
| `msmarco-jvector-hq-build.json` | 1M | msmarco | Build HQ index (ef_construction=512, M=64) |
| `msmarco-jvector-hq-search.json` | 1M | msmarco | Search HQ index, sweep ef_search 128/256/512 |
| `msmarco-jvector-hq-full.json` | 1M | msmarco | HQ build + ef_search sweep in one job |
