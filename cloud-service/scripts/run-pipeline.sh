#!/bin/bash
# OpenSearch Benchmark pipeline runner — submits a batch job to the cloud service API.
#
# Usage:
#   run-pipeline.sh --pipeline <name> <engine>
#
# Arguments:
#   --pipeline  Name of a pipeline under the pipelines/ directory (without .json extension).
#               E.g. "complete-1m" resolves to pipelines/complete-1m.json.
#               The pipeline lists scenario steps in order; __ENGINE__ is replaced with
#               the engine argument before parsing.
#   engine      One of: jvector, faiss, lucene
#
# Pipeline-level params (under top-level "params" key) are merged into every step.
# Step-level params override pipeline-level params, which override scenario defaults.
#
# Corpus-size derived values computed automatically from "corpus_size" in params:
#   target_index_name  →  <dataset>-<corpus_size>    (e.g. cohere-msmarco-1024-1m)
#   num_vectors        →  numeric value (1m=1000000, 5m=5000000, etc.)
#   label              →  <dataset-short>-<corpus_size>-<scenario>[-k<N>]
#
# Cluster credentials (optional, default to admin/admin):
#   Set "username" and "password" under the top-level "params" key in the pipeline JSON.
#   They are passed through to all API calls and the opensearch-benchmark --client-options flag.
#   Example in pipeline JSON:
#     "params": { "username": "myuser", "password": "mypass", "corpus_size": "1m" }
#
# Environment variables:
#   API_URL   Base URL of the benchmark cloud service (default: http://34.132.114.18)
#
# Examples:
#   ./cloud-service/scripts/run-pipeline.sh --pipeline complete-1m jvector
#   API_URL=http://1.2.3.4 ./cloud-service/scripts/run-pipeline.sh --pipeline msmarco-jvector-hq-build jvector

set -euo pipefail

# ── Parse arguments ────────────────────────────────────────────────────────────
PIPELINE_FILE=""
CLI_LOG_LEVEL=""
FIRST_RUN=false
ENABLE_PROFILING=false
PROFILING_DURATION=60
while true; do
  case "${1:-}" in
    --pipeline)
      PIPELINE_FILE="pipelines/${2:?--pipeline requires a name argument}.json"
      shift 2
      ;;
    --log-level)
      CLI_LOG_LEVEL="${2:?--log-level requires a value (debug|info|warning|error)}"
      shift 2
      ;;
    --first-run)
      FIRST_RUN=true
      shift
      ;;
    --enable-profiling)
      ENABLE_PROFILING=true
      shift
      ;;
    --profiling-duration)
      PROFILING_DURATION="${2:?--profiling-duration requires a value in seconds}"
      shift 2
      ;;
    *)
      break
      ;;
  esac
done

ENGINE="${1:?Usage: $0 --pipeline <name> [--log-level <level>] <engine>}"
API_URL="${API_URL:-http://34.132.114.18}"

# ── Validate ───────────────────────────────────────────────────────────────────
case "$ENGINE" in
  jvector|faiss|lucene) ;;
  *) echo "ERROR: engine must be jvector, faiss, or lucene (got: $ENGINE)"; exit 1 ;;
esac

if [ -z "$PIPELINE_FILE" ]; then
  echo "ERROR: --pipeline is required"
  exit 1
fi

if [ ! -f "$PIPELINE_FILE" ]; then
  echo "ERROR: pipeline not found: $PIPELINE_FILE"
  exit 1
fi

# ── Load pipeline ──────────────────────────────────────────────────────────────
PIPELINE_JSON=$(sed "s/__ENGINE__/${ENGINE}/g" "$PIPELINE_FILE")

DESCRIPTION=$(echo "$PIPELINE_JSON" | jq -r '.description // ""')
# --enable-profiling flag forces profiling on, overriding the pipeline JSON value.
PROFILING_ENABLED=$([ "$ENABLE_PROFILING" = "true" ] && echo "true" || \
  echo "$PIPELINE_JSON" | jq -r '.enable_profiling // false')
