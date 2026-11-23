## OpenRAG Hybrid Search workload

This workload benchmarks OpenSearch the way [OpenRAG](https://github.com/langflow-ai/openrag) uses it: hybrid retrieval over chunked, OpenAI-embedded PDFs, through both of OpenRAG's search code paths, with and without OpenRAG's document-level security (DLS). It measures how fast each search is and whether it returns the paper that answers the question.

The requests are reproduced from OpenRAG commit `f00c103` (2026-10-30). `openrag_queries.py` holds the request builders, and each one names the OpenRAG function it mirrors. When OpenRAG changes, update the builders and the commit together.

### How OpenRAG uses OpenSearch

OpenRAG's hybrid search is **not** the neural-search `hybrid` query with a normalization pipeline, which `noaa_semantic_search` and `treccovid_semantic_search` benchmark. OpenRAG embeds the query outside OpenSearch, then sends a `bool` query:

```
bool.should (minimum_should_match: 1)
 ├─ dis_max (boost 0.7, tie_breaker 0)
 │    └─ knn  chunk_embedding_<provider>_<model>  {vector, k: 50, num_candidates: 1000}   one per embedding model in the index
 ├─ multi_match (boost 0.3)   text^2, filename^1.5, best_fields, fuzzy
 └─ match_phrase_prefix (boost 0.25, search API path only)
+ terms facet aggregations, _source include list, highlight (search API path only)
```

Two code paths send this query, with small differences:

| Path | OpenRAG code | Used by | Differences |
|---|---|---|---|
| `api` | `SearchService._search` | search API, Knowledge view, backend search tool | `fuzziness: AUTO:4,7`, prefix clause, highlight, five facets with sub-aggregations, record-kind filter |
| `langflow` | `OpenSearchVectorStoreComponentMultimodalMultiEmbedding.search` | the chat agent's retrieval tool (`flows/openrag_agent.json`) | `fuzziness: AUTO`, smaller facets, an extra `GET _mapping` request |

A single OpenRAG search issues several OpenSearch requests, and the `openrag-search` runner replays all of them:

1. Discovery of the embedding spaces in the index: composite aggregations, paginated, first on qualified spaces and then on legacy models. A corpus with one model needs three requests.
2. `GET <index>/_mapping` (`langflow` path only).
3. The hybrid query. OpenSearch's `knn` query has no `num_candidates` parameter, so this request fails with a 400. OpenRAG then retries without `num_candidates`, which means **every OpenRAG search sends the hybrid query twice**.

Two more behaviors matter for DLS users:

* OpenRAG's ACL role uses a terms lookup (`openrag_dls_principals`), which forces filter-level DLS.
* Under filter-level DLS, `GET _mapping` is rejected. The `langflow` path then carries on without mapping metadata and aggregates its `data_sources` facet on `filename.keyword`, a field that does not exist.

Query embedding is precomputed. Embedding-API latency and the LLM are not part of the measurement, because OpenSearch does neither in OpenRAG.

### Dataset

The corpus is the 1000 arXiv papers of Vectara's [Open RAG Benchmark](https://huggingface.co/datasets/vectara/open_ragbench), and the queries are its 3045 questions. Each question is labelled with the one paper that answers it (`qrels.json`), so the workload measures whether OpenRAG's search finds the right paper as well as how fast it is. The papers span arXiv's categories from 2024 and early 2025. 400 of them answer at least one question; the other 600 were picked to be irrelevant to every question.

`scripts/build_corpus.py download` fetches Vectara's paper list, questions and labels from Hugging Face, and the PDFs from `export.arxiv.org` at one request per second. That is the host and pace arXiv's [bulk data page](https://info.arxiv.org/help/bulk_data.html) asks harvesters to use; 1000 papers take about an hour. `--source s3` takes one month of the requester-pays bulk bucket instead, and the queries are then generated (see below).

The chunks are what OpenRAG makes of the PDFs, built in one of two ways ([Building the corpus](#building-the-corpus)): by uploading the PDFs to a real OpenRAG and exporting its index, or offline with OpenRAG's settings:

* **Parse:** docling-serve, OCR off, table structure on, exported to markdown one page at a time.
* **Chunk:** `RecursiveCharacterTextSplitter` with `chunk_size` 1000, `chunk_overlap` 200 and separator `\n`.
* **Embed:** each chunk with OpenAI `text-embedding-3-small` (1536 dimensions).
* **Write:** chunk documents shaped like `DocumentIndexWriter._build_chunk_document`, with OpenRAG's scoped `_id` (`<owner digest>_<document_id>_<batch>_<n>`).

The index mapping (`index.json`) is OpenRAG's `create_index_body()` plus the embedding field that the first ingest adds. Vectors use OpenRAG's `jvector` engine with `disk_ann`, `l2`, `ef_construction` 100 and `m` 16.

For DLS, every paper is owned by one of 10 synthetic users (`bench_identity.py`):

* About 70% of papers are local uploads with an owner only.
* About 30% are connector-shared papers that also grant a second user and a group principal.

Queries (`queries.json.bz2`, precomputed `text-embedding-3-small` vectors included) record the paper that answers them and which synthetic users DLS lets see it. Open RAG Benchmark questions have type `abstractive` or `extractive`. For corpora without labelled questions (`--source s3`), the `queries` stage generates them instead: 40% `question` (an LLM writes a question from a random chunk), 40% `keyword` (the short tool query an agent would send for it) and 20% `token` (identifier lookups such as `ResNet-50`).

Example chunk document (vector shortened):

```json
{
  "document_id": "7ESdXBqOdJWu0-ULbVWpjTbk",
  "filename": "2407.01528v3.pdf",
  "mimetype": "application/pdf",
  "page": 3,
  "text": "## 3 Method\nWe fine-tune ...",
  "chunk_embedding_openai_text_embedding_3_small": [0.0123, -0.0456, "..."],
  "embedding_model": "text-embedding-3-small",
  "embedding_provider": "openai",
  "embedding_space_id": "openai:text-embedding-3-small",
  "embedding_dimensions": 1536,
  "owner": "openrag_bench_user_1",
  "allowed_users": [],
  "allowed_principals": [],
  "connector_type": "local",
  "parser": "Docling Serve 1.20.0",
  "chunk_size": 1000,
  "chunk_overlap": 200
}
```

Licensing: Vectara publishes the questions, labels and answers under CC BY-NC 4.0 (non-commercial use). The papers remain under their authors' licenses. Check both before using the data commercially or publishing a corpus built from it.

### Building the corpus

```
pip install -r scripts/requirements.txt
python scripts/build_corpus.py download --work-dir ~/openrag-arxiv
```

Then build the chunks one of two ways.

**Through OpenRAG (recommended).** The chunks are exactly what OpenRAG produces. You need a running OpenRAG configured with OpenAI `text-embedding-3-small` and its default ingestion settings, and an OpenRAG API key.

```
export OPENRAG_API_KEY=orag_...
python scripts/openrag_ingest.py --work-dir ~/openrag-arxiv --openrag-url http://localhost:3000

export OPENSEARCH_PASSWORD=<OpenRAG's OpenSearch admin password>
python scripts/openrag_export.py --work-dir ~/openrag-arxiv --opensearch-url https://localhost:9200
```

* `openrag_ingest.py` uploads each PDF through `POST /api/v1/documents/ingest` and waits for its task, two at a time by default because OpenRAG's Docling step runs two at a time. Progress is saved, so rerunning it resumes and retries failed files. Ingesting 1000 papers takes hours, mostly in Docling. It also appends per-PDF timings, a summary after every `--batch-size` (default 100) completed PDFs and a run total to `--timing-log` (default `<work-dir>/openrag_ingest_timing.log`).
* `openrag_export.py` reads every chunk OpenRAG indexed for those PDFs with the admin login, skips OpenRAG's own sample documents, checks each chunk has its vector, and writes the chunks for the next stages. It also saves OpenRAG's mapping (`openrag_mapping.json`) and a summary (`openrag_export.json`) listing any PDF that produced no chunks.
* Ownership: an upload's owner is the uploading account, or none at all in OpenRAG's no-auth mode, which DLS treats as visible to everyone. The export therefore replaces each paper's owner and sharing fields with the synthetic users above, assigned exactly as the offline route assigns them, and recomputes the owner part of each `_id`. `--keep-ownership` keeps OpenRAG's values, for example after uploading as several real users.

**Offline.** No OpenRAG needed. You need a docling-serve on port 5001, started the way OpenRAG starts it (`src/tui/managers/docling_manager.py`) with the same pinned versions, and `OPENAI_API_KEY`. Parsing 1000 papers takes hours on CPU; a GPU is much faster.

```
TORCH_COMPILE_DISABLE=1 TORCH_DYNAMO_DISABLE=1 uvx --python 3.13 \
  --from "docling-serve[ui]==1.26.0" --with onnxruntime --with easyocr \
  --with "docling[ocrmac,easyocr,rapidocr,vlm]==2.108.0" --with docling-core==2.85.0 \
  --with "transformers>=5.8.1,<5.9.0" \
  docling-serve run --host 127.0.0.1 --port 5001 --workers 1

python scripts/build_corpus.py parse --work-dir ~/openrag-arxiv --docling-url http://localhost:5001
python scripts/build_corpus.py embed --work-dir ~/openrag-arxiv
```

On Linux, drop `ocrmac` from the docling extras.

**Either way, finish with:**

```
export OPENAI_API_KEY=sk-...
python scripts/build_corpus.py queries --work-dir ~/openrag-arxiv
python scripts/build_corpus.py package --work-dir ~/openrag-arxiv
```

`queries` embeds the questions with `text-embedding-3-small`, which costs well under a cent, and drops any whose paper is missing from the corpus. `package` writes the files into OSB's dataset cache (`~/.osb/benchmarks/data/openrag_arxiv_1000`) and records the real document count and sizes in `corpora.json`. Every stage resumes from its cache in `--work-dir`. Once the files are hosted, set `corpus_base_url` so OSB can download them.

### Cluster

Run against OpenRAG's OpenSearch image (`langflowai/openrag-opensearch`). Stock OpenSearch has no `jvector` engine, so creating the index fails there unless you override `knn_engine` and `knn_method_name` to compare engines.

The DLS mode authenticates like OpenRAG does: an RS256 JWT through the security plugin's openid domain, with the claims OpenRAG's `SessionManager` issues (`sub`, `email`, `roles: [openrag_user]`). Basic-auth users cannot stand in. OpenRAG's roles reference `${attr.jwt.email}`, and OpenSearch Security rejects DLS queries that reference attributes the user does not have.

* **Full OpenRAG deployment:** set `dls_jwt_private_key` to OpenRAG's `keys/private_key.pem`.
* **OpenSearch without the OpenRAG backend:** run the OIDC stub and point the container's backend URL at it:

```
python scripts/oidc_stub.py --key-file ~/openrag-bench-jwt.pem --port 8000
docker run -d -p 9200:9200 -e discovery.type=single-node \
  -e OPENSEARCH_INITIAL_ADMIN_PASSWORD='<admin password>' \
  -e 'OPENSEARCH_JAVA_OPTS=-Xms1g -Xmx1g' \
  -e OPENRAG_BACKEND_INTERNAL_URL=http://host.docker.internal:8000 \
  langflowai/openrag-opensearch:latest
```

`-Xms1g -Xmx1g` is OpenRAG's docker-compose default. Size the heap for what you want to measure.

### Test procedures

| Procedure | What it does |
|---|---|
| `index-and-search` (default) | Ingests the corpus as OpenRAG writes it, then runs every search operation as admin |
| `index-and-search-dls` | Ingests, prepares DLS, then runs each user-facing search as admin and as DLS users, back to back |
| `search-only` / `search-only-dls` | The same searches against an index ingested earlier |
| `ingest-only` | Ingestion only |
| `concurrency-sweep` | `api-search` and `agent-retrieval` at each count in `sweep_clients` (`search_auth:dls` for DLS users) |
| `ingest-while-searching` | Re-ingests the corpus at `concurrent_ingest_throughput` bulks/s while both retrieval paths search, all for `ingest_while_searching_period` seconds |

Ingestion uses OpenRAG's batch of 100 chunks per bulk. Because nothing searches during ingestion, the index is refreshed only after every `refresh_every_docs` ingested documents (default 20000) and once more after ingestion, rather than after each bulk. The index is not force-merged unless `force_merge_max_num_segments` is set, because OpenRAG never force-merges.

### Operations

Every search operation also exists with a `-dls` suffix, which sends each request as a synthetic user who can see the paper that answers the query.

| Operation | Requests per op | What it measures |
|---|---|---|
| `api-search` | discovery + hybrid (with the `num_candidates` retry) | one search API / backend tool search, end to end |
| `agent-retrieval` | discovery + mapping + hybrid (with retry) | one call of the chat agent's retrieval tool, end to end |
| `knowledge-search` | as `api-search`, size 100, knowledge-sources mode | the Knowledge view search |
| `knowledge-facets` | discovery + `*` facet query | loading the Knowledge view |
| `api-search-file-filter`, `agent-retrieval-file-filter` | as above, with a knowledge filter of three files: the answering paper and two others | scoped search |
| `api-search-score-threshold` | as `api-search`, wrapped in `function_score` | the score-threshold setting |
| `api-search-keyword-fallback` | discovery + keyword-only query | OpenRAG when query embedding fails |
| `api-hybrid-query`, `agent-hybrid-query` | the successful hybrid query only | the hybrid query by itself |
| `embedding-space-discovery` | first discovery page only | discovery cost by itself |
| `baseline-knn-only` | plain `knn` query | vector-only baseline, not an OpenRAG request |

Besides latency and throughput, the end-to-end operations (those using the `openrag-search` runner, all but the last three rows) report relevance in OSB's results as `recall@k` and `recall@1`. These are the share of queries for which the answering paper appears among the results (`k` is the result size: 10, or 100 for `knowledge-search`) and is the top result. They are computed from OpenSearch's raw hits, before OpenRAG's own post-processing. `knowledge-facets` has no query text, so it reports none.

### Parameters

This workload allows [specifying the following parameters](#specifying-workload-parameters) using the `--workload-params` option to OpenSearch Benchmark:

* `dls_jwt_private_key`: RSA private key (PEM) that signs DLS-mode JWTs. Required for any `-dls` operation.
* `dls_jwt_issuer` (default: `http://openrag-backend:8000`): `iss` claim of DLS-mode JWTs.
* `dls_users` (default: 10): Number of synthetic users DLS requests are spread over.
* `dls_user_choice` (default: `can-see`): Search as a user who can see the answering paper; `random` picks any user, which lowers relevance for papers that user cannot see.
* `create_missing_openrag_roles` (default: false): Create OpenRAG's DLS roles if the cluster lacks them, for clusters not built from OpenRAG's image.
* `search_iterations` (default: 500), `search_warmup_iterations` (default: 50): Per search task.
* `search_clients` (default: 1): Clients per search task.
* `target_throughput` (default: unset, meaning as fast as possible): Requests per second per search task.
* `sweep_clients` (default: `[1, 2, 4, 8, 16]`), `search_auth` (default: `admin`): For `concurrency-sweep`.
* `num_candidates` (default: 1000): `num_candidates` the full-flow operations send. Set it to 0 to see OpenRAG without the retry.
* `score_threshold` (default: 0.5): For `api-search-score-threshold`.
* `query_types` (default: all): Restrict queries by type: `["abstractive"]` or `["extractive"]` for Open RAG Benchmark questions, or `question`, `keyword` and `token` for generated ones.
* `measure_relevance` (default: true): Report `recall@k` and `recall@1` for end-to-end operations.
* `query_seed` (default: 42): Seed for query and user selection.
* `detailed_results` (default: false): Record hits, took and request counts per search.
* `search_request_timeout` (default: unset): Client timeout for searches.
* `bulk_size` (default: 100), `bulk_indexing_clients` (default: 1), `ingest_percentage` (default: 100), `refresh_every_docs` (default: 20000, 0 disables mid-ingest refreshes): Ingestion.
* `concurrent_ingest_throughput` (default: 1), `ingest_while_searching_period` (default: 600): For `ingest-while-searching`.
* `number_of_shards` (default: 1), `number_of_replicas` (default: 0): OpenRAG's defaults.
* `refresh_interval` (default: unset): Index refresh interval.
* `knn_engine` (default: `jvector`), `knn_method_name` (default: `disk_ann`), `knn_ef_construction` (default: 100), `knn_m` (default: 16): Vector field method.
* `force_merge_max_num_segments` (default: unset): Force-merge after ingest.
* `cluster_health` (default: `green`): Use `yellow` on a single node with replicas.
* `error_level` (default: `non-fatal`): Bulk `ignore-response-error-level`.
* `corpus_base_url` (default: unset): Where to download the corpus and queries from.
* `queries_path` (default: next to the corpus): Local query file.

### Specifying Workload Parameters

Example:
```json
{
  "dls_jwt_private_key": "~/openrag-bench-jwt.pem",
  "search_clients": 4,
  "search_iterations": 1000,
  "cluster_health": "yellow"
}
```

Save it as `params.json` and provide it to OpenSearch Benchmark with `--workload-params="/path/to/params.json"`.

### Sample command

The subcommand is `run` in current OpenSearch Benchmark releases and `execute-test` in some other versions.

```
opensearch-benchmark run --workload-path=openrag_hybrid_search \
  --test-procedure=index-and-search-dls --pipeline=benchmark-only \
  --target-hosts=https://localhost:9200 \
  --client-options="basic_auth_user:admin,basic_auth_password:<admin password>,use_ssl:true,verify_certs:false" \
  --workload-params=params.json
```

### Checking fidelity against a live OpenRAG

Set `index.search.slowlog.threshold.query.trace: 0ms` on OpenRAG's `documents` index, run a few searches in the OpenRAG UI and chat, and compare the logged sources with the bodies `openrag_queries.py` builds for the same query. Apart from the query text and vectors, they should be identical.
