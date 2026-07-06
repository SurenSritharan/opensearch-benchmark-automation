#!/bin/bash
# Search-only batch test: runs vector-search scenarios against pre-built indexes
# Dataset: cohere-wiki-en-768 (768-dim, innerproduct) @ 1M and 5M vectors
# Requires indexes cohere-wiki-en-768-1m and cohere-wiki-en-768-5m to already exist

set -e

API_URL="${API_URL:-http://34.132.114.18}"

echo "=========================================="
echo "Search-Only Batch Test - Wiki (1M + 5M Vectors)"
echo "=========================================="
echo "API URL: $API_URL"
echo ""
echo "Test Flow (indexes must already exist):"
echo "  1. Search cohere-wiki-en-768-5m with k=100 (5 client sweeps)"
echo "  2. Search cohere-wiki-en-768-5m with k=10  (5 client sweeps)"
echo "  3. Search cohere-wiki-en-768-1m with k=100 (5 client sweeps)"
echo "  4. Search cohere-wiki-en-768-1m with k=10  (5 client sweeps)"
echo ""

# Complete batch payload with all steps
PAYLOAD=$(cat <<'EOF'
{
  "engine": "jvector",
  "tests": [
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "vector-search-k100-5m",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "num_vectors": 5000000,
        "query_k": 100,
        "time_period": 1800
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "vector-search-k10-5m",
      "params": {
        "target_index_name": "cohere-wiki-en-768-5m",
        "num_vectors": 5000000,
        "query_k": 10,
        "time_period": 1800
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "vector-search-k100-1m",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "num_vectors": 1000000,
        "query_k": 100,
        "time_period": 1800
      }
    },
    {
      "dataset": "cohere-wiki-en-768",
      "scenario": "vector-search",
      "label": "vector-search-k10-1m",
      "params": {
        "target_index_name": "cohere-wiki-en-768-1m",
        "num_vectors": 1000000,
        "query_k": 10,
        "time_period": 1800
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
echo "  - 5 sweeps for vector-search-k100-5m (clients: 4,8,10,12,16)"
echo "  - 5 sweeps for vector-search-k10-5m  (clients: 4,8,10,12,16)"
echo "  - 5 sweeps for vector-search-k100-1m (clients: 4,8,10,12,16)"
echo "  - 5 sweeps for vector-search-k10-1m  (clients: 4,8,10,12,16)"
echo "  Total: 20 sweeps"

# Made with Bob
