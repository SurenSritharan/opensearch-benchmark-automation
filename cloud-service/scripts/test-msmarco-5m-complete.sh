#!/bin/bash
# Complete end-to-end batch test: create-index, bulk-ingest, refresh, force-merge, search with sweeps
# Dataset: cohere-msmarco-1024 (1024-dim, cosinesimil) @ 5M vectors

set -e

API_URL="${API_URL:-http://34.132.114.18}"

echo "=========================================="
echo "Complete Batch Test - 5M Vectors"
echo "=========================================="
echo "API URL: $API_URL"
echo ""
echo "Test Flow:"
echo "  1. Create index"
echo "  2. Bulk ingest 5M vectors"
echo "  3. Refresh index"
echo "  4. Force-merge to 1 segment"
echo "  5. Search with k=100, ef=100 (5 client sweeps)"
echo "  6. Search with k=10, ef=32 (5 client sweeps)"
echo ""

# Complete batch payload with all steps
PAYLOAD=$(cat <<'EOF'
{
  "engine": "jvector",
  "tests": [
    {
      "dataset": "cohere-msmarco-1024",
      "engine": "jvector",
      "scenario": "create-index",
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
        "hnsw_ef_construction": 128,
        "hnsw_m": 16,
        "max_merged_segment": "25gb"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "engine": "jvector",
      "scenario": "bulk-ingest-data",
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
      "label": "refresh-index",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m"
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "force-merge",
      "label": "force-merge-1seg",
      "params": {
        "target_index_name": "cohere-msmarco-1024-5m",
        "target_index_max_num_segments": 1
      }
    },
    {
      "dataset": "cohere-msmarco-1024",
      "scenario": "vector-search",
      "label": "vector-search-k100-ef128",
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
      "label": "vector-search-k10-ef128",
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

# Trigger the batch benchmark
echo "Triggering complete batch benchmark..."
RESPONSE=$(curl -s -X POST "$API_URL/api/v1/benchmark/batch" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

echo "Response:"
echo "$RESPONSE" | jq '.'
echo ""

# Extract job_id
JOB_ID=$(echo "$RESPONSE" | jq -r '.job_id // empty')

if [ -z "$JOB_ID" ]; then
  echo "ERROR: Failed to get job_id from response"
  exit 1
fi

echo "Job ID: $JOB_ID"
echo ""

# Monitor job status
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
    
    # Show progress details
    COMPLETED=$(echo "$STATUS_RESPONSE" | jq -r '.scenarios_completed // 0')
    TOTAL=$(echo "$STATUS_RESPONSE" | jq -r '.scenarios_total // 0')
    if [ "$TOTAL" != "0" ] && [ "$TOTAL" != "null" ]; then
      echo "  Progress: $COMPLETED/$TOTAL scenarios completed"
    fi
    
    PREV_STATUS="$STATUS"
    PREV_SCENARIO="$CURRENT_SCENARIO"
  fi
  
  # Check if job is complete
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "error" ] || [ "$STATUS" = "cancelled" ]; then
    echo ""
    echo "=========================================="
    echo "Job finished with status: $STATUS"
    echo "=========================================="
    echo ""
    
    # Get full job details
    echo "Final job details:"
    curl -s "$API_URL/api/v1/benchmark/$JOB_ID" | jq '{
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
    
    # Get results if completed
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
      echo "Full results saved to: batch-5m-complete-results.json"
      echo "$RESULTS" | jq '.' > batch-5m-complete-results.json
      
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
echo "Expected Results:"
echo "  - 1 sweep for create-index"
echo "  - 1 sweep for bulk-ingest-5m"
echo "  - 1 sweep for refresh-index"
echo "  - 1 sweep for force-merge-1seg"
echo "  - 5 sweeps for vector-search-k100-ef128 (clients: 4,8,10,12,16)"
echo "  - 5 sweeps for vector-search-k10-ef128  (clients: 4,8,10,12,16)"
echo "  Total: 14 sweeps"

# Made with Bob
