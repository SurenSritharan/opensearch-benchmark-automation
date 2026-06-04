#!/bin/bash
# Benchmark scenario runners

source "$(dirname "${BASH_SOURCE[0]}")/k8s-utils.sh"
source "$(dirname "${BASH_SOURCE[0]}")/workload-params.sh"
source "$(dirname "${BASH_SOURCE[0]}")/profiling.sh"

run_index_creation_scenario() {
  local engine="$1"
  local namespace="$2"
  local results_base="$3"
  local engine_index_name="${engine}_index"
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📦 SCENARIO: Create Index [$engine]"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  ▶ Running index creation..."
  echo ""
  clear_remote_logs "$namespace"
  
  local scenario_dir="$results_base/scenario-1-create-index"
  mkdir -p "$scenario_dir"
  
  echo "ℹ️  Index name: $engine_index_name"
  
  # Build and run index creation
  local index_params=$(build_index_params "$engine")
  echo "ℹ️  Index params: $index_params"
  
  run_benchmark "$namespace" "$index_params" "no-train-test-index-only" \
    "--include-tasks=delete-target-index,create-target-index" \
    "$scenario_dir/console.log"
  
  # Download artifacts
  download_artifacts "$scenario_dir" "$scenario_dir/console.log" "$namespace"
  
  # Validate index mapping
  echo "🔍 Validating index field parameters for $engine..."
  local current_mapping=$(get_index_mapping "$namespace" "$engine_index_name")
  
  echo "$current_mapping" > "$scenario_dir/index-mapping-validation.json"
  sync
  
  local field_type=$(echo "$current_mapping" | jq -r '.. | .target_field? | .type?' 2>/dev/null | grep -v "null" | head -n 1)
  echo "🎯 Cluster Field Mapping Detected: [$field_type]"
  
  if [ "$field_type" != "knn_vector" ]; then
    echo "❌ CRITICAL ERROR: Field validation failed on $engine - Evaluated to: $field_type"
    return 1
  fi
  echo "✅ Schema verification successful."
  
  return 0
}

run_bulk_ingestion_scenario() {
  local engine="$1"
  local namespace="$2"
  local results_base="$3"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "📥 SCENARIO: Bulk Ingestion [$engine]"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  ▶ Running bulk vector ingestion..."
  echo ""
  
  local scenario_dir="$results_base/scenario-2-custom-vector-bulk"
  mkdir -p "$scenario_dir"
  
  local index_params=$(build_index_params "$engine")
  
  # Start profiling if enabled
  if [ "$ENABLE_PROFILING" = true ]; then
    echo ""
    echo "🔥 Starting profiling for bulk ingestion..."
    local pods=$(get_opensearch_pods "$namespace")
    local pod_array=($pods)
    for i in "${!pod_array[@]}"; do
      start_node_profiling "$namespace" "${pod_array[$i]}" 300 "$scenario_dir" "bulk-node-$i" &
    done
    echo ""
  fi
  
  run_benchmark "$namespace" "$index_params" "no-train-test-index-only" \
    "--include-tasks=custom-vector-bulk" \
    "$scenario_dir/console.log"
  
  # Collect profiling artifacts if enabled
  if [ "$ENABLE_PROFILING" = true ]; then
    echo ""
    echo "🔥 Collecting profiling artifacts..."
    sleep 5  # Allow profilers to complete
    local pods=$(get_opensearch_pods "$namespace")
    local pod_array=($pods)
    for i in "${!pod_array[@]}"; do
      reap_profile_artifacts "$namespace" "${pod_array[$i]}" "bulk-node-$i" "$scenario_dir"
    done
    echo ""
  fi
  
  download_artifacts "$scenario_dir" "$scenario_dir/console.log" "$namespace"
}

run_force_merge_scenario() {
  local engine="$1"
  local namespace="$2"
  local results_base="$3"
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔨 SCENARIO: Force Merge [$engine]"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  ▶ Running force merge operation..."
  echo ""
  clear_remote_logs "$namespace"
  
  local scenario_dir="$results_base/scenario-force-merge-index"
  mkdir -p "$scenario_dir"
  
  local index_params=$(build_index_params "$engine")
  
  run_benchmark "$namespace" "$index_params" "force-merge-index" \
    "" \
    "$scenario_dir/console.log"
  
  download_artifacts "$scenario_dir" "$scenario_dir/console.log" "$namespace"
}

