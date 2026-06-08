#!/bin/bash
# GKE Metrics Collection Script
# Collects CPU, Memory, and Disk I/O metrics from GKE cluster nodes and pods
#
# Usage: ./collect-gke-metrics.sh <namespace> <output-file> <duration-seconds> <interval-seconds>
# Example: ./collect-gke-metrics.sh os-jvector metrics.json 300 10

set -e

NAMESPACE="${1:-os-jvector}"
OUTPUT_FILE="${2:-gke-metrics.json}"
DURATION="${3:-300}"
INTERVAL="${4:-10}"

echo "=========================================="
echo "GKE Metrics Collection"
echo "=========================================="
echo "Namespace: $NAMESPACE"
echo "Output: $OUTPUT_FILE"
echo "Duration: ${DURATION}s"
echo "Interval: ${INTERVAL}s"
echo "=========================================="
echo ""

# Initialize JSON structure
cat > "$OUTPUT_FILE" << EOF
{
  "collection_start": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "namespace": "$NAMESPACE",
  "interval_seconds": $INTERVAL,
  "duration_seconds": $DURATION,
  "samples": []
}
EOF

# Function to collect a single sample
collect_sample() {
    local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local epoch=$(date +%s)
    
    echo "  📊 Collecting sample at $timestamp"
    
    # Collect node metrics
    local node_metrics=$(kubectl top nodes --no-headers 2>/dev/null | awk '{
        printf "{\"name\":\"%s\",\"cpu\":\"%s\",\"cpu_percent\":\"%s\",\"memory\":\"%s\",\"memory_percent\":\"%s\"},", $1, $2, $3, $4, $5
    }' | sed 's/,$//')
    
    # Collect pod metrics for namespace
    local pod_metrics=$(kubectl top pods -n "$NAMESPACE" --no-headers 2>/dev/null | awk '{
        printf "{\"name\":\"%s\",\"cpu\":\"%s\",\"memory\":\"%s\"},", $1, $2, $3
    }' | sed 's/,$//')
    
    # Get pod to node mapping
    local pod_nodes=$(kubectl get pods -n "$NAMESPACE" -o json 2>/dev/null | jq -c '[.items[] | {name: .metadata.name, node: .spec.nodeName, status: .status.phase}]')
    
    # Get node pool information
    local node_pools=$(kubectl get nodes -o json 2>/dev/null | jq -c '[.items[] | {name: .metadata.name, pool: .metadata.labels["cloud.google.com/gke-nodepool"]}]')
    
    # Create sample JSON
    local sample=$(cat <<SAMPLE
{
  "timestamp": "$timestamp",
  "epoch": $epoch,
  "nodes": [$node_metrics],
  "pods": [$pod_metrics],
  "pod_nodes": $pod_nodes,
  "node_pools": $node_pools
}
SAMPLE
)
    
    # Append to samples array (using jq to properly format JSON)
    local temp_file="${OUTPUT_FILE}.tmp"
    jq ".samples += [$sample]" "$OUTPUT_FILE" > "$temp_file" && mv "$temp_file" "$OUTPUT_FILE"
}

# Main collection loop
echo "🚀 Starting metrics collection..."
echo ""

start_time=$(date +%s)
end_time=$((start_time + DURATION))
sample_count=0

while [ $(date +%s) -lt $end_time ]; do
    collect_sample
    sample_count=$((sample_count + 1))
    
    # Sleep until next interval
    sleep "$INTERVAL"
done

# Finalize JSON with end time and summary
collection_end=$(date -u +%Y-%m-%dT%H:%M:%SZ)
actual_duration=$(($(date +%s) - start_time))

jq ".collection_end = \"$collection_end\" | .actual_duration_seconds = $actual_duration | .total_samples = $sample_count" "$OUTPUT_FILE" > "${OUTPUT_FILE}.tmp" && mv "${OUTPUT_FILE}.tmp" "$OUTPUT_FILE"

echo ""
echo "=========================================="
echo "✅ Collection Complete"
echo "=========================================="
echo "Samples collected: $sample_count"
echo "Actual duration: ${actual_duration}s"
echo "Output file: $OUTPUT_FILE"
echo "=========================================="

# Generate summary statistics
echo ""
echo "📈 Generating summary statistics..."

jq '
{
  namespace: .namespace,
  duration: .actual_duration_seconds,
  samples: .total_samples,
  node_summary: (
    .samples | 
    group_by(.nodes[].name) | 
    map({
      node: .[0].nodes[0].name,
      samples: length,
      avg_cpu_percent: (map(.nodes[0].cpu_percent | rtrimstr("%") | tonumber) | add / length),
      max_cpu_percent: (map(.nodes[0].cpu_percent | rtrimstr("%") | tonumber) | max),
      avg_memory_percent: (map(.nodes[0].memory_percent | rtrimstr("%") | tonumber) | add / length),
      max_memory_percent: (map(.nodes[0].memory_percent | rtrimstr("%") | tonumber) | max)
    })
  ),
  pod_summary: (
    .samples |
    map(.pods[]) |
    group_by(.name) |
    map({
      pod: .[0].name,
      samples: length
    })
  )
}
' "$OUTPUT_FILE" > "${OUTPUT_FILE%.json}_summary.json"

echo "  💾 Summary saved to: ${OUTPUT_FILE%.json}_summary.json"
echo ""

# Made with Bob
