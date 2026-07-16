#!/bin/bash
# Generic pipeline runner — builds and submits a batch job to the cloud service API.
#
# Usage:
#   run-pipeline.sh <pipeline> <engine> [<corpus_size>]
#
# Arguments:
#   pipeline     One of: complete, search-only
#                  complete   — create-index → bulk-ingest → refresh → force-merge → search k=100 → search k=10
#                  search-only — vector-search k=100 and k=10 only (indexes must exist)
#   engine       One of: jvector, faiss, lucene
#   corpus_size  One of: 1m, 5m (default: 1m)
#                Pass "all" to run 1m and 5m sequentially in one job.
#
# Environment variables:
#   API_URL      Base URL of the benchmark cloud service (default: http://34.132.114.18)
#   DATASET      Dataset name: cohere-wiki-en-768 or cohere-msmarco-1024
#                If not set, both datasets are run sequentially.
#   TIME_PERIOD  Search time period in seconds per sweep (default: 300)
#
# Examples:
#   ENGINE=jvector DATASET=cohere-wiki-en-768 ./run-pipeline.sh complete jvector 1m
#   API_URL=http://1.2.3.4 ./run-pipeline.sh search-only faiss 5m
#   ./run-pipeline.sh complete jvector all   # runs 1m then 5m in one job

set -euo pipefail

# ── Arguments ─────────────────────────────────────────────────────────────────
PIPELINE="${1:-complete}"
ENGINE="${2:?Usage: $0 <pipeline> <engine> [<corpus_size>]}"
CORPUS_ARG="${3:-1m}"

API_URL="${API_URL:-http://34.132.114.18}"
TIME_PERIOD="${TIME_PERIOD:-300}"

# ── Validate ───────────────────────────────────────────────────────────────────
case "$PIPELINE" in
  complete|search-only) ;;
  *) echo "ERROR: pipeline must be 'complete' or 'search-only' (got: $PIPELINE)"; exit 1 ;;
esac

case "$ENGINE" in
  jvector|faiss|lucene) ;;
  *) echo "ERROR: engine must be jvector, faiss, or lucene (got: $ENGINE)"; exit 1 ;;
esac

# Resolve corpus sizes list
if [ "$CORPUS_ARG" = "all" ]; then
  CORPUS_SIZES="1m 5m"
else
  CORPUS_SIZES="$CORPUS_ARG"
fi

# Resolve datasets list — treat unset, empty, or "all" as "run both"
if [ -z "${DATASET:-}" ] || [ "${DATASET:-}" = "all" ]; then
  DATASETS="cohere-wiki-en-768 cohere-msmarco-1024"
else
  DATASETS="$DATASET"
fi

# ── Engine-specific index config helpers ───────────────────────────────────────
# Returns extra JSON fields (no leading/trailing comma) for create-index params
create_index_engine_params() {
  local engine="$1"
  local dataset="$2"
  case "$engine" in
    jvector)
      echo '"method_name": "disk_ann", "mode": "on_disk"'
      ;;
    faiss|lucene)
      echo '"method_name": "hnsw"'
      ;;
  esac
}

# Returns dataset-specific index config (dimension, space_type, field_name)
dataset_index_params() {
  local dataset="$1"
  case "$dataset" in
    cohere-wiki-en-768)
      echo '"target_index_dimension": 768, "target_index_space_type": "innerproduct", "target_field_name": "target_field"'
      ;;
    cohere-msmarco-1024)
      echo '"target_index_dimension": 1024, "target_index_space_type": "cosinesimil", "target_field_name": "vector"'
      ;;
  esac
}

# Returns dataset-specific bulk ingest extras (data path for msmarco)
dataset_ingest_params() {
  local dataset="$1"
  local corpus_size="$2"
  case "$dataset" in
    cohere-msmarco-1024)
      echo ', "target_index_bulk_index_data_set_path": "/datasets/msmarco/cohere_msmarco_base_'"${corpus_size}"'.fvec"'
      ;;
    *)
      echo ''
      ;;
  esac
}

# Returns search extras (ef_search for msmarco)
dataset_search_params() {
  local dataset="$1"
  case "$dataset" in
    cohere-msmarco-1024)
      echo ', "hnsw_ef_search": 256'
      ;;
    *)
      echo ''
      ;;
  esac
}

