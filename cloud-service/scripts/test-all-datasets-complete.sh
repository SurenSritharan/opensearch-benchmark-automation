#!/bin/bash
# Complete end-to-end batch test: both datasets (cohere-wiki-en-768 + cohere-msmarco-1024)
# at both 1M and 5M vector sizes.
# Each dataset/size runs: create-index, bulk-ingest, refresh, force-merge, search (k=100 + k=10)

set -e

API_URL="${API_URL:-http://34.132.114.18}"

echo "=========================================="
echo "Complete Batch Test - All Datasets (1M + 5M)"
echo "=========================================="
echo "API URL: $API_URL"
echo ""
echo "Datasets:"
echo "  • cohere-wiki-en-768  (768-dim, innerproduct) @ 1M and 5M"
echo "  • cohere-msmarco-1024 (1024-dim, cosinesimil) @ 1M and 5M"
echo ""
echo "Test Flow per dataset/size:"
echo "  1. Create index"
echo "  2. Bulk ingest vectors"
echo "  3. Refresh index"
echo "  4. Force-merge to 1 segment"
echo "  5. Search with k=100, ef=128 (5 client sweeps)"
echo "  6. Search with k=10,  ef=128 (5 client sweeps)"
echo ""

PAYLOAD=$(cat <<'EOF'
{
  "engine": "jvector",
  "tests": [

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
        "compression_level": "16x",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "hnsw_ef_construction": 128,
        "hnsw_m": 16,
        "max_merged_segment": "25gb"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "bulk-ingest-data",
      "label": "wiki-1m-bulk-ingest",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "target_index_bulk_size": 500,
        "target_index_bulk_indexing_clients": 8,
        "num_vectors": 1000000
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "refresh",
      "label": "wiki-1m-refresh",
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
      "scenario": "vector-search",
      "label": "wiki-1m-search-k100-ef128",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "num_vectors": 1000000,
        "query_k": 100
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "wiki-1m-search-k10-ef128",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "num_vectors": 1000000,
        "query_k": 10
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
        "compression_level": "16x",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "hnsw_ef_construction": 128,
        "hnsw_m": 16,
        "max_merged_segment": "25gb"
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "bulk-ingest-data",
      "label": "wiki-5m-bulk-ingest",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "target_index_bulk_size": 500,
        "target_index_bulk_indexing_clients": 8,
        "num_vectors": 5000000
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "refresh",
      "label": "wiki-5m-refresh",
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
        "compression_level": "16x",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "hnsw_ef_construction": 128,
        "hnsw_m": 16,
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
      "scenario": "refresh",
      "label": "msmarco-1m-refresh",
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
      "scenario": "vector-search",
      "label": "msmarco-1m-search-k100-ef128",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m",
        "num_vectors": 1000000,
        "query_k": 100,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "vector-search",
      "label": "msmarco-1m-search-k10-ef128",
      "params": {
        "target_index_name": "cohere-msmarco-1024-1m",
        "num_vectors": 1000000,
        "query_k": 10,
        "time_period": 300
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
        "compression_level": "16x",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "segments_per_tier": 5,
        "hnsw_ef_construction": 128,
        "hnsw_m": 16,
        "floor_segment": "1gb",
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
      "scenario": "refresh",
      "label": "msmarco-5m-refresh",
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
      "scenario": "vector-search",
      "label": "msmarco-5m-search-k100-ef128",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "num_vectors": 5000000,
        "query_k": 100,
        "time_period": 300
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "vector-search",
      "label": "msmarco-5m-search-k10-ef128",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "num_vectors": 5000000,
        "query_k": 10,
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
  STATUS_RESPONSE=$(curl -s "$API_URL/api/v1/benchmark/$JOB_ID/status")
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
    curl -s "$API_URL/api/v1/benchmark/$JOB_ID/status" | jq '{
      job_id,
      status,
      scenarios_completed,
      scenarios_total,
      result: {
        scenario_results: .result.scenario_results | to_entries | map({
          key: .key,
          status: .value.status,
          sweeps: (.value.sweep_results | length)
        })
      }
    }'
    echo ""

    if [ "$STATUS" = "completed" ]; then
      echo "Fetching results..."
      RESULTS=$(curl -s "$API_URL/api/v1/benchmark/$JOB_ID/results")

      echo ""
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
      RESULTS_FILE="batch-all-datasets-complete-results.json"
      echo "Full results saved to: $RESULTS_FILE"
      echo "$RESULTS" | jq '.' > "$RESULTS_FILE"

      echo ""
      echo "Sweep Details:"
      echo "$RESULTS" | jq '.sweeps[] | {
        scenario: .scenario_label,
        sweep: .sweep_name,
        status: .status,
        has_test_run: (.test_run != null),
        has_workload_params: (.workload_params != null)
      }'
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
echo "Expected Results (per dataset/size: 14 sweeps each, 4 combos = 56 total):"
echo "  cohere-wiki-en-768 @ 1M:  1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14 sweeps"
echo "  cohere-wiki-en-768 @ 5M:  1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14 sweeps"
echo "  cohere-msmarco-1024 @ 1M: 1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14 sweeps"
echo "  cohere-msmarco-1024 @ 5M: 1 create + 1 ingest + 1 refresh + 1 force-merge + 5 search-k100 + 5 search-k10 = 14 sweeps"
echo "  Total: 56 sweeps"

# Made with Bob
