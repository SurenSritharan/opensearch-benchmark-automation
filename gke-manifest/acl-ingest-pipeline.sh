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
#   access_users:  user-name-only, user-group-name, user-ultrastrict
#   security_classification: internal, external, restricted
#
# Bucket layout (bucket = _id % 100):
#   0–26  : access_roles=[role-svc]                                          (no classification)
#   27–29 : access_roles=[role-svc]                                          classification=external
#   30–34 : access_roles=[role-svc]                                          classification=internal
#   35–64 : access_groups=[grp-finance]                                      (no classification)
#   65–67 : access_groups=[grp-finance]                                      classification=internal
#   68–77 : access_users=[user-name-only]                                    (no classification)
#   78–82 : access_roles=[role-svc], access_groups=[grp-finance]             (no classification)
#   83–87 : access_groups=[grp-finance], access_users=[user-group-name]      (no classification)
#   88–92 : access_groups=[grp-restricted]                                   (no classification)
#   93    : access_groups=[grp-restricted]                                   classification=restricted
#   94–98 : access_users=[user-ultrastrict]                                  (no classification)
#   99    : access_users=[user-ultrastrict]                                  classification=restricted
#
# Classification breakdown (~11% of corpus):
#   external:   buckets 27–29                                                = 3 buckets
#   internal:   buckets 30–34 + 65–67                                        = 8 buckets
#   restricted: buckets 93, 99                                               = 2 buckets
#   Total classified: 13 buckets (~13%).  No benchmark user holds external
#   or internal clearance — these docs are visible only to user-unrestricted
#   (admin/no-DLS). They verify the DLS filter correctly blocks classified
#   docs when the user lacks the matching clearance value in backend_roles.
#
# Expected visible doc counts per DLS user at 1M (each bucket ~10k docs):
#   user-unrestricted (no DLS)                         -> ~1M   (all buckets)
#   user-role-group   (role-svc + grp-finance)         -> ~640k (buckets 0–26, 35–64, 78–87; external+internal blocked)
#   user-group-only   (grp-finance)                    -> ~400k (buckets 35–64, 78–87; internal blocked)
#   user-group-name   (grp-finance + username match)   -> ~400k (buckets 35–64, 78–87)
#   user-role-only    (role-svc)                       -> ~320k (buckets 0–26, 78–82; external+internal blocked)
#   user-name-only    (username match)                 -> ~100k (buckets 68–77)
#   user-classified   (grp-restricted + restricted)    -> ~60k  (buckets 88–93)
#   user-ultrastrict  (username + restricted, no fall) -> ~10k  (bucket 99 only)

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
    "description": "Assigns heterogeneous ACL fields from _id mod 100. Each bucket sets only the access field(s) relevant to that segment, simulating realistic production document metadata.",
    "processors": [
      {
        "script": {
          "lang": "painless",
          "source": "long id = Long.parseLong(ctx._id); int b = (int)(id % 100); if (b >= 0 && b <= 26) { ctx.access_roles = [\"role-svc\"]; } else if (b >= 27 && b <= 29) { ctx.access_roles = [\"role-svc\"]; ctx.security_classification = \"external\"; } else if (b >= 30 && b <= 34) { ctx.access_roles = [\"role-svc\"]; ctx.security_classification = \"internal\"; } else if (b >= 35 && b <= 64) { ctx.access_groups = [\"grp-finance\"]; } else if (b >= 65 && b <= 67) { ctx.access_groups = [\"grp-finance\"]; ctx.security_classification = \"internal\"; } else if (b >= 68 && b <= 77) { ctx.access_users = [\"user-name-only\"]; } else if (b >= 78 && b <= 82) { ctx.access_roles = [\"role-svc\"]; ctx.access_groups = [\"grp-finance\"]; } else if (b >= 83 && b <= 87) { ctx.access_groups = [\"grp-finance\"]; ctx.access_users = [\"user-group-name\"]; } else if (b >= 88 && b <= 92) { ctx.access_groups = [\"grp-restricted\"]; } else if (b == 93) { ctx.access_groups = [\"grp-restricted\"]; ctx.security_classification = \"restricted\"; } else if (b >= 94 && b <= 98) { ctx.access_users = [\"user-ultrastrict\"]; } else { ctx.access_users = [\"user-ultrastrict\"]; ctx.security_classification = \"restricted\"; }"
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
