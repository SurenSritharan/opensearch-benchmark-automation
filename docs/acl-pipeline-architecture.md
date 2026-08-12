# ACL Pipeline Architecture

This document describes how the Query Time ACL benchmarking system works end-to-end — from cluster setup through ingest, DLS role assignment, pipeline execution, and result collection.

## Overview

The ACL benchmark measures the latency overhead introduced by Document Level Security (DLS) queries. DLS injects a per-user filter into every search query at the security plugin layer before the query reaches the engine.

The system is designed so that:

- Every document in the corpus has ACL fields injected at ingest time by an OpenSearch ingest pipeline, simulating realistic production document metadata.
- **One dynamic DLS filter** (`dls-standard`) is shared by all non-baseline users. Each user's `backend_roles` and username simulate their production JWT claims, causing the filter to expand differently per user.
- Eight benchmark users represent realistic production personas — role-only, group-only, name-only, role+group, group+name, classified, and ultrastrict — spanning a visibility gradient from ~1% to 100%.
- The E1 baseline (`user-unrestricted`, no DLS) provides raw vector search cost. Every other user's overhead is delta-compared against this baseline.

```
┌─────────────────────────────────────────────────────────────────┐
│  Jenkins build                                                  │
│                                                                 │
│  1. acl-setup.sh       -> installs ingest pipeline + index      │
│                          template + 3 DLS roles + 8 users       │
│                                                                 │
│  2. run-pipeline.sh    -> submits pipeline steps as a batch     │
│                          job to the benchmark API               │
│                                                                 │
│  3. acl-setup.sh verify -> checks ACL fields on random docs     │
│                           and confirms DLS is restrictive       │
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

These are **functionally identical**. One DLS role definition with `${user.roles}` and `${user.name}` template variables serves all benchmark users. Each user sees a different document set based on their own `backend_roles` and username — exactly as different JWT payloads would behave in production.

Classification clearance is also encoded in `backend_roles`. A user with `"restricted"` in `backend_roles` has restricted clearance because `terms: security_classification: ${user.roles}` will expand to match docs where `security_classification = "restricted"`.

## ACL Data Model

Based on our production design, the following `keyword` fields are injected at ingest time by the `acl_guard` ingest pipeline:

| Field                     | Type              | Description                                                                                         |
| ------------------------- | ----------------- | --------------------------------------------------------------------------------------------------- |
| `access_roles`            | `keyword` (array) | Role names that grant read access; absent when the doc is not role-controlled                       |
| `access_groups`           | `keyword` (array) | Group names that grant read access; absent when the doc is not group-controlled                     |
| `access_users`            | `keyword` (array) | Named users that may read this doc; absent when the doc has no named-user ACL                       |
| `security_classification` | `keyword`         | `internal`, `external`, or `restricted`; absent on ~87% of docs (~13% carry a classification value) |

Not every document has all three `access_*` fields. A document carries only the access field(s) relevant to how it was shared — exactly as in production.

### Classification Semantics

- `security_classification` **absent**: any user whose access fields match can see the doc
- `security_classification` **present**: user must have that exact classification value in their `backend_roles` AND their access field must also match
- Non-hierarchical: `restricted` clearance does NOT grant access to `internal` docs

## Corpus Bucket Layout

The `acl_guard` ingest pipeline assigns ACL fields deterministically using `_id % 100`. Each bucket = 1% of corpus (~10k docs at 1M, ~50k docs at 5M). Documents receive only the access field(s) for their bucket segment.

### Access Value Vocabulary

| Corpus segment        | `access_roles` | `access_groups`  | `access_users`     | What it simulates                             |
| --------------------- | -------------- | ---------------- | ------------------ | --------------------------------------------- |
| Role-tagged docs      | `role-svc`     | —                | —                  | Docs accessible only via role                 |
| Finance-group docs    | —              | `grp-finance`    | —                  | Docs accessible only via group                |
| Named-user docs       | —              | —                | `user-name-only`   | Docs accessible only to a named individual    |
| Role+group docs       | `role-svc`     | `grp-finance`    | —                  | Docs accessible by role OR group              |
| Group+name docs       | —              | `grp-finance`    | `user-group-name`  | Docs accessible by group OR named user        |
| Restricted-group docs | —              | `grp-restricted` | —                  | Docs accessible via a narrow restricted group |
| Ultrastrict docs      | —              | —                | `user-ultrastrict` | Docs accessible only to a single named user   |

### Bucket Table

| Bucket Range | `access_roles` | `access_groups`  | `access_users`     | `security_classification` | Visible to                                                         | Buckets |
| ------------ | -------------- | ---------------- | ------------------ | ------------------------- | ------------------------------------------------------------------ | ------- |
| 0–26         | `role-svc`     | —                | —                  | —                         | role-only, role-group                                              | 27      |
| 27–29        | `role-svc`     | —                | —                  | `external`                | _(no user has external clearance)_                                 | 3       |
| 30–34        | `role-svc`     | —                | —                  | `internal`                | _(no user has internal clearance)_                                 | 5       |
| 35–64        | —              | `grp-finance`    | —                  | —                         | group-only, role-group, group-name                                 | 30      |
| 65–67        | —              | `grp-finance`    | —                  | `internal`                | _(no user has internal clearance)_                                 | 3       |
| 68–77        | —              | —                | `user-name-only`   | —                         | name-only                                                          | 10      |
| 78–82        | `role-svc`     | `grp-finance`    | —                  | —                         | role-only, group-only, role-group, group-name                      | 5       |
| 83–87        | —              | `grp-finance`    | `user-group-name`  | —                         | group-only, role-group, group-name                                 | 5       |
| 88–92        | —              | `grp-restricted` | —                  | —                         | classified                                                         | 5       |
| 93           | —              | `grp-restricted` | —                  | `restricted`              | classified (has restricted clearance)                              | 1       |
| 94–98        | —              | —                | `user-ultrastrict` | —                         | _(ultrastrict role requires classification — these are invisible)_ | 5       |
| 99           | —              | —                | `user-ultrastrict` | `restricted`              | ultrastrict                                                        | 1       |
| **Total**    |                |                  |                    |                           |                                                                    | **100** |

**Classification breakdown** (~13% of corpus):

- `external`: buckets 27–29 = 3 buckets _(no user has external clearance — tests that DLS blocks external docs correctly)_
- `internal`: buckets 30–34 + 65–67 = 8 buckets _(no user has internal clearance)_
- `restricted`: buckets 93 + 99 = 2 buckets _(user-classified has restricted clearance; user-ultrastrict requires it)_

## DLS Roles and Users

### 3 DLS Roles

| Role              | Users               | DLS filter                                                                 |
| ----------------- | ------------------- | -------------------------------------------------------------------------- |
| `dls-baseline`    | `user-unrestricted` | No filter — raw baseline                                                   |
| `dls-standard`    | 6 standard users    | Full dynamic filter with `${user.roles}` + `${user.name}`                  |
| `dls-ultrastrict` | `user-ultrastrict`  | Hard `access_users` + hard `classification=restricted`, no absent fallback |

**`dls-standard` filter:**

```json
{
  "bool": {
    "filter": [
      {
        "bool": {
          "should": [
            { "terms": { "access_roles": "${user.roles}" } },
            { "terms": { "access_groups": "${user.roles}" } },
            { "term": { "access_users": "${user.name}" } }
          ],
          "minimum_should_match": 1
        }
      },
      {
        "bool": {
          "should": [
            { "terms": { "security_classification": "${user.roles}" } },
            {
              "bool": {
                "must_not": { "exists": { "field": "security_classification" } }
              }
            }
          ],
          "minimum_should_match": 1
        }
      }
    ]
  }
}
```

### 8 User Personas

| User                | Persona                    | `backend_roles`                   | JWT equivalent              | ~Visible @ 1M |
| ------------------- | -------------------------- | --------------------------------- | --------------------------- | ------------- |
| `user-unrestricted` | Admin / no DLS             | `["grp-broad"]`                   | Superuser, bypasses DLS     | **~1M**       |
| `user-role-group`   | Power user                 | `["role-svc","grp-finance"]`      | Role + group memberships    | **~640k**     |
| `user-group-only`   | Team member                | `["grp-finance"]`                 | Has group, no roles         | **~400k**     |
| `user-group-name`   | Team member + named access | `["grp-finance"]`                 | Group + named on some docs  | **~400k**     |
| `user-role-only`    | Service account            | `["role-svc"]`                    | Has role, no groups         | **~320k**     |
| `user-name-only`    | Named individual           | `[]`                              | Identity match only         | **~100k**     |
| `user-classified`   | Cleared user               | `["grp-restricted","restricted"]` | Restricted team + clearance | **~60k**      |
| `user-ultrastrict`  | Hyper-restricted           | `[]`                              | Named user, classified only | **~10k**      |

All users share password `BenchmarkACL-2024!`.

## Pipeline Catalogue

### Sweep Pipelines (self-contained)

Build the index from scratch and run all 8 user personas × 3 k/ef = 24 search steps.

| Pipeline                                         | Corpus         | Steps                                                  |
| ------------------------------------------------ | -------------- | ------------------------------------------------------ |
| [`dls-sweep-1m`](../pipelines/dls-sweep-1m.json) | 1M wiki-en-768 | 6 build + 24 search (ordered least → most restrictive) |
| [`dls-sweep-5m`](../pipelines/dls-sweep-5m.json) | 5M wiki-en-768 | 6 build + 24 search (ordered least → most restrictive) |

Each search step uses 3 k/ef combinations:

| Label suffix | `query_k` | `hnsw_ef_search` |
| ------------ | --------- | ---------------- |
| `k10-ef128`  | 10        | 128              |
| `k10-ef256`  | 10        | 256              |
| `k100-ef128` | 100       | 128              |

### Individual Persona Pipelines (search-only)

Run a single persona across all three k/ef combinations. Requires the index to already exist.

| Pipeline                                                 | User                | Persona                      | ~Visible |
| -------------------------------------------------------- | ------------------- | ---------------------------- | -------- |
| [`dls-unrestricted`](../pipelines/dls-unrestricted.json) | `user-unrestricted` | No DLS baseline              | ~100%    |
| [`dls-role-group`](../pipelines/dls-role-group.json)     | `user-role-group`   | Role + group                 | ~64%     |
| [`dls-group-only`](../pipelines/dls-group-only.json)     | `user-group-only`   | Group match only             | ~40%     |
| [`dls-group-name`](../pipelines/dls-group-name.json)     | `user-group-name`   | Group + username             | ~40%     |
| [`dls-role-only`](../pipelines/dls-role-only.json)       | `user-role-only`    | Role match only              | ~32%     |
| [`dls-name-only`](../pipelines/dls-name-only.json)       | `user-name-only`    | Username match only          | ~10%     |
| [`dls-classified`](../pipelines/dls-classified.json)     | `user-classified`   | Group + restricted clearance | ~6%      |
| [`dls-ultrastrict`](../pipelines/dls-ultrastrict.json)   | `user-ultrastrict`  | Username + hard restricted   | ~1%      |

## How a Pipeline Run Works

```
run-pipeline.sh --pipeline dls-sweep-1m jvector
       │
       ▼
