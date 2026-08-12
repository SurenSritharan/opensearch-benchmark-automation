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
STATS_INTERVAL=0   # 0 = disabled; >0 = poll _nodes/stats every N seconds
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
    --stats-interval)
      STATS_INTERVAL="${2:?--stats-interval requires a value in seconds}"
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
[ "$STATS_INTERVAL" -gt 0 ] 2>/dev/null && echo "Stats:       every ${STATS_INTERVAL}s → server-stats-timeseries.ndjson"
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

# ── Background server-stats sampler ───────────────────────────────────────────
# When --stats-interval N is set, polls _nodes/stats from the OpenSearch cluster
# at N-second intervals for the duration of the job and appends one NDJSON record
# per sample to <RESULTS_DEST>/server-stats-timeseries.ndjson.
#
# Each record is a flat JSON object:
#   { "ts": "<ISO-8601>", "node": "<name>", "heap_used_pct": N,
#     "cpu_pct": N, "load_1m": N, "gc_young_count": N, "gc_young_ms": N,
#     "gc_old_count": N, "gc_old_ms": N, "search_query_total": N,
#     "indexing_total": N, "fs_total_bytes": N, "fs_free_bytes": N }
#
# The file is ready for charting with any tool that understands NDJSON:
#   jq -s '.' server-stats-timeseries.ndjson        # → JSON array
#   jq -r '[.ts,.node,.heap_used_pct] | @csv' ...   # → CSV
#
# Uses the worker pod as a kubectl exec proxy (same pattern as telemetry
# collection) so no direct network path to the cluster is required.
_STATS_SAMPLER_PID=""

