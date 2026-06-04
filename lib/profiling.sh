# ===========================================================================
# Telemetry Profiling Library Module
# ===========================================================================

# Bootstrap profiling assets inside target pod's ephemeral space if missing
bootstrap_profiler() {
  local namespace="$1"
  local pod_name="$2"
  
  # Ensure async-profiler is unpacked in /tmp for JVM CPU tracking
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- bash -c '
    if [ ! -f /tmp/async-profiler/profiler.sh ]; then
      cd /tmp && curl -skL https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz | tar -xzf -
    fi
  ' 2>/dev/null || true
}

# Starts async background collection profiles
start_node_profiling() {
  local namespace="$1"
  local pod_name="$2"
  local duration="$3"
  local output_dir="$4"
  local label="$5"

  bootstrap_profiler "$namespace" "$pod_name"

  echo "⏱️  Spinning up profile collection suite on $pod_name ($label) for ${duration}s..."

  # 1. Background CPU Profiler (Async-Profiler Flame Graph)
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- \
    /tmp/async-profiler/profiler.sh -d "$duration" -f /tmp/cpu_profile_${label}.html 1 >/dev/null 2>&1 &
  
  # 2. Background Disk I/O Delta Profiler
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- bash -c "
    echo '=== Disk Stats Start ===' > /tmp/disk_${label}.log
    cat /proc/diskstats >> /tmp/disk_${label}.log
    sleep $duration
    echo '=== Disk Stats End ===' >> /tmp/disk_${label}.log
    cat /proc/diskstats >> /tmp/disk_${label}.log
  " &

  # 3. Background Memory Tracking (JVM Pool snapshots + NMT)
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- bash -c "
    echo '=== JVM Heap & Native Memory Snapshot ===' > /tmp/memory_${label}.log
    curl -sk -u admin:admin https://localhost:9200/_nodes/_local/stats/jvm?pretty >> /tmp/memory_${label}.log
    jcmd 1 VM.native_memory summary 2>/dev/null >> /tmp/memory_${label}.log || true
  " &
}

# Pulls generated telemetry files down to host environment execution workspace
reap_profile_artifacts() {
  local namespace="$1"
  local pod_name="$2"
  local label="$3"
  local target_dir="$4"

  # Allow background threads to finalize writes
  sleep 1

  mkdir -p "$target_dir/profiles"
  
  # Extract file payloads safely
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- cat /tmp/cpu_profile_${label}.html > "$target_dir/profiles/cpu_flame_graph_${label}.html" 2>/dev/null || true
  kubectl exec -n "$namespace" "$prefix" "$pod_name" -c opensearch -- cat /tmp/disk_${label}.log > "$target_dir/profiles/disk_io_${label}.log" 2>/dev/null || true
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- cat /tmp/memory_${label}.log > "$target_dir/profiles/jvm_memory_${label}.log" 2>/dev/null || true
  
  # Clean up ephemeral container workspace files
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- rm -f /tmp/cpu_profile_${label}.html /tmp/disk_${label}.log /tmp/memory_${label}.log 2>/dev/null || true
}