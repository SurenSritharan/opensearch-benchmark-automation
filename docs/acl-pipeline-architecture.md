# ACL Pipeline Architecture

This document describes how the Query Time ACL benchmarking system works end-to-end — from cluster setup through ingest, DLS role assignment, pipeline execution, and result collection.

## Overview

The ACL benchmark measures the latency overhead introduced by Document Level Security (DLS) queries. DLS injects a per-user filter into every search query at the security plugin layer before the query reaches the engine.

The system is designed so that:

- Every document in the corpus has ACL fields injected at ingest time by the `acl_guard` OpenSearch ingest pipeline.
- **One DLS role per user** (one-to-one mapping). Each user gets a dedicated role so the Security plugin does not union `backend_roles` across roles — exactly as production service accounts behave.
- **No `security_classification`**. Access control is enforced purely through `access_roles`, `access_groups`, and `access_users`.
- Two DLS filter patterns: `bool.should` (OR) for standard users and `bool.filter` (AND) for ultrastrict users.
- 11 benchmark users representing a visibility gradient from 100% down to a fixed 5,000 docs.
- The baseline (`user-unrestricted`, no DLS) provides raw vector search cost. Every other user's overhead is delta-compared against it.

```
┌─────────────────────────────────────────────────────────────────┐
│  Jenkins build                                                  │
│                                                                 │
│  1. acl-setup.sh <ns>  → installs ingest pipeline + index       │
│                           template + 11 DLS roles + 11 users    │
│                                                                 │
│  2. run-pipeline.sh    → submits pipeline steps as a batch      │
│                          job to the benchmark API               │
└─────────────────────────────────────────────────────────────────┘
```

## JWT Simulation

In production, DLS filters expand dynamically from the user's JWT:

```
${user.roles}  →  the user's group/role claims from the JWT
${user.name}   →  the user's identity from the JWT
```

In OpenSearch benchmarking there is no JWT. The equivalent is:

```
${user.roles}  →  the user's backend_roles array (set at user creation time)
${user.name}   →  the username string
```

These are **functionally identical**. Each user's `backend_roles` and username simulate their production JWT claims, causing the DLS filter to expand differently per user — exactly as different JWT payloads would behave in production.

## ACL Data Model

The following `keyword` fields are injected at ingest time by the `acl_guard` ingest pipeline:

| Field           | Type              | Description                                                                     |
| --------------- | ----------------- | ------------------------------------------------------------------------------- |
| `access_roles`  | `keyword` (array) | Role names that grant read access; absent when the doc is not role-controlled   |
| `access_groups` | `keyword` (array) | Group names that grant read access; absent when the doc is not group-controlled |
| `access_users`  | `keyword` (array) | Named users that may read this doc; absent when the doc has no named-user ACL   |

Not every document has all three fields. A document carries only the access field(s) relevant to its bucket segment.

### Access Value Vocabulary

| Value                           | Field           | Used by                                                        |
| ------------------------------- | --------------- | -------------------------------------------------------------- |
| `role-svc`                      | `access_roles`  | Standard role-based buckets (0–34, 78–82)                      |
| `grp-finance`                   | `access_groups` | Finance group buckets (35–67, 78–87)                           |
| `grp-sensitive`                 | `access_groups` | Sensitive group buckets (88–93)                                |
| `need-to-know`                  | `access_roles`  | Ultrastrict buckets (94, 95–99, Tier A, Tier B)                |
| `need-to-know`                  | `access_groups` | Ultrastrict buckets (94, 95–99, Tier A, Tier B)                |
| `user-name-only`                | `access_users`  | Name-only bucket (68–77)                                       |
| `user-group-name`               | `access_users`  | Group+name bucket (83–87, alongside noise users)               |
| `user-ultrastrict`              | `access_users`  | Ultrastrict bucket (95–99, alongside noise users)              |
| `user-ultrastrict-narrow`       | `access_users`  | Narrow bucket (94, alongside noise users)                      |
| `user-ultrastrict-micro`        | `access_users`  | Tier B (id≥5000 AND id%1000<6, with noise)                     |
| `user-ultrastrict-fixed`        | `access_users`  | Tier A (id<5000, with noise)                                   |
| `user-noise-01`…`user-noise-09` | `access_users`  | Noise entries on large-list buckets to test ACL list scan cost |

## Corpus Bucket Layout

The `acl_guard` ingest pipeline assigns ACL fields deterministically. Three tiers are evaluated top-to-bottom in Painless; first match wins.

### Tier A — Fixed-5k (`id < 5000`)

Exactly 5,000 docs regardless of corpus size. All three fields written; requires triple-AND DLS to match.

| Field           | Value                                        |
| --------------- | -------------------------------------------- |
| `access_users`  | `[user-ultrastrict-fixed, user-noise-01…09]` |
| `access_roles`  | `[need-to-know]`                             |
| `access_groups` | `[need-to-know]`                             |

### Tier B — Micro-0.6% (`id ≥ 5000 AND id % 1000 < 6`)