NO_METRICS=$(echo "$PIPELINE_JSON"  | jq -r '.no_metrics // false')
# CLI --log-level overrides the pipeline JSON value
LOG_LEVEL="${CLI_LOG_LEVEL:-$(echo "$PIPELINE_JSON" | jq -r '.log_level // ""')}"

# Top-level pipeline params merged into every step (later keys win)
PIPELINE_PARAMS=$(echo "$PIPELINE_JSON" | jq '.params // {}')

# Cluster credentials — read from pipeline params (fall back to empty string = server default)
USERNAME=$(echo "$PIPELINE_PARAMS" | jq -r '.username // ""')
PASSWORD=$(echo "$PIPELINE_PARAMS" | jq -r '.password // ""')

# When --first-run is set and the pipeline defines first_run_steps, prepend them
# to steps so the first run does: build (first_run_steps) + search (steps).
# Without --first-run, or when first_run_steps is absent, only steps runs.
if [ "$FIRST_RUN" = true ] && echo "$PIPELINE_JSON" | jq -e '.first_run_steps | length > 0' > /dev/null 2>&1; then
  EFFECTIVE_STEPS=$(echo "$PIPELINE_JSON" | jq '.first_run_steps + .steps')
else
  EFFECTIVE_STEPS=$(echo "$PIPELINE_JSON" | jq '.steps')
fi

STEP_COUNT=$(echo "$EFFECTIVE_STEPS" | jq 'length')
if [ "$STEP_COUNT" -eq 0 ]; then
  echo "ERROR: pipeline contains no steps: $PIPELINE_FILE"
  exit 1
fi

# ── Helper: derive num_vectors from corpus_size string ────────────────────────
num_vectors_for() {
  echo "$1" | awk '
    /^[0-9]+m$/ { n=substr($0,1,length($0)-1)+0; print n*1000000; exit }
    /^[0-9]+k$/ { n=substr($0,1,length($0)-1)+0; print n*1000;    exit }
    { print "ERROR: unrecognised corpus_size: " $0 > "/dev/stderr"; exit 1 }
  '
}

# ── Assemble tests[] from pipeline steps ──────────────────────────────────────
# Each step: { "dataset", "scenario", "params" (optional) }
# Merge order (later wins): pipeline params → step params
# Auto-injected from corpus_size (unless already set):
#   target_index_name, num_vectors (ingest/search only), label

TESTS_JSON="[]"

# Associative array to track how many times each base label has been used.
# When a label appears more than once a numeric suffix (-2, -3, …) is appended
# so every entry in tests[] has a unique label — duplicate labels would collide
# in the scenario_status dict on the server and cause results to be overwritten.
declare -A _LABEL_SEEN