1. Read pipelines/dls-sweep-1m.json
   Replace __ENGINE__ → jvector

2. For each step:
   a. Merge pipeline params + step params
   b. Auto-derive target_index_name from corpus_size
   c. Hoist step_username / step_password to top-level
      (never forwarded to OSB as workload variables)

3. POST /api/v1/benchmark/batch  {engine, tests:[...]}
       │
       ▼
4. app.py (worker pod)
   For each test:
   a. Validate scenario exists in config/datasets.yaml
   b. Call benchmark_runner.run_benchmark(...)

5. benchmark_runner.py
   a. Pop step_username/step_password from workload_params
      → authenticate OSB as that user for this step only
   b. Write workload-params.json (credential-free)
   c. Run:  opensearch-benchmark run
              --client-options basic_auth_user:<user>,...
              --workload-params workload-params.json

6. Results land in /results/<job_id>/<engine>/<scenario_key>/
```

### Per-Step Authentication

The key mechanism for per-step DLS isolation:

```jsonc
// pipeline step
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

`run-pipeline.sh` hoists `step_username`/`step_password` out of `params` before forwarding to the API. `benchmark_runner.py` injects them into `--client-options` only. Steps without `step_username` fall back to job-level credentials (`admin/admin`).

## Jenkins Integration

ACL setup is triggered automatically for any pipeline whose name contains `-acl` or starts with `dls-`:

```groovy
if (pipeline.contains('-acl') || pipeline.startsWith('dls-')) {
    sh "gke-manifest/acl-setup.sh ${ns}"
}
```

**Full stage sequence for a DLS pipeline run:**

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

## Post-Ingest Verification

[`gke-manifest/acl-verify.sh`](../gke-manifest/acl-verify.sh) runs three checks after the benchmark completes:

| Check             | What it confirms                                                                                                                            |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| index settings    | `default_pipeline: acl_guard` is set on the index and `knn: true`                                                                           |
| random doc sample | 10 random docs show heterogeneous ACL field population — some have only `access_roles`, some only `access_groups`, some only `access_users` |
| DLS restriction   | `user-role-group` sees ~67% of docs and `user-ultrastrict` sees ~1%; pass requires `admin > role-group > ultrastrict > 0`                   |

Check C is the key correctness gate: if the counts do not satisfy the expected ordering, DLS is either not active or the ingest pipeline did not assign the correct access fields.

## Expected Visible Document Counts

At 1M corpus (100 buckets × ~10k docs each), ordered from least to most restrictive:

| User                         | Visible buckets    | Expected count |
| ---------------------------- | ------------------ | -------------- |
| `user-unrestricted` (no DLS) | 0–99 (all)         | ~1,000,000     |
| `user-role-group`            | 0–26, 35–64, 78–87 | ~640,000       |
| `user-group-only`            | 35–64, 78–87       | ~400,000       |
| `user-group-name`            | 35–64, 78–87       | ~400,000       |
| `user-role-only`             | 0–26, 78–82        | ~320,000       |
| `user-name-only`             | 68–77              | ~100,000       |
| `user-classified`            | 88–93              | ~60,000        |
| `user-ultrastrict`           | 99                 | ~10,000        |

> **Why role-group dropped from ~670k to ~640k**: Buckets 27–29 now carry `security_classification: external`. Since no user has `"external"` in their `backend_roles`, the classification clause blocks these 3 buckets (~30k docs) from all DLS users — verifying that the `external` classification gate works correctly.
