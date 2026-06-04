#!/bin/bash
# Kubernetes utility functions

BENCHMARK_HOME="/datasets/opensearch-benchmark"

download_artifacts() {
  local target_dir="$1"
  local log_source="$2"
  local namespace="$3"
  
  local run_id=$(grep -oE "[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}" "$log_source" | head -n 1)

  if [ -n "$run_id" ]; then
    local remote_json_path="$BENCHMARK_HOME/.osb/benchmarks/test-runs/$run_id/test_run.json"
    kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
      cat "$remote_json_path" > "$target_dir/test_run.json" 2>/dev/null || true
  fi
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    cat "$BENCHMARK_HOME/.osb/logs/benchmark.log" > "$target_dir/benchmark.log" 2>/dev/null || true
}

clear_remote_logs() {
  local namespace="$1"
  
  kubectl exec opensearch-benchmark-client -n "$namespace" -c benchmark -- \
    truncate -s 0 /datasets/opensearch-benchmark/.osb/logs/benchmark.log 2>/dev/null || true
}

check_pod_status() {
  local namespace="$1"
  
  kubectl get pod opensearch-benchmark-client -n "$namespace" -o jsonpath='{.status.phase}' 2>/dev/null || echo "NotFound"
}

check_index_exists() {
  local namespace="$1"
  local index_name="$2"
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin -o /dev/null -w "%{http_code}" \
    "https://opensearch-cluster:9200/${index_name}" 2>/dev/null | tr -d '\r'
}

get_index_mapping() {
  local namespace="$1"
  local index_name="$2"
  
  kubectl exec -n "$namespace" -c benchmark opensearch-benchmark-client -- \
    curl -sk -u admin:admin "https://opensearch-cluster:9200/${index_name}/_mapping" 2>/dev/null || echo "failed"
}

run_benchmark() {
  local namespace="$1"
  local workload_params="$2"
  local test_procedure="$3"
  local tasks="$4"
  local output_log="$5"
  
  local compacted_params=$(echo "$workload_params" | jq -c .)
  
  kubectl exec -it opensearch-benchmark-client -n "$namespace" -- \
    opensearch-benchmark run \
      --pipeline=benchmark-only \
      --workload-path=/root/opensearch-benchmark-workloads/vectorsearch \
      --workload-params="$compacted_params" \
      --test-procedure="$test_procedure" \
      $tasks \
      --target-host=opensearch-cluster:9200 \
      --client-options="timeout:300,use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin" \
      --kill-running-processes \
      2>&1 | tee "$output_log"
}

# Made with Bob
