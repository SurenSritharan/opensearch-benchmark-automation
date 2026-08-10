# ACL Pipeline Architecture

This document describes how Query Time ACL benchmarking system works end-to-end — from cluster setup through ingest, DLS role assignment, pipeline execution, and result collection.

## Overview

The ACL benchmark measures the latency overhead introduced by DLS queries. DLS injects a per-user filter into every search query at the security plugin layer before the query reaches the engine.

The system is designed so that:

- Every document in the corpus has realistic ACL fields (`tenant_id`, `access_groups`, `access_users`, `access_roles`, `security_classification`) injected at ingest time by an OpenSearch ingest pipeline.
- Ten isolated OpenSearch users are pre-created, each mapped to exactly one DLS role so that role combinations never bleed into each other.
- Each benchmark pipeline step authenticates as a specific user, so only that user's DLS filter is active during measurement.
- The E1 baseline (no DLS, `user-e1`) provides the raw vector-search cost against which every other role's overhead is delta-compared.

```
┌─────────────────────────────────────────────────────────────────┐
│  Jenkins build                                                  │
│                                                                 │
│  1. acl-setup.sh       -> installs ingest pipeline + index      |
│                          template + 10 DLS roles + 10 users     │
│                                                                 │
│  2. run-pipeline.sh    -> submits pipeline steps as a batch     │
│                          job to the benchmark API               │
│                                                                 │
│  3. acl-setup.sh verify -> checks ACL fields on random docs     │
│                           and confirms DLS is restrictive       │
└─────────────────────────────────────────────────────────────────┘
```

## ACL data model

Based on our PRD, following `keyword` fields are added to every document by the `acl_guard` ingest pipeline and mapped by the `acl-benchmark-template` index template:

| Field                     | Type              | Purpose                                                                                |
| ------------------------- | ----------------- | -------------------------------------------------------------------------------------- |
| `tenant_id`               | `keyword`         | Tenant boundary — a DLS `term` filter on this field is the cheapest possible predicate |
| `access_groups`           | `keyword` (array) | Groups that can read the document                                                      |
| `access_roles`            | `keyword` (array) | Roles that can read the document                                                       |
| `access_users`            | `keyword` (array) | Named users that can read the document                                                 |
| `security_classification` | `keyword`         | Classification label (`internal`, `restricted`); absent means unclassified             |

## Corpus bucket layout

The `acl_guard` ingest pipeline assigns ACL fields deterministically using `_id % 8` so the distribution is uniform and reproducible across any corpus size. At 1M documents each bucket contains ~125 k docs.

| Bucket (`_id % 8`) | `tenant_id`  | `access_groups`                  | `access_users`    | `security_classification` |
| ------------------ | ------------ | -------------------------------- | ----------------- | ------------------------- |
| 0                  | `tenant_001` | `grp-finance`, `grp-controllers` | —                 | `internal`                |
| 1                  | `tenant_001` | `grp-finance`                    | —                 | `internal`                |
| 2                  | `tenant_001` | `grp-controllers`                | —                 | `internal`                |
| 3                  | `tenant_001` | `grp-finance`                    | `dave-contractor` | _(absent)_                |
| 4                  | `tenant_002` | `grp-finance`                    | —                 | `internal`                |
| 5                  | `tenant_002` | `grp-controllers`                | —                 | `internal`                |
| 6                  | `tenant_001` | `grp-finance`                    | —                 | `restricted`              |
| 7                  | `tenant_002` | `grp-finance`                    | —                 | _(absent)_                |

_For benchmarking only: The ingest pipeline only assigns fields when the document arrives without any ACL fields already set. If any of `access_roles`, `access_groups`, or `access_users` is non-empty the document is passed through unchanged — this lets future corpus files carry their own ACL metadata. This is because, workload contains only the vectors and we need other fields for Query Time ACL._

## _For production: The ingest pipeline will be our validation at ingestion time(opposite to benchmarking use case)._

## Setup scripts

All setup is orchestrated by [`gke-manifest/acl-setup.sh`](../gke-manifest/acl-setup.sh). It is called automatically by the Jenkins pipeline for any pipeline whose name contains `-acl` or starts with `dls-`.