# Returns num_vectors integer for a corpus size string
num_vectors_for() {
  case "$1" in
    1m)  echo 1000000 ;;
    5m)  echo 5000000 ;;
    10m) echo 10000000 ;;
    *)   echo "ERROR: unsupported corpus_size '$1'"; exit 1 ;;
  esac
}

# ── Build tests array ──────────────────────────────────────────────────────────
build_tests() {
  local dataset="$1"
  local engine="$2"
  local corpus_size="$3"
  local pipeline="$4"

  local index_name="${dataset}-${corpus_size}"
  local num_vectors
  num_vectors=$(num_vectors_for "$corpus_size")
  local label_prefix="${dataset##cohere-}-${corpus_size}"   # e.g. wiki-en-768-1m

  local ds_idx_params
  ds_idx_params=$(dataset_index_params "$dataset")
  local eng_idx_params
  eng_idx_params=$(create_index_engine_params "$engine" "$dataset")
  local ingest_extras
  ingest_extras=$(dataset_ingest_params "$dataset" "$corpus_size")
  local search_extras
  search_extras=$(dataset_search_params "$dataset")

  local tests=""

  if [ "$pipeline" = "complete" ]; then
    tests=$(cat <<ENDTESTS
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "create-index",
      "label": "${label_prefix}-create-index",
      "params": {
        "target_index_name": "${index_name}",
        "target_index_body": "indices/index.json",
        "target_index_primary_shards": 3,
        "target_index_replica_shards": 0,
        "refresh_interval": "-1",
        "translog_flush_threshold_size": "15gb",
        "max_merged_segment": "25gb",
        "hnsw_ef_construction": 256,
        "hnsw_m": 32,
        ${ds_idx_params},
        ${eng_idx_params}
      }
    },
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "bulk-ingest-data",
      "label": "${label_prefix}-bulk-ingest",
      "params": {
        "target_index_name": "${index_name}",
        "target_index_bulk_size": 5000,
        "target_index_bulk_indexing_clients": 8,
        "num_vectors": ${num_vectors}${ingest_extras}
      }
    },
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "refresh-index",
      "label": "${label_prefix}-refresh-after-ingest",
      "params": { "target_index_name": "${index_name}" }
    },
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "force-merge",
      "label": "${label_prefix}-force-merge",
      "params": {
        "target_index_name": "${index_name}",
        "target_index_max_num_segments": 1
      }
    },
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "refresh-index",
      "label": "${label_prefix}-refresh-after-merge",
      "params": { "target_index_name": "${index_name}" }
    },
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "vector-search",
      "label": "${label_prefix}-search-k100",
      "params": {
        "target_index_name": "${index_name}",
        "num_vectors": ${num_vectors},
        "query_k": 100,
        "time_period": ${TIME_PERIOD}${search_extras}
      }
    },
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "vector-search",
      "label": "${label_prefix}-search-k10",
      "params": {
        "target_index_name": "${index_name}",
        "num_vectors": ${num_vectors},
        "query_k": 10,
        "time_period": ${TIME_PERIOD}${search_extras}
      }
    }
ENDTESTS
)
  else
    # search-only
    tests=$(cat <<ENDTESTS
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "vector-search",
      "label": "${label_prefix}-search-k100",
      "params": {
        "target_index_name": "${index_name}",
        "num_vectors": ${num_vectors},
        "query_k": 100,
        "time_period": ${TIME_PERIOD}${search_extras}
      }
    },
    {
      "dataset": "${dataset}",
      "engine": "${engine}",
      "scenario": "vector-search",
      "label": "${label_prefix}-search-k10",
      "params": {
        "target_index_name": "${index_name}",
        "num_vectors": ${num_vectors},
        "query_k": 10,
        "time_period": ${TIME_PERIOD}${search_extras}
      }
    }
ENDTESTS
)
  fi

  echo "$tests"
}

# ── Assemble full payload ──────────────────────────────────────────────────────
all_tests=""
first=true
for dataset in $DATASETS; do
  for corpus_size in $CORPUS_SIZES; do
    tests=$(build_tests "$dataset" "$ENGINE" "$corpus_size" "$PIPELINE")
    if [ "$first" = "true" ]; then
      all_tests="$tests"
      first=false
    else
      all_tests="${all_tests},
