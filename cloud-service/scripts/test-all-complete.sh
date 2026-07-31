#!/bin/bash
# Complete end-to-end batch test for both datasets at both sizes.
# Runs all four pipelines sequentially in a single job:
#   cohere-wiki-en-768   @ 1M: create-index, bulk-ingest, refresh, force-merge, search k=100, search k=10
#   cohere-wiki-en-768   @ 5M: create-index, bulk-ingest, refresh, force-merge, search k=100, search k=10
#   cohere-msmarco-1024  @ 1M: create-index, bulk-ingest, refresh, force-merge, search k=100, search k=10
#   cohere-msmarco-1024  @ 5M: create-index, bulk-ingest, refresh, force-merge, search k=100, search k=10
# Requires: indexes do NOT exist yet (create-index will build them)

set -e

API_URL="${API_URL:-http://34.132.114.18}"

echo "=========================================="
echo "Complete Batch Test - Both Datasets (1M + 5M)"
echo "=========================================="
echo "API URL: $API_URL"
echo ""
echo "Test Flow:"
echo "  cohere-wiki-en-768 @ 1M:  create + ingest + refresh + force-merge + search-k100 + search-k10"
echo "  cohere-wiki-en-768 @ 5M:  create + ingest + refresh + force-merge + search-k100 + search-k10"
echo "  cohere-msmarco-1024 @ 1M: create + ingest + refresh + force-merge + search-k100 + search-k10"
echo "  cohere-msmarco-1024 @ 5M: create + ingest + refresh + force-merge + search-k100 + search-k10"
echo ""
echo "Expected: 4 x (1+1+1+1+5+5) = 56 sweeps total"
echo ""

PAYLOAD=$(cat <<'EOF'
{
  "engine": "jvector",
  "tests": [
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "delete-index",
      "label": "wiki-1m-delete-index",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "create-index",
      "label": "wiki-1m-create-index",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "target_index_body": "indices/index.json",
        "target_field_name": "target_field",
        "target_index_dimension": 768,
        "target_index_space_type": "innerproduct",
        "engine": "jvector",
        "method_name": "disk_ann",
        "mode": "on_disk",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "max_merged_segment": "25gb"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "bulk-ingest-data",
      "label": "wiki-1m-bulk-ingest",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "target_index_bulk_size": 5000,
        "target_index_bulk_indexing_clients": 8,
        "num_vectors": 1000000
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "refresh-index",
      "label": "wiki-1m-refresh-after-ingest",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "force-merge",
      "label": "wiki-1m-force-merge",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "target_index_max_num_segments": 1
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "refresh-index",
      "label": "wiki-1m-refresh-after-merge",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "wiki-1m-search-k100",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "num_vectors": 1000000,
        "query_k": 100,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "wiki-1m-search-k10",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "num_vectors": 1000000,
        "query_k": 10,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "delete-index",
      "label": "wiki-5m-delete-index",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "create-index",
      "label": "wiki-5m-create-index",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "target_index_body": "indices/index.json",
        "target_field_name": "target_field",
        "target_index_dimension": 768,
        "target_index_space_type": "innerproduct",
        "engine": "jvector",
        "method_name": "disk_ann",
        "mode": "on_disk",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "max_merged_segment": "25gb"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "bulk-ingest-data",
      "label": "wiki-5m-bulk-ingest",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "target_index_bulk_size": 5000,
        "target_index_bulk_indexing_clients": 8,
        "num_vectors": 5000000
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "refresh-index",
      "label": "wiki-5m-refresh-after-ingest",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "force-merge",
      "label": "wiki-5m-force-merge",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "target_index_max_num_segments": 1
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "refresh-index",
      "label": "wiki-5m-refresh-after-merge",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "wiki-5m-search-k100",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "num_vectors": 5000000,
        "query_k": 100,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "wiki-5m-search-k10",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "num_vectors": 5000000,
        "query_k": 10,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "delete-index",
      "label": "msmarco-1m-delete-index",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "create-index",
      "label": "msmarco-1m-create-index",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m",
        "target_index_body": "indices/index.json",
        "target_field_name": "vector",
        "target_index_dimension": 1024,
        "target_index_space_type": "cosinesimil",
        "engine": "jvector",
        "method_name": "disk_ann",
        "mode": "on_disk",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "hnsw_ef_construction": 256,
        "hnsw_m": 32,
        "max_merged_segment": "25gb"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "bulk-ingest-data",
      "label": "msmarco-1m-bulk-ingest",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m",
        "target_index_bulk_index_data_set_path": "/datasets/msmarco/cohere_msmarco_base_1m.fvec",
        "target_index_bulk_size": 5000,
        "target_index_bulk_indexing_clients": 8,
        "num_vectors": 1000000
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "refresh-index",
      "label": "msmarco-1m-refresh-after-ingest",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "force-merge",
      "label": "msmarco-1m-force-merge",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m",
        "target_index_max_num_segments": 1
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "refresh-index",
      "label": "msmarco-1m-refresh-after-merge",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "vector-search",
      "label": "msmarco-1m-search-k100",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m",
        "num_vectors": 1000000,
        "query_k": 100,
        "hnsw_ef_search": 256,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "vector-search",
      "label": "msmarco-1m-search-k10",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m",
        "num_vectors": 1000000,
        "query_k": 10,
        "hnsw_ef_search": 256,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "delete-index",
      "label": "msmarco-5m-delete-index",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "create-index",
      "label": "msmarco-5m-create-index",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "target_index_body": "indices/index.json",
        "target_field_name": "vector",
        "target_index_dimension": 1024,
        "target_index_space_type": "cosinesimil",
        "engine": "jvector",
        "method_name": "disk_ann",
        "mode": "on_disk",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "hnsw_ef_construction": 256,
        "hnsw_m": 32,
        "max_merged_segment": "25gb"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "bulk-ingest-data",
      "label": "msmarco-5m-bulk-ingest",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "target_index_bulk_index_data_set_path": "/datasets/msmarco/cohere_msmarco_base_5m.fvec",
        "target_index_bulk_size": 5000,
        "target_index_bulk_indexing_clients": 8,
        "num_vectors": 5000000
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "refresh-index",
      "label": "msmarco-5m-refresh-after-ingest",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "force-merge",
      "label": "msmarco-5m-force-merge",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "target_index_max_num_segments": 1
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "refresh-index",
      "label": "msmarco-5m-refresh-after-merge",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "vector-search",
      "label": "msmarco-5m-search-k100",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "num_vectors": 5000000,
        "query_k": 100,
        "hnsw_ef_search": 256,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "vector-search",
      "label": "msmarco-5m-search-k10",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "num_vectors": 5000000,
        "query_k": 10,
        "hnsw_ef_search": 256,
        "time_period": 300
      }
    }
  ]
}
EOF
)