```
acl-setup.sh <namespace>          # full setup
acl-setup.sh <namespace> verify   # post-ingest verification only
```

Internally it calls three sub-scripts in order:

| Script                                                             | What it does                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`acl-ingest-pipeline.sh`](../gke-manifest/acl-ingest-pipeline.sh) | `PUT`s the `acl_guard` ingest pipeline and the `acl-benchmark-template` index template. The template sets `default_pipeline: acl_guard` on all `cohere-wiki-en-768-*` and `cohere-msmarco-1024-*` indices so ACL fields are stamped on every document at bulk-ingest time without requiring changes to the OSB workload. |
| [`acl-roles.sh`](../gke-manifest/acl-roles.sh)                     | Creates 10 DLS roles via `PUT /_plugins/_security/api/roles/<name>` and maps each role to exactly one user.                                                                                                                                                                                                              |
| [`acl-users.sh`](../gke-manifest/acl-users.sh)                     | Creates 10 internal users via `PUT /_plugins/_security/api/internalusers/<name>`. All users share the password `BenchmarkACL-2024!`. Users whose DLS role uses `${user.roles}` receive a `backend_role` of `grp-finance` so OpenSearch can expand the template variable.                                                 |

All three scripts are **idempotent** — every call is a `PUT`, so re-running
setup on an existing cluster is safe.

## DLS roles and users

Each user is mapped to exactly **one** role. OpenSearch merges DLS filters across roles with `OR`, so holding multiple roles would make the most-permissive filter win and corrupt the measurement.

| Tier     | Role name                      | User              | DLS filter summary                                            | `backend_roles` needed |
| -------- | ------------------------------ | ----------------- | ------------------------------------------------------------- | ---------------------- |
| Easy     | `test_baseline`                | `user-e1`         | No DLS — raw baseline                                         | No                     |
| Easy     | `test_tenant_only`             | `user-e2`         | `term tenant_id:tenant_001`                                   | No                     |
| Easy     | `test_static_groups`           | `user-e3`         | `terms access_groups:[grp-finance, grp-controllers]`          | No                     |
| Moderate | `test_tenant_group`            | `user-m1`         | `tenant_001 AND grp-finance`                                  | No                     |
| Moderate | `test_full_identity`           | `user-m2`         | `tenant_001 AND (groups OR roles OR user.name)`               | `grp-finance`          |
| Moderate | `test_identity_classification` | `user-m3`         | M2 filter + `classification=internal OR absent`               | `grp-finance`          |
| Complex  | `test_dual_validation`         | `user-c1`         | `tenant_001 AND user.name AND user.roles` (AND, not OR)       | `grp-finance`          |
| Complex  | `test_large_users_list`        | `user-c3`         | `tenant_001 AND terms access_users:[50-entry list]`           | No                     |
| Complex  | `test_multi_tenant`            | `user-c5`         | `(tenant_001 OR tenant_002) AND identity AND classification`  | `grp-finance`          |
| Complex  | `test_no_classification`       | `dave-contractor` | `tenant_001 AND user.name AND must_not exists classification` | No                     |

## Pipeline catalogue

### Sweep pipelines (self-contained)

These pipelines build the index from scratch and then run all DLS role variants. No prior state is required.

| Pipeline                                         | Corpus         | Steps                                                      |
| ------------------------------------------------ | -------------- | ---------------------------------------------------------- |
| [`dls-sweep-1m`](../pipelines/dls-sweep-1m.json) | 1M wiki-en-768 | 6 build steps + 30 search steps (10 roles × 3 k/ef combos) |
| [`dls-sweep-5m`](../pipelines/dls-sweep-5m.json) | 5M wiki-en-768 | 6 build steps + 30 search steps (10 roles × 3 k/ef combos) |

Each search step authenticates as a different user so only that role's DLS filter is active. The three `k` & `ef` combos per role are:

| Label suffix | `query_k` | `hnsw_ef_search` |
| ------------ | --------- | ---------------- |
| `k10-ef128`  | 10        | 128              |
| `k10-ef256`  | 10        | 256              |
| `k100-ef128` | 100       | 128              |

### Individual role pipelines (search-only)