while IFS= read -r raw_step; do
  dataset=$(echo  "$raw_step" | jq -r '.dataset')
  procedure=$(echo "$raw_step" | jq -r '.scenario')
  step_params=$(echo "$raw_step" | jq '.params // {}')
  # Per-step profile flag — null means "inherit job-level enable_profiling"
  step_profile=$(echo "$raw_step" | jq '.profile // null')

  # Merge: pipeline-level params first, then step-level params override
  merged_params=$(jq -n \
    --argjson pipeline "$PIPELINE_PARAMS" \
    --argjson step     "$step_params" \
    '$pipeline + $step')

  corpus_size=$(echo "$merged_params" | jq -r '.corpus_size // ""')

  if [ -n "$corpus_size" ]; then
    # Auto-inject target_index_name if not set
    if [ "$(echo "$merged_params" | jq 'has("target_index_name")')" = "false" ]; then
      merged_params=$(echo "$merged_params" | jq --arg v "${dataset}-${corpus_size}" '.target_index_name = $v')
    fi
    # Auto-inject num_vectors for ingest and search steps
    if [ "$(echo "$merged_params" | jq 'has("num_vectors")')" = "false" ]; then
      case "$procedure" in
        bulk-ingest-data|vector-search)
          merged_params=$(echo "$merged_params" | jq --argjson v "$(num_vectors_for "$corpus_size")" '.num_vectors = $v')
          ;;
      esac
    fi
  fi

  # Build label from dataset (strip "cohere-"), corpus_size, and procedure
  dataset_short="${dataset#cohere-}"
  label="${dataset_short}-${corpus_size}-${procedure}"
  # Append query_k to search labels so k=10 and k=100 are distinct
  query_k=$(echo "$merged_params" | jq -r '.query_k // ""')
  [ -n "$query_k" ] && label="${label}-k${query_k}"

  # Deduplicate: if this label has appeared before, append an occurrence counter.
  # First occurrence keeps the plain label; second becomes label-2, third label-3, etc.
  _LABEL_SEEN["$label"]=$(( ${_LABEL_SEEN["$label"]:-0} + 1 ))
  if [ "${_LABEL_SEEN["$label"]}" -gt 1 ]; then
    label="${label}-${_LABEL_SEEN["$label"]}"
  fi

  test_entry=$(jq -n \
    --arg     dataset      "$dataset" \
    --arg     scenario     "$procedure" \
    --arg     label        "$label" \
    --argjson params       "$merged_params" \
    --argjson step_profile "$step_profile" \
    '{ dataset: $dataset, scenario: $scenario, label: $label, params: $params }
     | if $step_profile != null then .profile = $step_profile else . end')

  TESTS_JSON=$(echo "$TESTS_JSON" | jq --argjson t "$test_entry" '. + [$t]')

done < <(echo "$EFFECTIVE_STEPS" | jq -c '.[]')

unset _LABEL_SEEN

# ── Build final payload ────────────────────────────────────────────────────────
PAYLOAD=$(jq -n \
  --arg     engine              "$ENGINE" \
  --argjson enable_profiling    "$PROFILING_ENABLED" \
  --argjson profiling_duration  "$PROFILING_DURATION" \
  --argjson no_metrics          "$NO_METRICS" \
  --arg     log_level           "$LOG_LEVEL" \
  --arg     username            "$USERNAME" \
  --arg     password            "$PASSWORD" \
  --argjson tests               "$TESTS_JSON" \
  '{
    engine: $engine,
    enable_profiling: $enable_profiling,
    profiling_duration: $profiling_duration,
    no_metrics: $no_metrics,
    log_level: $log_level,
    username: $username,
    password: $password,
    tests: $tests
  } | if .no_metrics == false then del(.no_metrics)   else . end
    | if .log_level  == ""    then del(.log_level)    else . end
    | if .username   == ""    then del(.username)     else . end
    | if .password   == ""    then del(.password)     else . end')

# ── Print header ───────────────────────────────────────────────────────────────
echo "=========================================="
echo "OpenSearch Benchmark Pipeline"
echo "=========================================="
echo "Pipeline:    $(basename "$PIPELINE_FILE" .json)"
[ -n "$DESCRIPTION" ] && echo "Description: $DESCRIPTION"
echo "Engine:      $ENGINE"
echo "Scenarios:   $STEP_COUNT step(s)"
echo "API URL:     $API_URL"
echo ""
echo "Steps (in order):"
echo "$EFFECTIVE_STEPS" | jq -r '.[] | .dataset + " / " + .scenario + (if .params then " (params: \(.params | keys | join(", ")))" else "" end)' \
  | nl -ba -w3 -v1 | sed 's/^/  /'
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
echo "Monitoring job status..."
echo ""

PREV_COMPLETED="-1"
PREV_STATUS=""
PREV_LABEL=""
PREV_FAILURES=""

while true; do
  now="[$(date '+%Y-%m-%d %H:%M:%S')]"
  resp=$(curl -s "${API_URL}/api/v1/benchmark/${JOB_ID}?engine=${ENGINE}")
  if ! echo "$resp" | jq '.' > /dev/null 2>&1; then
    echo "$now Waiting for job to appear..."
    sleep 4
    continue
  fi

  summary=$(echo "$resp" | jq -r '
    (.current_scenario // "") as $cur |
    (.scenarios // []) as $scens |
    ( if $cur == "" then ""
      else ( $scens[] | select( (.dataset + "-" + .label) == $cur ) | .label ) // $cur
      end ) as $label |
    ([ .scenario_status // {} | to_entries[] | select(.value | test("completed|failed|partial_failure|error|cancelled")) ] | length) as $done |
    ([ .scenarios // [] | .[] ] | length) as $total |
    ( if $label != "" then ($done + 1) else $done end ) as $display |
    [ (.status // "unknown"), ($done|tostring), ($total|tostring), $label, ($display|tostring) ] | join("|")
  ')

  IFS='|' read -r job_status completed total label display <<< "$summary"

  # Suppress transient "unknown 0/0" that appears when the worker pod is
  # restarting and the proxy temporarily returns an empty/error response.
  if [ "$job_status" = "unknown" ] && [ "$total" = "0" ]; then
    sleep 4
    continue
  fi

  terminal=false
  case "$job_status" in completed|failed|error|partial|cancelled) terminal=true ;; esac

  # Print any newly failed scenarios — runs every poll, not just on label change,
  # so failures from scenarios that complete between polls are never silently dropped.
  FAILURES=$(echo "$resp" | jq -r '
    .scenario_status // {} | to_entries[] |
    select(.value == "failed" or .value == "error") |
    "  ✗  \(.key)  [\(.value)]"
  ')
  if [ "$FAILURES" != "$PREV_FAILURES" ]; then
    while IFS= read -r line; do
      case "$PREV_FAILURES" in
        *"$line"*) ;;
        *) echo "$line" ;;
      esac
    done <<< "$FAILURES"
    PREV_FAILURES="$FAILURES"
  fi

  if [ "$label" != "$PREV_LABEL" ] || [ "$job_status" != "$PREV_STATUS" ]; then
    if [ -n "$label" ]; then
      printf "%s  %-9s  %2d/%d  (running: %s)\n" "$now" "$job_status" "$display" "$total" "$label"
    else
      printf "%s  %-9s  %2d/%d\n" "$now" "$job_status" "$completed" "$total"
    fi
    PREV_COMPLETED="$completed"
    PREV_STATUS="$job_status"
    PREV_LABEL="$label"
  fi

  if $terminal; then
    echo ""
    echo "=========================================="
    echo "Job finished with status: $job_status"
    echo "=========================================="
    echo ""

    # Always print scenario-level outcomes so skipped/failed tests are visible
    echo "Scenario status:"
    echo "$resp" | jq -r '
      .scenario_status // {} | to_entries[] |
      "  \(if .value == "completed" then "✓" elif .value == "failed" or .value == "error" then "✗" else "?" end)  \(.key)  [\(.value)]"
    '
    echo ""

    if [ "$job_status" = "completed" ] || [ "$job_status" = "partial" ]; then
      RESULTS=$(curl -s "${API_URL}/api/v1/benchmark/${JOB_ID}/results?engine=${ENGINE}")
      OUTFILE="${ENGINE}-$(basename "$PIPELINE_FILE" .json)-results.json"
      echo "$RESULTS" | jq '.' > "$OUTFILE"
      echo "Full results saved to: $OUTFILE"
    fi

    if [ "$job_status" = "failed" ] || [ "$job_status" = "error" ] || [ "$job_status" = "cancelled" ]; then
      echo "ERROR: job did not complete successfully (status: $job_status)" >&2
      exit 1
    fi

    break
  fi

  sleep 2
done

echo ""
echo "=========================================="
echo "Done!"
echo "=========================================="
echo "Job ID: $JOB_ID"
echo "View results at: ${API_URL}/results/${JOB_ID}?job_id=${JOB_ID}&engine=${ENGINE}"
echo ""

# Made with Bob