echo "Payload:"
echo "$PAYLOAD" | jq '.'
echo ""

echo "Triggering complete batch benchmark..."
RESPONSE=$(curl -s -X POST "$API_URL/api/v1/benchmark/batch" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

echo "Response:"
echo "$RESPONSE" | jq '.'
echo ""

JOB_ID=$(echo "$RESPONSE" | jq -r '.job_id // empty')
if [ -z "$JOB_ID" ]; then
  echo "ERROR: Failed to get job_id from response"
  exit 1
fi

echo "Job ID: $JOB_ID"
echo ""

echo "Monitoring job status..."
echo "Press Ctrl+C to stop monitoring (job will continue running)"
echo ""

PREV_STATUS=""
PREV_SCENARIO=""
while true; do
  STATUS_RESPONSE=$(curl -s "$API_URL/api/v1/benchmark/$JOB_ID")
  if ! echo "$STATUS_RESPONSE" | jq '.' > /dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Waiting for job to appear (got: ${STATUS_RESPONSE:-(empty)})"
    sleep 10
    continue
  fi
  STATUS=$(echo "$STATUS_RESPONSE" | jq -r '.status // "unknown"')
  CURRENT_SCENARIO=$(echo "$STATUS_RESPONSE" | jq -r '.current_scenario // ""')

  if [ "$STATUS" != "$PREV_STATUS" ] || [ "$CURRENT_SCENARIO" != "$PREV_SCENARIO" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Status: $STATUS"
    if [ -n "$CURRENT_SCENARIO" ]; then
      echo "  Current scenario: $CURRENT_SCENARIO"
    fi
    COMPLETED=$(echo "$STATUS_RESPONSE" | jq -r '.scenarios_completed // 0')
    TOTAL=$(echo "$STATUS_RESPONSE" | jq -r '.scenarios_total // 0')
    if [ "$TOTAL" != "0" ] && [ "$TOTAL" != "null" ]; then
      echo "  Progress: $COMPLETED/$TOTAL scenarios completed"
    fi
    PREV_STATUS="$STATUS"
    PREV_SCENARIO="$CURRENT_SCENARIO"
  fi

  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "error" ] || [ "$STATUS" = "cancelled" ]; then
    echo ""
    echo "=========================================="
    echo "Job finished with status: $STATUS"
    echo "=========================================="
    echo ""

    echo "Final job details:"
    curl -s "$API_URL/api/v1/benchmark/$JOB_ID" | jq '{
      job_id, status, scenarios_completed, scenarios_total,
      scenario_status
    }'
    echo ""

    if [ "$STATUS" = "completed" ]; then
      echo "Fetching results..."
      RESULTS=$(curl -s "$API_URL/api/v1/benchmark/$JOB_ID/results")
      echo "Results Summary:"
      echo "$RESULTS" | jq '{
        job_id,
        total_sweeps: (.sweeps | length),
        scenarios: (.sweeps | group_by(.scenario_label) | map({
          scenario: .[0].scenario_label,
          dataset: .[0].dataset,
          sweep_count: length
        }))
      }'
      echo ""
      echo "$RESULTS" | jq '.' > "all-complete-results-$JOB_ID.json"
      echo "Full results saved to: all-complete-results-$JOB_ID.json"
    fi

    break
  fi

  sleep 10
done

echo ""
echo "=========================================="
echo "Test Complete!"
echo "=========================================="
echo "Job ID: $JOB_ID"
echo "View results at: $API_URL/results.html?job_id=$JOB_ID"
echo ""
echo "Expected Results (56 sweeps total):"
echo "  wiki-en-768 @ 1M:   1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14"
echo "  wiki-en-768 @ 5M:   1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14"
echo "  msmarco-1024 @ 1M:  1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14"
echo "  msmarco-1024 @ 5M:  1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14"

# Made with Bob