~0.6% of corpus (~6k docs at 1M). All three fields written; requires triple-AND DLS to match.

| Field           | Value                                        |
| --------------- | -------------------------------------------- |
| `access_users`  | `[user-ultrastrict-micro, user-noise-01…09]` |
| `access_roles`  | `[need-to-know]`                             |
| `access_groups` | `[need-to-know]`                             |

### Tier C — Bucket layout (`id % 100`, for `id ≥ 5000 AND id % 1000 ≥ 6`)

Each bucket = ~1% of the Tier C pool (~10k docs at 1M). Total: 100 buckets.

| Bucket range | `access_roles` | `access_groups` | `access_users`                                | Visible to                                    | Buckets |
| ------------ | -------------- | --------------- | --------------------------------------------- | --------------------------------------------- | ------- |
| 0–34         | `role-svc`     | —               | —                                             | role-only, role-group                         | 35      |
| 35–67        | —              | `grp-finance`   | —                                             | group-only, role-group, group-name            | 33      |
| 68–77        | —              | —               | `[user-name-only]`                            | name-only                                     | 10      |
| 78–82        | `role-svc`     | `grp-finance`   | —                                             | role-only, group-only, role-group, group-name | 5       |
| 83–87        | —              | `grp-finance`   | `[user-group-name, user-noise-01…09]`         | group-only, role-group, group-name            | 5       |
| 88–93        | —              | `grp-sensitive` | —                                             | group-sensitive                               | 6       |
| 94           | `need-to-know` | `need-to-know`  | `[user-ultrastrict-narrow, user-noise-01…09]` | ultrastrict-narrow                            | 1       |
| 95–99        | `need-to-know` | `need-to-know`  | `[user-ultrastrict, user-noise-01…09]`        | ultrastrict                                   | 5       |
| **Total**    |                |                 |                                               |                                               | **100** |

## DLS Roles and Users

### 11 DLS Roles (one-to-one with users)

Two filter patterns are used:

**Standard filter** (`bool.should`, OR — any one field match grants access):

```json
{
  "bool": {
    "should": [
      { "terms": { "access_roles": ["${user.roles}"] } },
      { "terms": { "access_groups": ["${user.roles}"] } },
      { "terms": { "access_users": ["${user.name}"] } }
    ],
    "minimum_should_match": 1
  }
}
```

**Ultrastrict filter** (`bool.filter`, AND — all three fields must match):

```json
{
  "bool": {
    "filter": [
      { "terms": { "access_roles": ["${user.roles}"] } },
      { "terms": { "access_groups": ["${user.roles}"] } },
      { "terms": { "access_users": ["${user.name}"] } }
    ]
  }
}
```

### 11 User Personas

| User                      | Role                     | DLS filter   | `backend_roles`              | Visible docs @ 1M | Visible buckets / tier      |
| ------------------------- | ------------------------ | ------------ | ---------------------------- | ----------------- | --------------------------- |
| `user-unrestricted`       | `dls-baseline`           | None         | `["grp-broad"]`              | **~1,000,000**    | all (no DLS)                |
| `user-role-group`         | `dls-role-group`         | should (OR)  | `["role-svc","grp-finance"]` | **~780,000**      | 0–87                        |
| `user-group-only`         | `dls-group-only`         | should (OR)  | `["grp-finance"]`            | **~430,000**      | 35–87                       |
| `user-group-name`         | `dls-group-name`         | should (OR)  | `["grp-finance"]`            | **~430,000**      | 35–87                       |
| `user-role-only`          | `dls-role-only`          | should (OR)  | `["role-svc"]`               | **~400,000**      | 0–34, 78–82                 |
| `user-name-only`          | `dls-name-only`          | should (OR)  | `[]`                         | **~100,000**      | 68–77                       |
| `user-group-sensitive`    | `dls-group-sensitive`    | should (OR)  | `["grp-sensitive"]`          | **~60,000**       | 88–93                       |
| `user-ultrastrict`        | `dls-ultrastrict`        | filter (AND) | `["need-to-know"]`           | **~50,000**       | 95–99                       |
| `user-ultrastrict-narrow` | `dls-ultrastrict-narrow` | filter (AND) | `["need-to-know"]`           | **~10,000**       | bucket 94                   |
| `user-ultrastrict-micro`  | `dls-ultrastrict-micro`  | filter (AND) | `["need-to-know"]`           | **~6,000**        | Tier B (id%1000<6, id≥5000) |
| `user-ultrastrict-fixed`  | `dls-ultrastrict-fixed`  | filter (AND) | `["need-to-know"]`           | **5,000**         | Tier A (id<5000)            |

All users share password `BenchmarkACL-2024!`.

> **Why `user-group-name` and `user-group-only` show the same count**: Both match `grp-finance` via `${user.roles}`. `user-group-name` also gets username matches on buckets 83–87 via `user-group-name` in `access_users`, but those buckets are already covered by the `grp-finance` group match — so the visible set is identical.