${tests}"
    fi
  done
done

PAYLOAD=$(cat <<EOF
{
  "engine": "${ENGINE}",
  "tests": [
${all_tests}
  ]
}
EOF
)

# ── Print header ───────────────────────────────────────────────────────────────
echo "=========================================="
echo "OpenSearch Benchmark Pipeline"
echo "=========================================="
echo "Pipeline:    $PIPELINE"
echo "Engine:      $ENGINE"
echo "Dataset(s):  $DATASETS"
echo "Corpus:      $CORPUS_SIZES"
echo "API URL:     $API_URL"
echo "Time period: ${TIME_PERIOD}s per search sweep"
echo ""
echo "Payload:"
echo "$PAYLOAD" | jq '.'
echo ""

# ── Submit job ─────────────────────────────────────────────────────────────────
echo "Submitting batch job..."
RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/benchmark/batch" \
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

# ── Poll for completion ────────────────────────────────────────────────────────
# Prints a line only when a scenario completes — avoids flicker from
# current_scenario mid-transition.
echo "Monitoring job status..."
echo ""

PREV_COMPLETED="-1"
PREV_STATUS=""

while true; do
  now="[$(date '+%Y-%m-%d %H:%M:%S')]"
  resp=$(curl -s "${API_URL}/api/v1/benchmark/${JOB_ID}?engine=${ENGINE}")
  if ! echo "$resp" | jq '.' > /dev/null 2>&1; then
    echo "$now Waiting for job to appear..."
    sleep 10
    continue
  fi

  # scenario_status and current_scenario are unpacked at the top level by the API.
  # Resolve current_scenario key → label via the scenarios list.
  summary=$(echo "$resp" | jq -r '
    (.current_scenario // "") as $cur |
    (.scenarios // []) as $scens |
    ( if $cur == "" then ""
      else ( $scens[] | select( (.dataset + "-" + .label) == $cur ) | .label ) // $cur
      end ) as $label |
    ([ .scenario_status // {} | to_entries[] | select(.value == "completed") ] | length) as $done |
    ([ .scenario_status // {} | keys[] ] | length) as $total |
    # display index: completed+1 while a scenario is actively running, else completed
    ( if $label != "" then ($done + 1) else $done end ) as $display |
    [ (.status // "unknown"), ($done|tostring), ($total|tostring), $label, ($display|tostring) ] | join("|")
  ')

  IFS='|' read -r job_status completed total label display <<< "$summary"

  terminal=false
  case "$job_status" in completed|failed|error|partial|cancelled) terminal=true ;; esac

  if [ "$completed" != "$PREV_COMPLETED" ] || [ "$job_status" != "$PREV_STATUS" ]; then
    if [ -n "$label" ]; then
      printf "%s  %-9s  %2d/%d  (running: %s)\n" "$now" "$job_status" "$display" "$total" "$label"
    else
      printf "%s  %-9s  %2d/%d\n" "$now" "$job_status" "$completed" "$total"
    fi
    PREV_COMPLETED="$completed"
    PREV_STATUS="$job_status"
  fi

  if $terminal; then
    echo ""
    echo "=========================================="
    echo "Job finished with status: $job_status"
    echo "=========================================="
    echo ""

    if [ "$job_status" = "completed" ]; then
      RESULTS=$(curl -s "${API_URL}/api/v1/benchmark/${JOB_ID}/results?engine=${ENGINE}")
      echo "Results Summary:"
      echo "$RESULTS" | jq '{
        job_id,
        total_sweeps: (.sweeps | length),
        by_scenario: (.sweeps | group_by(.scenario_label) | map({
          scenario: .[0].scenario_label,
          sweeps:   length
        }))
      }'
      echo ""
      OUTFILE="${ENGINE}-${PIPELINE}-results.json"
      echo "$RESULTS" | jq '.' > "$OUTFILE"
      echo "Full results saved to: $OUTFILE"
    fi

    break
  fi

  sleep 10
done

echo ""
echo "=========================================="
echo "Done!"
echo "=========================================="
echo "Job ID: $JOB_ID"
echo "View results at: ${API_URL}/results/${JOB_ID}?job_id=${JOB_ID}&engine=${ENGINE}"
echo ""

# Made with Bob
