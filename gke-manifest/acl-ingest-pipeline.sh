#!/usr/bin/env bash
# Installs the acl_guard ingest pipeline and the index template that attaches it.
#
# The pipeline assigns ACL fields to every document using _id % 100 buckets.
# Each bucket = 1% of corpus (~10k docs at 1M, ~50k docs at 5M).
#
# Documents receive ONLY the access field(s) relevant to their bucket — not all
# three fields on every doc. This mirrors production where documents carry only
# the access metadata set at write time.
#
# Access value vocabulary:
#   access_roles:  role-svc
#   access_groups: grp-finance, grp-restricted
#   access_users:  user-name-only, user-group-name, user-ultrastrict,
#                  user-ultrastrict-narrow, user-ultrastrict-micro, user-ultrastrict-fixed
#   security_classification: internal, external, restricted
#
# Assignment order (first match wins — evaluated top-to-bottom in Painless):
#
#   TIER A — fixed-5k (id < 5000):
#     id < 5000 : access_users=[user-ultrastrict-fixed]   classification=restricted
#                 Exactly 5,000 docs regardless of corpus size.
#
#   TIER B — 0.6% (id >= 5000 AND id % 1000 < 6):
#     ~0.6% of corpus: access_users=[user-ultrastrict-micro]  classification=restricted
#     Uses modulo-1000 sub-sampling on docs outside the fixed-5k window.
#
#   TIER C — bucket layout (id % 100), for id >= 5000 AND id % 1000 >= 6:
#     0–26  : access_roles=[role-svc]                                          (no classification)
#     27–29 : access_roles=[role-svc]                                          classification=external
#     30–34 : access_roles=[role-svc]                                          classification=internal
#     35–64 : access_groups=[grp-finance]                                      (no classification)
#     65–67 : access_groups=[grp-finance]                                      classification=internal
#     68–77 : access_users=[user-name-only]                                    (no classification)
#     78–82 : access_roles=[role-svc], access_groups=[grp-finance]             (no classification)
#     83–87 : access_groups=[grp-finance], access_users=[user-group-name]      (no classification)
#     88–92 : access_groups=[grp-restricted]                                   (no classification)
#     93    : access_groups=[grp-restricted]                                   classification=restricted
#     94    : access_users=[user-ultrastrict-narrow]                             classification=restricted  (~1%)
#     95–98 : access_users=[user-ultrastrict]                                  (no classification)
#     99    : access_users=[user-ultrastrict]                                  classification=restricted
#
# Classification breakdown:
#   external:   buckets 27–29                                                = 3 buckets
#   internal:   buckets 30–34 + 65–67                                        = 8 buckets
#   restricted: tier-A (5k docs) + tier-B (~0.6%) + bucket 93 + bucket 94 + bucket 99
#
# Expected visible doc counts per DLS user at 1M (each bucket ~10k docs):
#   user-unrestricted        (no DLS)                          -> ~1M    (all docs)
#   user-role-group          (role-svc + grp-finance)          -> ~640k  (buckets 0–26, 35–64, 78–87; classified blocked)
#   user-group-only          (grp-finance)                     -> ~400k  (buckets 35–64, 78–87; internal blocked)
#   user-group-name          (grp-finance + username match)    -> ~400k  (buckets 35–64, 78–87)
#   user-role-only           (role-svc)                        -> ~320k  (buckets 0–26, 78–82; classified blocked)
#   user-name-only           (username match)                  -> ~100k  (buckets 68–77)
#   user-classified          (grp-restricted + restricted)     -> ~60k   (buckets 88–93)
#   user-ultrastrict         (username + restricted, no fall)  -> ~10k   (buckets 95–98 no-class + bucket 99 restricted)
#   user-ultrastrict-narrow    (username + restricted, no fall)  -> ~10k   (bucket 94 only, ~1%)
#   user-ultrastrict-micro   (username + restricted, no fall)  -> ~6k    (id%1000<6 outside first 5k, ~0.6%)
#   user-ultrastrict-fixed      (username + restricted, no fall)  -> 5,000  (id<5000, fixed regardless of corpus)

set -euo pipefail

OS_HOST="$1"

os_api() {
    kubectl exec -n "$WORKER_NS" "$WORKER_POD" -c worker -- \
        curl -sk -u admin:admin "$@"
}

echo "[acl-ingest-pipeline] Installing acl_guard pipeline..."

os_api -X PUT "https://${OS_HOST}/_ingest/pipeline/acl_guard" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Assigns heterogeneous ACL fields to every document. Three ultrastrict sub-tiers are carved out before the standard bucket layout: fixed-5k (id<5000), 0.6% (id%1000<6 outside fixed-5k), 1pct (bucket 94). Remaining buckets follow the standard _id%100 layout.",
    "processors": [
      {
        "script": {
          "lang": "painless",
          "source": "long id = Long.parseLong(ctx._id); if (id < 5000) { ctx.access_users = [\"user-ultrastrict-fixed\"]; ctx.security_classification = \"restricted\"; } else if (id % 1000 < 6) { ctx.access_users = [\"user-ultrastrict-micro\"]; ctx.security_classification = \"restricted\"; } else { int b = (int)(id % 100); if (b >= 0 && b <= 26) { ctx.access_roles = [\"role-svc\"]; } else if (b >= 27 && b <= 29) { ctx.access_roles = [\"role-svc\"]; ctx.security_classification = \"external\"; } else if (b >= 30 && b <= 34) { ctx.access_roles = [\"role-svc\"]; ctx.security_classification = \"internal\"; } else if (b >= 35 && b <= 64) { ctx.access_groups = [\"grp-finance\"]; } else if (b >= 65 && b <= 67) { ctx.access_groups = [\"grp-finance\"]; ctx.security_classification = \"internal\"; } else if (b >= 68 && b <= 77) { ctx.access_users = [\"user-name-only\"]; } else if (b >= 78 && b <= 82) { ctx.access_roles = [\"role-svc\"]; ctx.access_groups = [\"grp-finance\"]; } else if (b >= 83 && b <= 87) { ctx.access_groups = [\"grp-finance\"]; ctx.access_users = [\"user-group-name\"]; } else if (b >= 88 && b <= 92) { ctx.access_groups = [\"grp-restricted\"]; } else if (b == 93) { ctx.access_groups = [\"grp-restricted\"]; ctx.security_classification = \"restricted\"; } else if (b == 94) { ctx.access_users = [\"user-ultrastrict-narrow\"]; ctx.security_classification = \"restricted\"; } else if (b >= 95 && b <= 98) { ctx.access_users = [\"user-ultrastrict\"]; } else { ctx.access_users = [\"user-ultrastrict\"]; ctx.security_classification = \"restricted\"; } }"
        }
      }
    ]
  }'

echo "[acl-ingest-pipeline] Installing index template..."

os_api -X PUT "https://${OS_HOST}/_index_template/acl-benchmark-template" \
  -H "Content-Type: application/json" \
  -d '{
    "index_patterns": ["cohere-wiki-en-768-*", "cohere-msmarco-1024-*"],
    "priority": 200,
    "template": {
      "settings": {
        "index": { "default_pipeline": "acl_guard" }
      },
      "mappings": {
        "properties": {
          "access_roles":            { "type": "keyword" },
          "access_groups":           { "type": "keyword" },
          "access_users":            { "type": "keyword" },
          "security_classification": { "type": "keyword" }
        }
      }
    }
  }'

echo "[acl-ingest-pipeline] Done."