## Pipeline Catalogue

### Sweep Pipelines (self-contained)

Build the index from scratch and run all 11 user personas × 2 sweeps (k10 and k100).

| Pipeline                                           | Corpus          |
| -------------------------------------------------- | --------------- |
| [`acl-sweep-1m`](../pipelines/acl-sweep-1m.json)   | 1M wiki-en-768  |
| [`acl-sweep-5m`](../pipelines/acl-sweep-5m.json)   | 5M wiki-en-768  |
| [`acl-sweep-10m`](../pipelines/acl-sweep-10m.json) | 10M wiki-en-768 |

Search sweeps per persona:

| Sweep | `query_k` | `hnsw_ef_search` |
| ----- | --------- | ---------------- |
| 1     | 10        | 128              |
| 2     | 100       | 128              |

Search order (least → most restrictive): `unrestricted` → `role-group` → `group-only` → `group-name` → `role-only` → `name-only` → `group-sensitive` → `ultrastrict` → `ultrastrict-narrow` → `ultrastrict-micro` → `ultrastrict-fixed`.

### Individual Persona Pipelines (search-only)

Run a single persona across both k/ef sweeps. Requires the index to already exist.

- [`acl-unrestricted`](../pipelines/acl-unrestricted.json)
- [`acl-role-group`](../pipelines/acl-role-group.json)
- [`acl-group-only`](../pipelines/acl-group-only.json)
- [`acl-group-name`](../pipelines/acl-group-name.json)
- [`acl-role-only`](../pipelines/acl-role-only.json)
- [`acl-name-only`](../pipelines/acl-name-only.json)
- [`acl-group-sensitive`](../pipelines/acl-group-sensitive.json)
- [`acl-ultrastrict`](../pipelines/acl-ultrastrict.json)
- [`acl-ultrastrict-narrow`](../pipelines/acl-ultrastrict-narrow.json)
- [`acl-ultrastrict-micro`](../pipelines/acl-ultrastrict-micro.json)
- [`acl-ultrastrict-fixed`](../pipelines/acl-ultrastrict-fixed.json)

## How a Pipeline Run Works

```
run-pipeline.sh --pipeline acl-sweep-1m jvector-acl
       │
       ▼
1. Read pipelines/acl-sweep-1m.json
   Replace __ENGINE__ → jvector-acl
   Derive PIPELINE_NAME = "acl-sweep-1m"

2. For each step:
   a. Merge pipeline params + step params
   b. Auto-derive target_index_name from corpus_size
   c. Hoist step_username / step_password to top-level fields
      (never forwarded to OSB as workload variables)

3. POST /api/v1/benchmark/batch  { engine, pipeline_name, tests: [...] }
       │
       ▼
4. app.py (worker pod)
   For each scenario:
   a. Validate scenario exists in config/datasets.yaml
   b. Inject pipeline_name + step credentials into workload_params
   c. Call benchmark_runner.run_benchmark(...)

5. benchmark_runner.py
   a. Pop step_username / step_password / pipeline_name from workload_params
      → pipeline_name stored on RunContext (never sent to OSB)
      → authenticate OSB as step_username for this step only
   b. Write workload-params.json (credential-free)
   c. Run:  opensearch-benchmark run
              --client-options basic_auth_user:<user>,...
              --workload-params workload-params.json

6. _download_artifacts: enrich test_run.json with run_context
   → gated on pipeline_name.startswith("acl-")
   → adds username, visible_doc_count, index, engine, query params

7. Results land in /results/<job_id>/<engine>/<scenario_key>/
```

### Per-Step Authentication

The key mechanism for per-step DLS isolation:

```jsonc
// pipeline step (e.g. in acl-sweep-1m.json)
{
  "dataset": "cohere-wiki-en-768",
  "scenario": "vector-search",
  "label": "dls-role-group",
  "params": {
    "query_k": 10,
    "hnsw_ef_search": 128,
    "step_username": "user-role-group",
    "step_password": "BenchmarkACL-2024!",
  },
}
```

`run-pipeline.sh` hoists `step_username`/`step_password` out of `params` before forwarding to the API. `benchmark_runner.py` pops them from `workload_params` and injects them into `--client-options` only — they are never written to `workload-params.json`. Steps without `step_username` fall back to job-level credentials (`admin/admin`).

## Cluster Setup

`acl-setup.sh <namespace>` runs three sub-scripts in order:

```
acl-setup.sh os-jvector-acl
  │
  ├─ acl-ingest-pipeline.sh  → PUT /_ingest/pipeline/acl_guard
  │                             PUT /_index_template/acl-benchmark-template
  │                             (attaches acl_guard as default_pipeline)
  │
  ├─ acl-roles.sh            → PUT /_plugins/_security/api/roles/<name>
  │                             11 roles, one per user, with DLS filter
  │
  └─ acl-users.sh            → PUT /_plugins/_security/api/internalusers/<name>
                                11 users with backend_roles + role mappings
```
