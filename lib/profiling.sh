# ===========================================================================
# Telemetry Profiling Library Module
# ===========================================================================

# Bootstrap profiling assets inside target pod's ephemeral space if missing
bootstrap_profiler() {
  local namespace="$1"
  local pod_name="$2"
  
  echo "  🔧 Bootstrapping async-profiler on pod: $pod_name"
  
  # Ensure async-profiler is unpacked in /tmp for JVM CPU tracking
  local output=$(kubectl exec -n "$namespace" "$pod_name" -c opensearch -- bash -c '
    if [ ! -f /tmp/async-profiler/bin/asprof ]; then
      cd /tmp
      curl -skL https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz -o async-profiler.tar.gz
      if [ ! -f async-profiler.tar.gz ]; then
        echo "ERROR: Failed to download async-profiler"
        exit 1
      fi
      
      tar -xzf async-profiler.tar.gz
      if [ $? -ne 0 ]; then
        echo "ERROR: Failed to extract async-profiler"
        exit 1
      fi
      rm -f async-profiler.tar.gz
      
      # Create symlink if needed
      if [ -d /tmp/async-profiler-3.0-linux-x64 ] && [ ! -d /tmp/async-profiler ]; then
        ln -s /tmp/async-profiler-3.0-linux-x64 /tmp/async-profiler
      fi
      
      # Verify asprof exists in bin directory (v3.0 uses asprof, not profiler.sh)
      if [ -f /tmp/async-profiler/bin/asprof ]; then
        chmod +x /tmp/async-profiler/bin/asprof
        echo "SUCCESS: Profiler installed"
        exit 0
      else
        echo "ERROR: asprof not found at /tmp/async-profiler/bin/asprof"
        ls -la /tmp/async-profiler/bin/ 2>&1 || echo "bin directory does not exist"
        exit 1
      fi
    else
      echo "SUCCESS: Profiler already installed"
      exit 0
    fi
  ' 2>&1)
  
  local exit_code=$?
  
  if [ $exit_code -eq 0 ]; then
    if echo "$output" | grep -q "SUCCESS"; then
      echo "  ✅ Profiler bootstrap successful on $pod_name"
      return 0
    fi
  fi
  
  echo "  ❌ Failed to bootstrap profiler on $pod_name"
  echo "$output" | sed 's/^/     /'
  return 1
}

# Starts async background collection profiles
start_node_profiling() {
  local namespace="$1"
  local pod_name="$2"
  local duration="$3"
  local output_dir="$4"
  local label="$5"

  # Create a log file for this pod's profiling output
  local log_file="$output_dir/profiling-${label}.log"
  mkdir -p "$output_dir"
  
  {
    if ! bootstrap_profiler "$namespace" "$pod_name"; then
      echo "  ⚠️  Skipping profiling for $pod_name due to bootstrap failure"
      return 1
    fi

    echo "  ⏱️  Starting profile collection on $pod_name ($label) for ${duration}s..."

    # 1. Background CPU Profiler (Async-Profiler Flame Graph)
    # Stop any existing profiler first, then start new one
    echo "  📊 Starting CPU profiler..."
    kubectl exec -n "$namespace" "$pod_name" -c opensearch -- \
      bash -c "
        # Stop any existing profiler
        /tmp/async-profiler/bin/asprof stop 1 2>/dev/null || true
        # Start new profiling session
        cd /tmp && /tmp/async-profiler/bin/asprof collect -d $duration -f /tmp/cpu_profile_${label}.html 1
      " >/dev/null 2>&1 &
    local cpu_pid=$!
    echo "  ✅ CPU profiler started (PID: $cpu_pid)"
    
    # 2. Background Disk I/O Delta Profiler
    echo "  💾 Starting disk I/O profiler..."
    kubectl exec -n "$namespace" "$pod_name" -c opensearch -- bash -c "
      echo '=== Disk Stats Start ===' > /tmp/disk_${label}.log
      cat /proc/diskstats >> /tmp/disk_${label}.log
      sleep $duration
      echo '=== Disk Stats End ===' >> /tmp/disk_${label}.log
      cat /proc/diskstats >> /tmp/disk_${label}.log
    " >/dev/null 2>&1 &
    local disk_pid=$!
    echo "  ✅ Disk I/O profiler started (PID: $disk_pid)"

    # 3. Background Memory Tracking (JVM Pool snapshots + NMT)
    echo "  🧠 Starting memory profiler..."
    kubectl exec -n "$namespace" "$pod_name" -c opensearch -- bash -c "
      echo '=== JVM Heap & Native Memory Snapshot ===' > /tmp/memory_${label}.log
      curl -sk -u admin:admin https://localhost:9200/_nodes/_local/stats/jvm?pretty >> /tmp/memory_${label}.log
      jcmd 1 VM.native_memory summary 2>/dev/null >> /tmp/memory_${label}.log || true
    " >/dev/null 2>&1 &
    local mem_pid=$!
    echo "  ✅ Memory profiler started (PID: $mem_pid)"
  } > "$log_file" 2>&1
  
  # Show summary on console
  echo "  ✅ Profiling started for $pod_name (log: $(basename $log_file))"
}

# Pulls generated telemetry files down to host environment execution workspace
reap_profile_artifacts() {
  local namespace="$1"
  local pod_name="$2"
  local label="$3"
  local target_dir="$4"

  echo "  📥 Collecting profiling artifacts from $pod_name ($label)..."
  
  # Allow background threads to finalize writes
  sleep 1

  mkdir -p "$target_dir/profiles"
  
  # Check if files exist before downloading
  echo "  🔍 Checking for profiling artifacts..."
  local files_found=0
  
  # Extract CPU flame graph
  if kubectl exec -n "$namespace" "$pod_name" -c opensearch -- test -f /tmp/cpu_profile_${label}.html 2>/dev/null; then
    echo "  📊 Downloading CPU flame graph..."
    if kubectl exec -n "$namespace" "$pod_name" -c opensearch -- cat /tmp/cpu_profile_${label}.html > "$target_dir/profiles/cpu_flame_graph_${label}.html" 2>/dev/null; then
      local size=$(wc -c < "$target_dir/profiles/cpu_flame_graph_${label}.html")
      echo "  ✅ CPU flame graph collected (${size} bytes)"
      ((files_found++))
    else
      echo "  ❌ Failed to download CPU flame graph"
    fi
  else
    echo "  ⚠️  CPU flame graph not found on pod"
  fi
  
  # Extract disk I/O log
  if kubectl exec -n "$namespace" "$pod_name" -c opensearch -- test -f /tmp/disk_${label}.log 2>/dev/null; then
    echo "  💾 Downloading disk I/O stats..."
    if kubectl exec -n "$namespace" "$pod_name" -c opensearch -- cat /tmp/disk_${label}.log > "$target_dir/profiles/disk_io_${label}.log" 2>/dev/null; then
      local size=$(wc -c < "$target_dir/profiles/disk_io_${label}.log")
      echo "  ✅ Disk I/O stats collected (${size} bytes)"
      ((files_found++))
    else
      echo "  ❌ Failed to download disk I/O stats"
    fi
  else
    echo "  ⚠️  Disk I/O stats not found on pod"
  fi
  
  # Extract memory log
  if kubectl exec -n "$namespace" "$pod_name" -c opensearch -- test -f /tmp/memory_${label}.log 2>/dev/null; then
    echo "  🧠 Downloading memory stats..."
    if kubectl exec -n "$namespace" "$pod_name" -c opensearch -- cat /tmp/memory_${label}.log > "$target_dir/profiles/jvm_memory_${label}.log" 2>/dev/null; then
      local size=$(wc -c < "$target_dir/profiles/jvm_memory_${label}.log")
      echo "  ✅ Memory stats collected (${size} bytes)"
      ((files_found++))
    else
      echo "  ❌ Failed to download memory stats"
    fi
  else
    echo "  ⚠️  Memory stats not found on pod"
  fi
  
  if [ $files_found -eq 0 ]; then
    echo "  ❌ No profiling artifacts found on $pod_name"
  else
    echo "  ✅ Collected $files_found profiling artifact(s) from $pod_name"
  fi
  
  # Clean up ephemeral container workspace files
  echo "  🧹 Cleaning up temporary files on pod..."
  kubectl exec -n "$namespace" "$pod_name" -c opensearch -- rm -f /tmp/cpu_profile_${label}.html /tmp/disk_${label}.log /tmp/memory_${label}.log 2>/dev/null || true
}