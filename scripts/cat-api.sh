#!/bin/bash

# Script to perform CAT API calls against an OpenSearch cluster in a given namespace
# Defaults to os-jvector namespace; supports interactive or direct command selection
#
# Special command: ingest_progress
#   Polls _nodes/stats/indices/indexing — reflects docs written to translog,
#   NOT dependent on refresh. Use this to watch live ingest progress.
#   Add --watch [interval_seconds] to poll continuously (default interval: 5s).
#
# Usage:
#   ./cat-api.sh [namespace] [cat-command|ingest_progress] [--watch [seconds]]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ──────────────────────────────────────────────
# CAT API definitions: (name, endpoint, description)
# ──────────────────────────────────────────────
CAT_NAMES=(
  "indices"
  "shards"
  "nodes"
  "health"
  "aliases"
  "allocation"
  "segments"
  "pending_tasks"
  "recovery"
  "plugins"
  "templates"
  "thread_pool"
  "fielddata"
)

CAT_ENDPOINTS=(
  "_cat/indices?v&h=index,health,status,pri,rep,docs.count,store.size,pri.store.size"
  "_cat/shards?v&h=index,shard,prirep,state,docs,store,node"
  "_cat/nodes?v&h=name,ip,heap.percent,ram.percent,cpu,load_1m,node.role,master"
  "_cat/health?v"
  "_cat/aliases?v"
  "_cat/allocation?v"
  "_cat/segments?v&h=index,shard,prirep,segment,generation,docs.count,size,size.memory,committed,searchable,version,compound"
  "_cat/pending_tasks?v"
  "_cat/recovery?v&h=index,shard,time,type,stage,source_host,target_host,files_percent,bytes_percent"
  "_cat/plugins?v"
  "_cat/templates?v"
  "_cat/thread_pool?v&h=name,node_name,active,rejected,completed,queue,queue_size,largest,min,max,keep_alive,type"
  "_cat/fielddata?v"
)

CAT_DESCRIPTIONS=(
  "Index list with health, status, shard counts, doc counts, and store sizes"
  "Shard allocation across nodes — state, doc counts, and sizes"
  "Node list with heap/RAM/CPU usage and roles"
  "Cluster health — status, active/unassigned shards"
  "Index aliases"
  "Disk allocation per node"
  "Lucene segment details per shard"
  "Tasks waiting in the cluster task queue"
  "Active and completed shard recovery progress"
  "Installed plugins on each node"
  "Index templates"
  "Thread pool stats — active, rejected, queued threads"
  "Per-field fielddata memory usage"
)

# ──────────────────────────────────────────────
# Usage
# ──────────────────────────────────────────────
usage() {
  echo "Usage: $0 [namespace] [cat-command|ingest_progress] [--watch [seconds]]"
  echo ""
  echo "  namespace       Kubernetes namespace (default: os-jvector)"
  echo "  cat-command     One of the CAT API command names below (optional; prompts if omitted)"
  echo "  ingest_progress Poll _nodes/stats indexing counters (refresh-independent)"
  echo "  --watch [N]     Re-run every N seconds (default: 5). Only valid with a command."
  echo ""
  echo "Available CAT commands:"
  for i in "${!CAT_NAMES[@]}"; do
    printf "  %-20s %s\n" "${CAT_NAMES[$i]}" "${CAT_DESCRIPTIONS[$i]}"
  done
  printf "  %-20s %s\n" "ingest_progress" "Live ingest doc count from _nodes/stats (translog-level, no refresh needed)"
  echo ""
  echo "Examples:"
  echo "  $0                                    # interactive: pick namespace + command"
  echo "  $0 os-jvector                         # interactive command selection in os-jvector"
  echo "  $0 os-jvector indices                 # run _cat/indices in os-jvector"
  echo "  $0 os-jvector ingest_progress         # single snapshot of ingest counters"
  echo "  $0 os-jvector ingest_progress --watch # poll ingest counters every 5s"
  echo "  $0 os-jvector ingest_progress --watch 10  # poll every 10s"
}

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage
  exit 0
fi

# ──────────────────────────────────────────────
# Parse --watch flag (can appear as $3 or $4)
# ──────────────────────────────────────────────
WATCH_MODE=false
WATCH_INTERVAL=5
for arg in "$@"; do
  if [[ "$arg" == "--watch" ]]; then
    WATCH_MODE=true
  fi