run_search_concurrency_scenario() {
  local engine="$1"
  local namespace="$2"
  local results_base="$3"
  
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔍 SCENARIO: Search Concurrency Matrix Sweeps [$engine]"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  local scenario_dir="$results_base/scenario-search-only"
  mkdir -p "$scenario_dir"
  
  # Create CSV header with all available metrics
  echo "Clients,Mean_Throughput_ops/s,Median_Latency_ms,p90_Latency_ms,p99_Latency_ms,p999_Latency_ms,p9999_Latency_ms,Max_Latency_ms,Median_Service_Time_ms,p90_Service_Time_ms,p99_Service_Time_ms,p999_Service_Time_ms,p9999_Service_Time_ms,Max_Service_Time_ms,Error_Rate_%,Recall@k,Recall@1,Young_Gen_GC_Time_s,Young_Gen_GC_Count,Old_Gen_GC_Time_s,Old_Gen_GC_Count" > "$scenario_dir/summary.csv"
  
  for clients in 10 20 30 40 50 60 70 80 90 100; do
    echo ""
    echo "  ▶ Running search test with ${clients} concurrent clients..."
    echo ""
    
    local run_dir="$scenario_dir/clients-${clients}"
    mkdir -p "$run_dir"
    
    clear_remote_logs "$namespace"
    
    # Build search params with search_clients
    local search_params=$(build_search_params "$engine" "$clients")
    
    # Start profiling if enabled
    if [ "$ENABLE_PROFILING" = true ]; then
      echo ""
      echo "🔥 Starting profiling for ${clients} clients..."
      local pods=$(get_opensearch_pods "$namespace")
      local pod_array=($pods)
      for i in "${!pod_array[@]}"; do
        start_node_profiling "$namespace" "${pod_array[$i]}" 120 "$run_dir" "search-${clients}c-node-$i" &
      done
      echo ""
    fi
    
    run_benchmark "$namespace" "$search_params" "search-only" \
      "--exclude-tasks=warmup-indices" \
      "$run_dir/console.log"
    
    # Collect profiling artifacts if enabled
    if [ "$ENABLE_PROFILING" = true ]; then
      echo ""
      echo "🔥 Collecting profiling artifacts..."
      sleep 5  # Allow profilers to complete
      local pods=$(get_opensearch_pods "$namespace")
      local pod_array=($pods)
      for i in "${!pod_array[@]}"; do
        reap_profile_artifacts "$namespace" "${pod_array[$i]}" "search-${clients}c-node-$i" "$run_dir"
      done
      echo ""
    fi
    
    download_artifacts "$run_dir" "$run_dir/console.log" "$namespace"
    
    # Parse performance metrics - all available statistics
    local json_file="$run_dir/test_run.json"
    if [ -f "$json_file" ]; then
      # Throughput
      local throughput=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .throughput.mean // 0' "$json_file")
      
      # Latency percentiles (keys use underscores: 50_0 not "50.0")
      local median=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .latency."50_0" // 0' "$json_file")
      local p90=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .latency."90_0" // 0' "$json_file")
      local p99=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .latency."99_0" // 0' "$json_file")
      local p999=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .latency."99_9" // 0' "$json_file")
      local p9999=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .latency."99_99" // 0' "$json_file")
      local max=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .latency."100_0" // 0' "$json_file")
      
      # Service time percentiles
      local svc_median=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .service_time."50_0" // 0' "$json_file")
      local svc_p90=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .service_time."90_0" // 0' "$json_file")
      local svc_p99=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .service_time."99_0" // 0' "$json_file")
      local svc_p999=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .service_time."99_9" // 0' "$json_file")
      local svc_p9999=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .service_time."99_99" // 0' "$json_file")
      local svc_max=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .service_time."100_0" // 0' "$json_file")
      
      # Error rate from op_metrics
      local error=$(jq -r '.results.op_metrics[] | select(.task=="prod-queries") | .error_rate // 0' "$json_file")
      
      # Recall metrics from correctness_metrics (separate array)
      local recall_k=$(jq -r '.results.correctness_metrics[] | select(.task=="prod-queries") | .["recall@k"].mean // 0' "$json_file")
      local recall_1=$(jq -r '.results.correctness_metrics[] | select(.task=="prod-queries") | .["recall@1"].mean // 0' "$json_file")
      
      # GC metrics from results root level
      local young_gc_time=$(jq -r '.results.young_gc_time // 0' "$json_file")
      local young_gc_count=$(jq -r '.results.young_gc_count // 0' "$json_file")
      local old_gc_time=$(jq -r '.results.old_gc_time // 0' "$json_file")
      local old_gc_count=$(jq -r '.results.old_gc_count // 0' "$json_file")
      
      echo "$clients,$throughput,$median,$p90,$p99,$p999,$p9999,$max,$svc_median,$svc_p90,$svc_p99,$svc_p999,$svc_p9999,$svc_max,$error,$recall_k,$recall_1,$young_gc_time,$young_gc_count,$old_gc_time,$old_gc_count" >> "$scenario_dir/summary.csv"
    fi
    sleep 2
  done
  
  echo "✅ Engine performance matrix output mapped to: $scenario_dir/summary.csv"
}

collect_telemetry() {
  local engine="$1"
  local namespace="$2"
  local results_base="$3"
  local engine_index_name="${engine}_index"
  
  local telemetry_dir="$results_base/cluster-telemetry-state"
  mkdir -p "$telemetry_dir"
  
  echo "📊 Collecting comprehensive cluster telemetry..."
  
  # Cluster health and status
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/_cluster/health?pretty" \
    > "$telemetry_dir/cluster-health.json" || true
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/_cluster/stats?pretty" \
    > "$telemetry_dir/cluster-stats.json" || true
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/_cluster/settings?include_defaults=true&flat_settings=true&pretty" \
    > "$telemetry_dir/cluster-settings.json" || true
  
  # Node information
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/_cat/nodes?v&h=name,heap.percent,heap.current,heap.max,ram.percent,ram.current,ram.max,cpu,load_1m,load_5m,load_15m" \
    > "$telemetry_dir/cluster-nodes.txt" || true
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/_nodes/stats?pretty" \
    > "$telemetry_dir/nodes-stats.json" || true
  
  # Index information
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/${engine_index_name}/_settings?pretty" \
    > "$telemetry_dir/index-settings.json" || true
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/${engine_index_name}/_mapping?pretty" \
    > "$telemetry_dir/index-mapping.json" || true
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/${engine_index_name}/_stats?pretty" \
    > "$telemetry_dir/index-stats.json" || true
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/_cat/indices/${engine_index_name}?v&h=index,health,status,pri,rep,docs.count,store.size,pri.store.size" \
    > "$telemetry_dir/index-info.txt" || true
  
  # Thread pool statistics
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,largest,completed,size" \
    > "$telemetry_dir/thread-pools.txt" || true
  
  echo "✅ Comprehensive telemetry collected in: $telemetry_dir"
}

# Made with Bob