These pipelines run a single DLS role across all three combinations. They require the index to already exist from a prior sweep or ACL complete run.

| Pipeline                                                                             | User              | DLS tier                             |
| ------------------------------------------------------------------------------------ | ----------------- | ------------------------------------ |
| [`dls-e1-baseline`](../pipelines/dls-e1-baseline.json)                               | `user-e1`         | Easy — no DLS                        |
| [`dls-e2-tenant-only`](../pipelines/dls-e2-tenant-only.json)                         | `user-e2`         | Easy — single term                   |
| [`dls-e3-static-groups`](../pipelines/dls-e3-static-groups.json)                     | `user-e3`         | Easy — static terms                  |
| [`dls-m1-tenant-group`](../pipelines/dls-m1-tenant-group.json)                       | `user-m1`         | Moderate — 2-clause bool             |
| [`dls-m2-full-identity`](../pipelines/dls-m2-full-identity.json)                     | `user-m2`         | Moderate — dynamic identity          |
| [`dls-m3-identity-classification`](../pipelines/dls-m3-identity-classification.json) | `user-m3`         | Moderate — identity + classification |
| [`dls-c1-dual-validation`](../pipelines/dls-c1-dual-validation.json)                 | `user-c1`         | Complex — AND validation             |
| [`dls-c3-large-users-list`](../pipelines/dls-c3-large-users-list.json)               | `user-c3`         | Complex — 50-entry terms list        |
| [`dls-c5-multi-tenant`](../pipelines/dls-c5-multi-tenant.json)                       | `user-c5`         | Complex — multi-tenant               |
| [`dls-c8-no-classification`](../pipelines/dls-c8-no-classification.json)             | `dave-contractor` | Complex — must_not exists            |

### ACL complete / smoke pipelines

These pipelines build the index and run search as `user-e1` (no DLS filter) to measure the raw cost of operating under the ACL index template.

| Pipeline                                                                   | Corpus            | Description                            |
| -------------------------------------------------------------------------- | ----------------- | -------------------------------------- |
| [`complete-1m-acl`](../pipelines/complete-1m-acl.json)                     | 1M wiki + msmarco | Full build + search on both datasets   |
| [`complete-1m-acl-wiki-only`](../pipelines/complete-1m-acl-wiki-only.json) | 1M wiki only      | Smoke test — quick single-dataset run  |
| [`search-1m-acl`](../pipelines/search-1m-acl.json)                         | 1M wiki + msmarco | Search-only (index must already exist) |

## How a pipeline run works

```
run-pipeline.sh --pipeline dls-sweep-1m jvector
       │
       ▼
1. Read pipelines/dls-sweep-1m.json
   Replace __ENGINE__ → jvector
   Read top-level "params" (corpus_size, etc.)

2. For each step in "steps":
   a. Merge pipeline params + step params
   b. Auto-derive target_index_name from corpus_size
   c. Auto-derive num_vectors for ingest/search steps
   d. Build label:  wiki-en-768-1m-vector-search-k10   (unique per step)
   e. Hoist step_username / step_password to top-level
      (never forwarded to OSB as workload variables)

3. POST /api/v1/benchmark/batch  {engine, tests:[...]}
       │
       ▼
4. app.py  (worker pod)
   For each test:
   a. Validate scenario exists in config/datasets.yaml
   b. Merge params from datasets.yaml defaults
   c. Call benchmark_runner.run_benchmark(scenario=procedure_name,
                                          workload_params=merged_params)

5. benchmark_runner.py
   a. Pop step_username/step_password from workload_params
      → authenticate OSB as that user for this step only
   b. Write workload-params.json (credential-free)
   c. Run:  opensearch-benchmark run
              --workload-path ...
              --test-procedure vector-search
              --client-options basic_auth_user:<user>,...
              --workload-params workload-params.json

6. Results land in /results/<job_id>/<engine>/<scenario_key>/
   Copied incrementally to Jenkins workspace by run-pipeline.sh
   as each scenario completes.
```

### Per-step authentication

The key mechanism that enables per-step DLS isolation:

```jsonc
// pipeline step
{
  "dataset": "cohere-wiki-en-768",
  "scenario": "vector-search",
  "label": "dls-m3-identity-cls-k10-ef128",
  "params": {
    "query_k": 10,
    "hnsw_ef_search": 128,
    "step_username": "user-m3",
    "step_password": "BenchmarkACL-2024!",
  },
}
```

`run-pipeline.sh` hoists `step_username` / `step_password` out of `params` before forwarding to the API, so they are stored on the scenario entry but **never sent to OSB as workload variables**. `benchmark_runner.py` pops them from `workload_params` before writing `workload-params.json` and injects them into `--client-options` instead. Steps without `step_username` fall back to the job-level credentials (default `admin/admin`).

## Jenkins integration

ACL setup is triggered automatically for any pipeline whose name contains `-acl` or starts with `dls-`:

```groovy
if (pipeline.contains('-acl') || pipeline.startsWith('dls-')) {
    sh "gke-manifest/acl-setup.sh ${ns}"
}
```

The setup runs **after** the OpenSearch cluster is confirmed green and the benchmark worker pod is confirmed `Ready` — both are prerequisites because `acl-setup.sh` routes all `curl` calls through `kubectl exec` on the worker pod.

**Full stage sequence for an ACL pipeline run:**

```
Prepare
  └─ write kubeconfig, chmod scripts, sync TLS certs

Prepare Workers
  └─ scale up API server + benchmark workers (parallel)

Verify Health
  └─ list pods, curl /health

Seed Dataset Cache
  └─ download GCS corpus files to worker PVCs (skips if present)

Run Benchmarks  (per engine, in parallel)
  ├─ a)  scale up / deploy OpenSearch cluster
  ├─ a2) acl-setup.sh <ns>          ← install pipeline + roles + users
  ├─ b)  run-pipeline.sh --pipeline dls-sweep-1m <engine>
  ├─ b2) acl-setup.sh <ns> verify   ← confirm ACL fields + DLS restriction
  ├─ c)  fetch & save results
  └─ d)  collect server logs + telemetry
```

## Post-ingest verification (Temporary verification. To be removed once stable as to reduce the time)

[`gke-manifest/acl-verify.sh`](../gke-manifest/acl-verify.sh) runs three checks after the benchmark completes:

| Check                     | What it confirms                                                          |
| ------------------------- | ------------------------------------------------------------------------- |
| **A** — index settings    | `default_pipeline: acl_guard` is set on the index and `knn: true`         |
| **B** — random doc sample | 10 random documents have all four ACL fields populated                    |
| **C** — DLS restriction   | `user-m2` sees fewer documents than `admin` (DLS is active and selective) |

Check C is the key correctness gate: if admin and `user-m2` return the same
count, DLS is not being applied and the benchmark results are invalid.

## Expected visible document counts

At 1M corpus (8 buckets × ~125 k docs each):

| Role                                                      | User              | Visible buckets                 | Expected count |
| --------------------------------------------------------- | ----------------- | ------------------------------- | -------------- |
| E1 no DLS                                                 | `user-e1`         | all                             | ~1 M           |
| E2 tenant_001                                             | `user-e2`         | 0, 1, 2, 3, 6                   | ~625 k         |
| E3 grp-finance or grp-controllers                         | `user-e3`         | all                             | ~1 M           |
| M1 tenant_001 + grp-finance                               | `user-m1`         | 0, 1, 3, 6                      | ~500 k         |
| M2 tenant_001 + identity                                  | `user-m2`         | 0, 1, 3, 6                      | ~500 k         |
| M3 M2 + classification=internal                           | `user-m3`         | 0, 1, 3                         | ~375 k         |
| C1 tenant_001 + user AND group                            | `user-c1`         | none (user-c1 not in corpus)    | ~0             |
| C3 tenant_001 + 50-user static list                       | `user-c3`         | none (list has no corpus users) | ~0             |
| C5 (tenant_001 OR tenant_002) + identity + classification | `user-c5`         | 0, 1, 3, 4, 7                   | ~625 k         |
| C8 tenant_001 + dave-contractor + no classification       | `dave-contractor` | 3                               | ~125 k         |

C1 and C3 returning zero documents is intentional — it measures the DLS filter evaluation cost when the filter is maximally selective (no matches), which represents the worst-case overhead scenario.