done
# If --watch is $3, check for an optional interval as $4
if $WATCH_MODE; then
  # find position of --watch and grab next token if it's a number
  for i in $(seq 1 $#); do
    val="${!i}"
    if [[ "$val" == "--watch" ]]; then
      next=$((i+1))
      nextval="${!next}"
      if [[ "$nextval" =~ ^[0-9]+$ ]]; then
        WATCH_INTERVAL="$nextval"
      fi
    fi
  done
fi

echo "=========================================="
echo " OpenSearch CAT API Explorer"
echo "=========================================="
echo ""

# ──────────────────────────────────────────────
# Namespace selection
# ──────────────────────────────────────────────
select_namespace() {
  if [ -n "$1" ]; then
    NAMESPACE="$1"
    echo "Using namespace: $NAMESPACE"
    return
  fi

  # Default namespace — override by passing an argument or selecting from the list
  DEFAULT_NS="os-jvector"

  echo "📋 Fetching available namespaces..."
  NAMESPACES=$(kubectl get namespaces -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\n' | grep -E '^os-')

  NS_ARRAY=()
  while IFS= read -r line; do
    NS_ARRAY+=("$line")
  done <<< "$NAMESPACES"

  # Find the index of the default namespace in the list
  DEFAULT_IDX=0
  for i in "${!NS_ARRAY[@]}"; do
    if [[ "${NS_ARRAY[$i]}" == "$DEFAULT_NS" ]]; then
      DEFAULT_IDX=$((i+1))
      break
    fi
  done

  if [ ${#NS_ARRAY[@]} -eq 0 ]; then
    echo -e "${YELLOW}⚠️  No 'os-*' namespaces found — defaulting to '$DEFAULT_NS'${NC}"
    NAMESPACE="$DEFAULT_NS"
    return
  fi

  echo ""
  echo "Available Namespaces:"
  echo "---------------------"
  for i in "${!NS_ARRAY[@]}"; do
    if [[ "${NS_ARRAY[$i]}" == "$DEFAULT_NS" ]]; then
      echo -e "  $((i+1))) ${NS_ARRAY[$i]}  ${GREEN}(default)${NC}"
    else
      echo "  $((i+1))) ${NS_ARRAY[$i]}"
    fi
  done
  echo ""

  if [ "$DEFAULT_IDX" -gt 0 ]; then
    read -rp "Select namespace [default: $DEFAULT_NS]: " NS_SELECTION
  else
    read -rp "Select namespace (1-${#NS_ARRAY[@]}): " NS_SELECTION
  fi

  # Accept empty input → use default
  if [ -z "$NS_SELECTION" ]; then
    NAMESPACE="$DEFAULT_NS"
    echo "Using default namespace: $NAMESPACE"
    return
  fi

  if ! [[ "$NS_SELECTION" =~ ^[0-9]+$ ]] || [ "$NS_SELECTION" -lt 1 ] || [ "$NS_SELECTION" -gt ${#NS_ARRAY[@]} ]; then
    echo -e "${RED}❌ Invalid selection${NC}"
    exit 1
  fi

  NAMESPACE="${NS_ARRAY[$((NS_SELECTION-1))]}"
  echo ""
  echo "Selected namespace: $NAMESPACE"
}

select_namespace "$1"

# ──────────────────────────────────────────────
# Find a reachable data pod + container name
# ──────────────────────────────────────────────
echo ""
echo "🔎 Looking for an OpenSearch data pod in namespace '$NAMESPACE'..."

DATA_POD=$(kubectl get pods -n "$NAMESPACE" \
  -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\n' | \
  grep -E '^opensearch-data-' | sort | head -1)

if [ -z "$DATA_POD" ]; then
  # Fallback: any pod with opensearch in its name
  DATA_POD=$(kubectl get pods -n "$NAMESPACE" \
    -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '\n' | \
    grep -i opensearch | sort | head -1)
fi

if [ -z "$DATA_POD" ]; then
  echo -e "${RED}❌ No OpenSearch pods found in namespace '$NAMESPACE'${NC}"
  echo ""
  echo "Pods in namespace:"
  kubectl get pods -n "$NAMESPACE" 2>/dev/null || echo "(none)"
  exit 1
fi

# Auto-detect the container name — prefer 'opensearch', fall back to first container
OS_CONTAINER=$(kubectl get pod -n "$NAMESPACE" "$DATA_POD" \
  -o jsonpath='{.spec.containers[*].name}' 2>/dev/null | tr ' ' '\n' | \
  grep -i opensearch | head -1)

if [ -z "$OS_CONTAINER" ]; then
  OS_CONTAINER=$(kubectl get pod -n "$NAMESPACE" "$DATA_POD" \
    -o jsonpath='{.spec.containers[0].name}' 2>/dev/null)
fi

echo -e "${GREEN}✅ Using pod: $DATA_POD  container: $OS_CONTAINER${NC}"

# ──────────────────────────────────────────────
# CAT command selection
# ──────────────────────────────────────────────
select_cat_command() {
  local input="$1"

  if [ -n "$input" ]; then
    # Find index of the provided command name
    for i in "${!CAT_NAMES[@]}"; do
      if [[ "${CAT_NAMES[$i]}" == "$input" ]]; then
        CAT_INDEX=$i
        return
      fi
    done
    echo -e "${RED}❌ Unknown CAT command: '$input'${NC}"
    echo ""
    usage
    exit 1
  fi

  echo ""
  echo "Available CAT API Commands:"
  echo "----------------------------"
  for i in "${!CAT_NAMES[@]}"; do
    printf "  %2d) %-20s %s\n" "$((i+1))" "${CAT_NAMES[$i]}" "${CAT_DESCRIPTIONS[$i]}"
  done
  echo "   0) Run ALL commands"
  echo ""

  read -rp "Select a command (0-${#CAT_NAMES[@]}): " CMD_SELECTION

  if ! [[ "$CMD_SELECTION" =~ ^[0-9]+$ ]] || [ "$CMD_SELECTION" -lt 0 ] || [ "$CMD_SELECTION" -gt ${#CAT_NAMES[@]} ]; then
    echo -e "${RED}❌ Invalid selection${NC}"
    exit 1
  fi

  CAT_INDEX=$((CMD_SELECTION-1))   # -1 means "all" (when CMD_SELECTION == 0)
}

# Handle ingest_progress as a special non-CAT command before select_cat_command
INGEST_PROGRESS_MODE=false
if [[ "$2" == "ingest_progress" ]]; then
  INGEST_PROGRESS_MODE=true
fi

# ──────────────────────────────────────────────
# ingest_progress: _nodes/stats — translog-level
# ──────────────────────────────────────────────
run_ingest_progress() {
  local ts
  ts=$(date '+%H:%M:%S')

  echo ""
  echo -e "${CYAN}=========================================="
  echo " Ingest Progress  [$ts]"
  echo " _nodes/stats/indices/indexing  (pre-refresh)"
  echo -e "==========================================${NC}"

  # Per-index breakdown via _stats/indexing — primaries only to avoid replica double-counting
  local per_index
  per_index=$(kubectl exec -n "$NAMESPACE" -c "$OS_CONTAINER" "$DATA_POD" -- \
    curl -sk -u admin:admin \
    "https://localhost:9200/_stats/indexing" 2>/dev/null | \
    jq -r '
      .indices | to_entries[]
      | select(.key | startswith(".") | not)
      | [.key, (.value.primaries.indexing.index_total | tostring)]
      | @tsv
    ' 2>/dev/null)

  # Sum primaries across all indices for the cluster total
  local total_indexed
  total_indexed=$(kubectl exec -n "$NAMESPACE" -c "$OS_CONTAINER" "$DATA_POD" -- \
    curl -sk -u admin:admin \
    "https://localhost:9200/_stats/indexing" 2>/dev/null | \
    jq '[.indices[] | .primaries.indexing.index_total] | add // 0')

  echo ""
  echo -e "  Total docs indexed (primaries only, all indices): ${GREEN}${total_indexed}${NC}"
  echo ""

  if [ -n "$per_index" ]; then
    echo "  Per-index breakdown (primaries only):"
    echo "  --------------------------------------"
    printf "  %-45s %s\n" "INDEX" "DOCS INDEXED"
    printf "  %-45s %s\n" "-----" "------------"
    while IFS=$'\t' read -r idx_name idx_count; do
      printf "  %-45s %s\n" "$idx_name" "$idx_count"
    done <<< "$per_index"
  fi

  echo ""
  echo -e "${YELLOW}  ⚠  Counts are primary-shard only (no replica inflation)."
  echo -e "     They advance before any refresh — use _cat/indices for refreshed doc counts.${NC}"
  echo ""
}

if $INGEST_PROGRESS_MODE; then
  if $WATCH_MODE; then
    echo -e "${GREEN}Watching ingest progress every ${WATCH_INTERVAL}s — Ctrl+C to stop${NC}"
    while true; do
      run_ingest_progress
      sleep "$WATCH_INTERVAL"
    done
  else
    run_ingest_progress
    echo -e "${GREEN}✅ Done.${NC}"
  fi
  exit 0
fi

select_cat_command "$2"

# ──────────────────────────────────────────────
# Execute CAT API call(s)
# ──────────────────────────────────────────────
run_cat() {
  local idx="$1"
  local name="${CAT_NAMES[$idx]}"
  local endpoint="${CAT_ENDPOINTS[$idx]}"
  local desc="${CAT_DESCRIPTIONS[$idx]}"

  echo ""
  echo -e "${CYAN}=========================================="
  echo " _cat/$name"
  echo -e " $desc"
  echo -e "==========================================${NC}"

  kubectl exec -n "$NAMESPACE" -c "$OS_CONTAINER" "$DATA_POD" -- \
    curl -sk -u admin:admin "https://localhost:9200/${endpoint}" 2>/dev/null || \
    echo -e "${YELLOW}⚠️  No data returned for _cat/$name${NC}"

  echo ""
}

if [ "$CAT_INDEX" -eq -1 ]; then
  echo ""
  echo -e "${GREEN}Running all CAT API commands against namespace '$NAMESPACE'...${NC}"
  for i in "${!CAT_NAMES[@]}"; do
    run_cat "$i"
  done
else
  run_cat "$CAT_INDEX"
fi

echo -e "${GREEN}✅ Done.${NC}"

# Made with Bob
