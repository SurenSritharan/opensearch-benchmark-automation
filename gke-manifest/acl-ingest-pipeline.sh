#!/usr/bin/env bash
# Installs the acl_guard ingest pipeline and the index template that attaches it.
#
# The pipeline assigns four ACL fields to every document using _id % 8 buckets:
#   tenant_id, access_groups, access_users, security_classification
#
# Bucket layout (~125k docs each at 1M corpus):
#   0: tenant_001  grp-finance + grp-controllers  internal
#   1: tenant_001  grp-finance                    internal
#   2: tenant_001  grp-controllers                internal
#   3: tenant_001  grp-finance  dave-contractor    (no classification)
#   4: tenant_002  grp-finance                    internal
#   5: tenant_002  grp-controllers                internal
#   6: tenant_001  grp-finance                    restricted
#   7: tenant_002  grp-finance                    (no classification)
#
# Expected visible counts per DLS role at 1M:
#   E1 no DLS              -> 1M
#   E2 tenant_001          -> ~625k  (buckets 0-3, 6)
#   E3 grp-finance/controllers -> ~1M (all buckets)
#   M1 tenant_001+grp-finance  -> ~500k (buckets 0,1,3,6)
#   M2 tenant_001+identity     -> ~500k (buckets 0,1,3,6)
#   M3 tenant_001+identity+classification -> ~375k (buckets 0,1,3)
#   C1 tenant_001+user+group   -> 0 (user-c1 not in corpus access_users)
#   C3 tenant_001+50-user list -> 0 (no corpus user in static list)
#   C5 tenant_001/002+identity+classification -> ~625k (buckets 0,1,3,4,7)
#   C8 tenant_001+dave-contractor+no-classification -> ~125k (bucket 3)

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
    "description": "Assigns tenant_id, access_groups, access_users, security_classification from _id mod 8.",
    "processors": [
      { "lowercase": { "field": "access_roles",  "ignore_missing": true } },
      { "trim":      { "field": "access_roles",  "ignore_missing": true } },
      { "lowercase": { "field": "access_groups", "ignore_missing": true } },
      { "trim":      { "field": "access_groups", "ignore_missing": true } },
      { "lowercase": { "field": "access_users",  "ignore_missing": true } },
      { "trim":      { "field": "access_users",  "ignore_missing": true } },
      {
        "script": {
          "lang": "painless",
          "source": "for (def f : [\"access_roles\",\"access_groups\",\"access_users\"]) { if (ctx.containsKey(f) && ctx[f] != null) { ctx[f].removeIf(v -> v == null || v == \"\"); } } boolean missing = (ctx.access_roles == null || ctx.access_roles.isEmpty()) && (ctx.access_groups == null || ctx.access_groups.isEmpty()) && (ctx.access_users == null || ctx.access_users.isEmpty()); if (missing) { long id = Long.parseLong(ctx._id); int b = (int)(id % 8); if (b == 0) { ctx.tenant_id = \"tenant_001\"; ctx.access_groups = [\"grp-finance\",\"grp-controllers\"]; ctx.security_classification = \"internal\"; } else if (b == 1) { ctx.tenant_id = \"tenant_001\"; ctx.access_groups = [\"grp-finance\"]; ctx.security_classification = \"internal\"; } else if (b == 2) { ctx.tenant_id = \"tenant_001\"; ctx.access_groups = [\"grp-controllers\"]; ctx.security_classification = \"internal\"; } else if (b == 3) { ctx.tenant_id = \"tenant_001\"; ctx.access_groups = [\"grp-finance\"]; ctx.access_users = [\"dave-contractor\"]; } else if (b == 4) { ctx.tenant_id = \"tenant_002\"; ctx.access_groups = [\"grp-finance\"]; ctx.security_classification = \"internal\"; } else if (b == 5) { ctx.tenant_id = \"tenant_002\"; ctx.access_groups = [\"grp-controllers\"]; ctx.security_classification = \"internal\"; } else if (b == 6) { ctx.tenant_id = \"tenant_001\"; ctx.access_groups = [\"grp-finance\"]; ctx.security_classification = \"restricted\"; } else { ctx.tenant_id = \"tenant_002\"; ctx.access_groups = [\"grp-finance\"]; } }"
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
          "tenant_id":               { "type": "keyword" },
          "access_groups":           { "type": "keyword" },
          "access_roles":            { "type": "keyword" },
          "access_users":            { "type": "keyword" },
          "security_classification": { "type": "keyword" }
        }
      }
    }
  }'

echo "[acl-ingest-pipeline] Done."
