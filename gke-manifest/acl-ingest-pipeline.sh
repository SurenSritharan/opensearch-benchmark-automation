#!/usr/bin/env bash
# Installs the acl_guard ingest pipeline and the index template that attaches it.
#
# The pipeline assigns ACL fields to every document using _id % 100 buckets.
# Each bucket = 1% of corpus (~10k docs at 1M, ~50k docs at 5M).
#
# security_classification is NOT used. Access control is enforced purely through
# access_roles, access_groups, and access_users — all stored as keyword arrays.
# A document may carry values in one, two, or all three fields.
#
# Access value vocabulary:
#   access_roles:  role-svc, need-to-know
#   access_groups: grp-finance, grp-sensitive, need-to-know
#   access_users:  user-name-only, user-group-name,
#                  user-ultrastrict, user-ultrastrict-narrow,
#                  user-ultrastrict-micro, user-ultrastrict-fixed,
#                  + 9 noise users (user-noise-01 … user-noise-09) on large-list buckets
#
# Assignment order (first match wins — evaluated top-to-bottom in Painless):
#
#   TIER A — fixed-5k (id < 5000):
#     id < 5000 : access_users=[user-ultrastrict-fixed, user-noise-01..09]
#                 access_roles=[need-to-know]
#                 access_groups=[need-to-know]
#                 Exactly 5,000 docs. DLS requires all three fields to match.
#
#   TIER B — 0.6% (id >= 5000 AND id % 1000 < 6):
#     ~0.6% of corpus: access_users=[user-ultrastrict-micro, user-noise-01..09]
#                      access_roles=[need-to-know]
#                      access_groups=[need-to-know]
#                      DLS requires all three fields to match.
#
#   TIER C — bucket layout (id % 100), for id >= 5000 AND id % 1000 >= 6:
#     0–34  : access_roles=[role-svc]
#     35–67 : access_groups=[grp-finance]
#     68–77 : access_users=[user-name-only]
#     78–82 : access_roles=[role-svc], access_groups=[grp-finance]
#     83–87 : access_groups=[grp-finance], access_users=[user-group-name, user-noise-01..09]
#             (10 users in access_users — large-list test bucket)
#     88–93 : access_groups=[grp-sensitive]
#     94    : access_users=[user-ultrastrict-narrow, user-noise-01..09]
#             access_roles=[need-to-know]
#             access_groups=[need-to-know]
#             (~1% — isolated from user-ultrastrict)
#     95–99 : access_users=[user-ultrastrict, user-noise-01..09]
#             access_roles=[need-to-know]
#             access_groups=[need-to-know]
#             (~5% — ultrastrict bucket)
#
# Expected visible doc counts per DLS user at 1M (each standard bucket ~10k docs):
#   user-unrestricted       (no DLS)                   -> ~1M    (all docs)
#   user-role-group         (role-svc + grp-finance)   -> ~780k  (buckets 0–87)
#   user-group-only         (grp-finance)              -> ~430k  (buckets 35–87)
#   user-group-name         (grp-finance)              -> ~430k  (buckets 35–87)
#   user-role-only          (role-svc)                 -> ~400k  (buckets 0–34, 78–82)
#   user-name-only          (username match)           -> ~100k  (buckets 68–77)
#   user-group-sensitive         (grp-sensitive)           -> ~60k   (buckets 88–93)
#   user-ultrastrict        (need-to-know triple AND)  -> ~50k   (buckets 95–99)
#   user-ultrastrict-narrow (need-to-know triple AND)  -> ~10k   (bucket 94)
#   user-ultrastrict-micro  (need-to-know triple AND)  -> ~6k    (Tier B: id%1000<6 outside 5k)
#   user-ultrastrict-fixed  (need-to-know triple AND)  -> 5,000  (Tier A: id<5000)
#
# ${user.roles} and ${user.name} in DLS strings are OpenSearch Security template
# variables — NOT shell variables.

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
    "description": "Assigns ACL fields (access_roles, access_groups, access_users) to every document. No security_classification. Three ultrastrict tiers carved out first: fixed-5k (id<5000), micro-0.6% (id%1000<6), and bucket 94 (narrow ~1%). Ultrastrict docs carry all three access fields so the AND DLS filter can match. Standard buckets carry only the fields relevant to their persona.",
    "processors": [
      {
        "script": {
          "lang": "painless",
          "source": "long id = Long.parseLong(ctx._id); List noise = [\"user-noise-01\",\"user-noise-02\",\"user-noise-03\",\"user-noise-04\",\"user-noise-05\",\"user-noise-06\",\"user-noise-07\",\"user-noise-08\",\"user-noise-09\"]; if (id < 5000) { List u = new ArrayList(noise); u.add(0, \"user-ultrastrict-fixed\"); ctx.access_users = u; ctx.access_roles = [\"need-to-know\"]; ctx.access_groups = [\"need-to-know\"]; } else if (id % 1000 < 6) { List u = new ArrayList(noise); u.add(0, \"user-ultrastrict-micro\"); ctx.access_users = u; ctx.access_roles = [\"need-to-know\"]; ctx.access_groups = [\"need-to-know\"]; } else { int b = (int)(id % 100); if (b >= 0 && b <= 34) { ctx.access_roles = [\"role-svc\"]; } else if (b >= 35 && b <= 67) { ctx.access_groups = [\"grp-finance\"]; } else if (b >= 68 && b <= 77) { ctx.access_users = [\"user-name-only\"]; } else if (b >= 78 && b <= 82) { ctx.access_roles = [\"role-svc\"]; ctx.access_groups = [\"grp-finance\"]; } else if (b >= 83 && b <= 87) { ctx.access_groups = [\"grp-finance\"]; List u = new ArrayList(noise); u.add(0, \"user-group-name\"); ctx.access_users = u; } else if (b >= 88 && b <= 93) { ctx.access_groups = [\"grp-sensitive\"]; } else if (b == 94) { List u = new ArrayList(noise); u.add(0, \"user-ultrastrict-narrow\"); ctx.access_users = u; ctx.access_roles = [\"need-to-know\"]; ctx.access_groups = [\"need-to-know\"]; } else { List u = new ArrayList(noise); u.add(0, \"user-ultrastrict\"); ctx.access_users = u; ctx.access_roles = [\"need-to-know\"]; ctx.access_groups = [\"need-to-know\"]; } }"
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
          "access_roles":  { "type": "keyword" },
          "access_groups": { "type": "keyword" },
          "access_users":  { "type": "keyword" }
        }
      }
    }
  }'

echo "[acl-ingest-pipeline] Done."
