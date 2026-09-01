#!/bin/bash
# Collect JVM thread dumps from all OpenSearch pods in a namespace.
#
# Each dump is captured two ways:
#   1. kill -3  → JVM writes SIGQUIT output to container stdout (kubectl logs)
#   2. REST API → GET /_nodes/hot_threads for a quick human-readable summary
#
# Dumps are written to:
#   <OUTPUT_DIR>/<pod>-thread-dump-<timestamp>.txt  (from kill -3 / container log tail)
#   <OUTPUT_DIR>/hot-threads-<timestamp>.txt        (REST hot_threads response)
#
# Usage:
#   scripts/collect-thread-dumps.sh <namespace> [output-dir] [--repeat N --interval S]
#
# Arguments:
#   namespace    Kubernetes namespace (e.g. os-jvector, os-faiss, os-lucene)
#   output-dir   Local directory to write dumps into (default: ./thread-dumps/<namespace>/<timestamp>)
#   --repeat N   Take N consecutive snapshots (default: 1)
#   --interval S Seconds between snapshots when --repeat > 1 (default: 5)
#
# Examples:
#   scripts/collect-thread-dumps.sh os-jvector
#   scripts/collect-thread-dumps.sh os-faiss ./results/thread-dumps
#   scripts/collect-thread-dumps.sh os-lucene ./out --repeat 3 --interval 10

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Parse arguments ───────────────────────────────────────────────────────────
if [ -z "${1:-}" ]; then
    echo -e "${YELLOW}Usage: $0 <namespace> [output-dir] [--repeat N] [--interval S]${NC}"
    echo "  namespace   e.g. os-jvector, os-faiss, os-lucene"
    echo "  output-dir  local directory for dumps (default: ./thread-dumps/<namespace>/<ts>)"
    echo "  --repeat N  number of snapshots to take (default: 1)"
    echo "  --interval S seconds between snapshots (default: 5)"
    exit 1
fi

NAMESPACE="$1"
shift

OUTPUT_DIR=""
REPEAT=1
INTERVAL=5

while [ "${1:-}" != "" ]; do
    case "$1" in
        --repeat)
            REPEAT="${2:?--repeat requires a value}"
            shift 2
            ;;
        --interval)
            INTERVAL="${2:?--interval requires a value}"
            shift 2
            ;;
        --*)
            echo -e "${RED}Unknown option: $1${NC}" >&2
            exit 1
            ;;
        *)
            OUTPUT_DIR="$1"
            shift
            ;;
    esac
done

TS=$(date -u '+%Y%m%d-%H%M%S')
OUTPUT_DIR="${OUTPUT_DIR:-thread-dumps/${NAMESPACE}/${TS}}"
mkdir -p "$OUTPUT_DIR"

# ── Discover pods ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../config/cluster.yaml"
POD_SELECTOR="app=opensearch-data"
if [ -f "$CONFIG_FILE" ]; then
    _sel=$(grep "pod_label_selector:" "$CONFIG_FILE" | sed 's/.*pod_label_selector: *"\(.*\)".*/\1/' | tr -d '"')
    [ -n "$_sel" ] && POD_SELECTOR="$_sel"
fi

PODS=$(kubectl get pods -n "$NAMESPACE" -l "$POD_SELECTOR" \
    --no-headers -o custom-columns=':metadata.name' 2>/dev/null || true)

if [ -z "$PODS" ]; then
    echo -e "${RED}❌ No pods found in namespace $NAMESPACE with selector '$POD_SELECTOR'${NC}"
    exit 1
fi

echo -e "${GREEN}Collecting thread dumps from namespace: $NAMESPACE${NC}"
echo "Output directory: $OUTPUT_DIR"
echo "Pods:"
for pod in $PODS; do echo "  - $pod"; done
echo ""

# ── Helper: one snapshot round ───────────────────────────────────────────────
_snapshot() {
    local snap_ts
    snap_ts=$(date -u '+%Y%m%d-%H%M%S')

    # ── Capture thread dump via jcmd, fall back to kill -3 / kubectl logs ─
    for pod in $PODS; do
        local outfile="${OUTPUT_DIR}/${pod}-thread-dump-${snap_ts}.txt"
        echo "  [${pod}] Fetching thread dump..."

        # jcmd streams directly to a local file without touching container log
        # buffers. PID 1 is always the JVM in these containers.
        if kubectl exec -n "$NAMESPACE" "$pod" -c opensearch -- \
                jcmd 1 Thread.print > "$outfile" 2>/dev/null; then
            local lines
            lines=$(wc -l < "$outfile" | tr -d ' ')
            echo -e "  ${GREEN}[${pod}] Saved — ${lines} lines${NC}"
        else
            echo -e "  ${YELLOW}[${pod}] jcmd failed; falling back to kill -3 / kubectl logs${NC}"
            kubectl exec -n "$NAMESPACE" "$pod" -c opensearch -- \
                kill -3 1 2>/dev/null || true
            sleep 1
            kubectl logs "$pod" -c opensearch -n "$NAMESPACE" \
                --tail=1000 > "$outfile" 2>/dev/null || true
        fi
    done

    # ── REST hot_threads ─────────────────────────────────────────────────
    # Proxy through the first data pod — no direct network path needed.
    local first_pod
    first_pod=$(echo "$PODS" | head -n 1)
    local ht_file="${OUTPUT_DIR}/hot-threads-${snap_ts}.txt"

    echo "  [hot_threads] Fetching /_nodes/hot_threads via ${first_pod}..."
    kubectl exec -n "$NAMESPACE" "$first_pod" -c opensearch -- \
        curl -sk -u "${OPENSEARCH_USER:-admin}:${OPENSEARCH_PASS:-admin}" \
        "https://localhost:9200/_nodes/hot_threads?threads=10&type=cpu&interval=500ms" \
        > "$ht_file" 2>/dev/null || echo "(hot_threads request failed)" >> "$ht_file"
    echo -e "  ${GREEN}[hot_threads] Saved → $(basename "$ht_file")${NC}"
}

# ── Main loop ─────────────────────────────────────────────────────────────────
for i in $(seq 1 "$REPEAT"); do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Snapshot ${i}/${REPEAT}  ($(date -u '+%Y-%m-%d %H:%M:%S UTC'))"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    _snapshot
    if [ "$i" -lt "$REPEAT" ]; then
        echo ""
        echo "Waiting ${INTERVAL}s before next snapshot..."
        sleep "$INTERVAL"
    fi
done

echo ""
echo -e "${GREEN}✅ Done — dumps written to: ${OUTPUT_DIR}${NC}"
ls -lh "$OUTPUT_DIR"

# Made with Bob