_start_stats_sampler() {
  [ "${STATS_INTERVAL:-0}" -gt 0 ] 2>/dev/null || return 0
  [ -z "${RESULTS_DEST:-}" ] && return 0

  local ns="os-develop-${ENGINE}"
  local os_host="opensearch-cluster.${ns}.svc.cluster.local:9200"
  local worker_pod="opensearch-benchmark-worker-${ENGINE}-0"
  local outfile="${RESULTS_DEST}/server-stats-timeseries.ndjson"

  echo "  [stats] Sampler started — interval=${STATS_INTERVAL}s → $(basename "$outfile")"

  (
    while true; do
      local ts
      ts=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

      # Fetch _nodes/stats via the worker pod; suppress errors so a transient
      # cluster hiccup does not kill the sampler subprocess.
      local raw
      raw=$(kubectl exec -n benchmark-api-develop "$worker_pod" -c worker -- \
        curl -sk -u admin:admin \
        "https://${os_host}/_nodes/stats/jvm,os,indices,thread_pool,fs" \
        2>/dev/null || true)

      # Skip this sample if the response is not valid JSON
      echo "$raw" | jq -e '.nodes' > /dev/null 2>&1 || { sleep "$STATS_INTERVAL"; continue; }

      # Emit one NDJSON record per node
      echo "$raw" | jq -c --arg ts "$ts" '
        .nodes | to_entries[] | .value as $n |
        {
          ts:                 $ts,
          node:               $n.name,
          heap_used_pct:      ($n.jvm.mem.heap_used_percent // null),
          heap_used_mb:       (($n.jvm.mem.heap_used_in_bytes // 0) / 1048576 | round),
          heap_max_mb:        (($n.jvm.mem.heap_max_in_bytes  // 0) / 1048576 | round),
          gc_young_count:     ($n.jvm.gc.collectors.young.collection_count       // null),
          gc_young_ms:        ($n.jvm.gc.collectors.young.collection_time_in_millis // null),
          gc_old_count:       ($n.jvm.gc.collectors.old.collection_count         // null),
          gc_old_ms:          ($n.jvm.gc.collectors.old.collection_time_in_millis  // null),
          cpu_pct:            ($n.os.cpu.percent // null),
          load_1m:            ($n.os.cpu.load_average."1m" // null),
          mem_used_pct:       ($n.os.mem.used_percent // null),
          search_query_total: ($n.indices.search.query_total // null),
          search_query_ms:    ($n.indices.search.query_time_in_millis // null),
          indexing_total:     ($n.indices.indexing.index_total // null),
          indexing_ms:        ($n.indices.indexing.index_time_in_millis // null),
          search_rejected:    ($n.thread_pool.search.rejected // null),
          write_rejected:     ($n.thread_pool.write.rejected  // null),
          fs_total_bytes:     ($n.fs.total.total_in_bytes // null),
          fs_free_bytes:      ($n.fs.total.free_in_bytes  // null)
        }
      ' >> "$outfile" 2>/dev/null || true

      sleep "$STATS_INTERVAL"
    done
  ) &
  _STATS_SAMPLER_PID=$!
}

_stop_stats_sampler() {
  [ -z "${_STATS_SAMPLER_PID:-}" ] && return 0
  kill "$_STATS_SAMPLER_PID" 2>/dev/null || true
  wait "$_STATS_SAMPLER_PID" 2>/dev/null || true
  _STATS_SAMPLER_PID=""
  [ -n "${RESULTS_DEST:-}" ] && \
    echo "  [stats] Sampler stopped — $(wc -l < "${RESULTS_DEST}/server-stats-timeseries.ndjson" 2>/dev/null || echo 0) samples written"
}

PREV_COMPLETED="-1"
PREV_STATUS=""
PREV_LABEL=""
PREV_FAILURES=""
# Tracks scenario keys already processed so we never re-collect on subsequent deltas.
declare -A _COLLECTED

# ── Incremental result copy helper ────────────────────────────────────────────
# Called whenever a scenario transitions to a terminal state.
# Copies only that scenario's subdirectory from the worker pod to the Jenkins
# workspace so results are preserved as each step finishes rather than all-at-
# once at the end (where a pod restart or scale-down could lose everything).
#
# Requires two env vars set by the Jenkinsfile before invoking run-pipeline.sh:
#   RESULTS_DEST   local destination directory  (e.g. results/12/3.7.0/small/test-runs/jvector)
#   WORKER_POD     pod name                      (e.g. opensearch-benchmark-worker-jvector-0)
_copy_scenario_results() {
  local scenario_key="$1"   # e.g. cohere-wiki-en-768-wiki-en-768-5m-bulk-ingest-data
  [ -z "${RESULTS_DEST:-}" ] || [ -z "${WORKER_POD:-}" ] && return 0

  local src_path="/results/${JOB_ID}/${ENGINE}/${scenario_key}"
  local dest="${RESULTS_DEST}/${scenario_key}"
  mkdir -p "$dest"
  kubectl cp -c worker "benchmark-api-develop/${WORKER_POD}:${src_path}/." "$dest/" >/dev/null 2>&1 || \
    echo "  WARNING: kubectl cp failed for ${scenario_key} (pod may not be reachable)"
}

# ── Per-scenario server log collector ─────────────────────────────────────────
# Called immediately after a scenario reaches a terminal state.
# Collects pod container logs, GC logs, heap dumps, and REST telemetry from the
# OpenSearch namespace and writes them into the scenario's local results dir.
#
# Uses the same env vars as _copy_scenario_results:
#   RESULTS_DEST   local destination directory
#   OS_NAMESPACE   OpenSearch namespace (derived from ENGINE when not set)
_collect_scenario_server_logs() {
  local scenario_key="$1"
  [ -z "${RESULTS_DEST:-}" ] && return 0

  local ns="${OS_NAMESPACE:-os-develop-${ENGINE}}"
  local log_dir="${RESULTS_DEST}/${scenario_key}/server-logs"
  mkdir -p "$log_dir"

  # ── Pod container logs ────────────────────────────────────────────────────
  local pods pod_count=0 gc_count=0 hprof_count=0
  pods=$(kubectl get pods -n "$ns" \
    -l 'app in (opensearch-data,opensearch-cluster-manager)' \
    --no-headers \
    -o custom-columns=':metadata.name' 2>/dev/null || true)

  for pod in $pods; do
    local containers
    containers=$(kubectl get pod "$pod" -n "$ns" \
      -o jsonpath='{.spec.containers[*].name}' 2>/dev/null || true)
    for container in $containers; do
      local logfile="${log_dir}/${pod}-${container}.log"
      kubectl logs "$pod" -c "$container" -n "$ns" --tail=5000 \
        > "$logfile" 2>&1 || true
      pod_count=$((pod_count + 1))
    done

    # GC log
    local gc_file="${log_dir}/${pod}-gc.log"
    kubectl exec "$pod" -c opensearch -n "$ns" -- \
      cat /usr/share/opensearch/logs/gc.log > "$gc_file" 2>/dev/null || true
    if [ -s "$gc_file" ]; then
      gc_count=$((gc_count + 1))
    else
      rm -f "$gc_file"
    fi

    # Heap dumps
    local hprof_files
    hprof_files=$(kubectl exec "$pod" -c opensearch -n "$ns" -- \
      sh -c 'ls /usr/share/opensearch/data/*.hprof 2>/dev/null || true' || true)
    for hprof in $hprof_files; do
      local hprof_name
      hprof_name=$(basename "$hprof")
      local local_hprof="${log_dir}/${pod}-${hprof_name}"
      kubectl cp "${ns}/${pod}:${hprof}" "$local_hprof" \
        -c opensearch 2>/dev/null || true
      [ -s "$local_hprof" ] && hprof_count=$((hprof_count + 1))
    done
  done

  # ── REST telemetry ────────────────────────────────────────────────────────
  local tel_dir="${log_dir}/telemetry"
  mkdir -p "$tel_dir"
  local os_host="opensearch-cluster.${ns}.svc.cluster.local:9200"

  while IFS= read -r entry; do
    local endpoint filename
    endpoint=$(echo "$entry" | awk '{print $1}')
    filename=$(echo "$entry" | awk '{print $2}')
    kubectl exec -n benchmark-api-develop \
      "opensearch-benchmark-worker-${ENGINE}-0" -c worker -- \
      curl -sk -u admin:admin "https://${os_host}${endpoint}" \
      > "${tel_dir}/${filename}" 2>/dev/null || true
  done << 'ENDPOINTS'
/_cluster/health?pretty cluster-health.json
/_cluster/stats?pretty cluster-stats.json
/_cluster/settings?include_defaults=true&flat_settings=true&pretty cluster-settings.json
/_nodes/stats?pretty nodes-stats.json
/_cat/nodes?v&h=name,heap.percent,heap.current,heap.max,ram.percent,cpu,load_1m,load_5m nodes.txt
/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,largest,completed thread-pools.txt
/_cat/tasks?v&detailed tasks.txt
/_cat/segments?v segments.txt
ENDPOINTS

  local hprof_note=""
  [ "$hprof_count" -gt 0 ] && hprof_note=" | ${hprof_count} heap dump(s)"
  echo "  [server-logs] ${scenario_key}: ${pod_count} pod log(s), ${gc_count} gc.log(s)${hprof_note}"
}

_start_stats_sampler

while true; do
  now="[$(date '+%Y-%m-%d %H:%M:%S')]"
  resp=$(curl -s --max-time 30 "${API_URL}/api/v1/benchmark/${JOB_ID}?engine=${ENGINE}" || true)
  if ! echo "$resp" | jq '.' > /dev/null 2>&1; then
    echo "$now  (waiting for job to appear or API busy — retrying...)"
    sleep 6
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
    PREV_STATUS="$job_status"
    PREV_LABEL="$label"
  fi

  # Copy results and collect server logs for each newly-completed scenario.
  # We compare against PREV_COMPLETED so we only act on the delta.
  if [ "${completed:-0}" -gt "$PREV_COMPLETED" ] && [ -n "${RESULTS_DEST:-}" ]; then
    while IFS= read -r skey; do
      [ -z "$skey" ] && continue
      # Skip scenarios already processed in a previous iteration.
      [ "${_COLLECTED[$skey]+set}" = "set" ] && continue
      _COLLECTED[$skey]=1
      _copy_scenario_results "$skey"
      _collect_scenario_server_logs "$skey"
    done < <(echo "$resp" | jq -r '
      .scenario_status // {} | to_entries[] |
      select(.value | test("completed|failed|partial_failure|error|cancelled")) |
      .key
    ')
  fi
  PREV_COMPLETED="${completed:-0}"

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

_stop_stats_sampler

echo ""
echo "=========================================="
echo "Done!"
echo "=========================================="
echo "Job ID: $JOB_ID"
echo "View results at: ${API_URL}/results/${JOB_ID}?job_id=${JOB_ID}&engine=${ENGINE}"
echo ""

# Made with Bob
