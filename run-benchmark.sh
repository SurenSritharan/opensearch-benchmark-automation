#!/bin/bash
# OpenSearch Vector Benchmark Runner
# Modular script for running benchmark tests across multiple vector engines

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source all library modules
source "$SCRIPT_DIR/lib/cli-menu.sh"
source "$SCRIPT_DIR/lib/workload-params.sh"
source "$SCRIPT_DIR/lib/k8s-utils.sh"
source "$SCRIPT_DIR/lib/inject-templates.sh"
source "$SCRIPT_DIR/lib/scenarios.sh"
source "$SCRIPT_DIR/lib/profiling.sh"

# ==================================================================
# ⚙️ GLOBAL CONFIGURATION & DEFAULTS
# ==================================================================
# Set this to true/false to change the default script behavior
ENABLE_PROFILING=true

# Parse command line arguments and get user selections
parse_cli_args "$@"

# Display configuration and confirm
display_configuration

# Set up results directory
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
GLOBAL_RESULTS_BASE="./results/${TIMESTAMP}"
mkdir -p "$GLOBAL_RESULTS_BASE"

# Main engine loop
for ENGINE in "${TARGET_ENGINES[@]}"; do
  K8S_NAMESPACE="os-${ENGINE}"
  RESULTS_BASE="${GLOBAL_RESULTS_BASE}/${ENGINE}-metrics"
  mkdir -p "$RESULTS_BASE"
  
  ENGINE_INDEX_NAME="${ENGINE}_index"

  echo ""
  echo "🚀 =================================================================="
  echo "🚀 STARTING BENCHMARK LOOP MATRIX FOR VECTOR ENGINE: [ $ENGINE ]"
  echo "🚀 TARGETING KUBERNETES NAMESPACE: [ $K8S_NAMESPACE ]"
  echo "🚀 =================================================================="
  echo ""

  # Check pod status
  echo "Checking opensearch-benchmark-client status within $K8S_NAMESPACE..."
  POD_STATUS=$(check_pod_status "$K8S_NAMESPACE")

  if [ "$POD_STATUS" != "Running" ]; then
    echo "❌ Error: Benchmark client pod in namespace $K8S_NAMESPACE is status: [$POD_STATUS]."
    echo "Skipping engine $ENGINE..."
    continue
  fi

  # Inject index templates
  inject_all_templates "$K8S_NAMESPACE"

  # Check if index exists
  INDEX_EXISTS=$(check_index_exists "$K8S_NAMESPACE" "$ENGINE_INDEX_NAME")

  LOCAL_RUN_INDEX_CREATION=$RUN_INDEX_CREATION
  if [ "$INDEX_EXISTS" != "200" ] && ([ "$RUN_FORCE_MERGE" = true ] || [ "$RUN_SEARCH_TESTS" = true ]); then
    echo "ℹ️  Index absent in $K8S_NAMESPACE. Explicitly enabling schema generation."
    LOCAL_RUN_INDEX_CREATION=true
  fi

  # Run scenarios
  if [ "$LOCAL_RUN_INDEX_CREATION" = true ]; then
    if run_index_creation_scenario "$ENGINE" "$K8S_NAMESPACE" "$RESULTS_BASE"; then
      run_bulk_ingestion_scenario "$ENGINE" "$K8S_NAMESPACE" "$RESULTS_BASE"
      INDEX_EXISTS="200"
    else
      echo "❌ Index creation failed. Skipping remaining scenarios for $ENGINE."
      continue
    fi
  fi

  if [ "$RUN_FORCE_MERGE" = true ] && [ "$INDEX_EXISTS" = "200" ]; then
    run_force_merge_scenario "$ENGINE" "$K8S_NAMESPACE" "$RESULTS_BASE"
  fi

  if [ "$INDEX_EXISTS" = "200" ]; then
    collect_telemetry "$ENGINE" "$K8S_NAMESPACE" "$RESULTS_BASE"
  fi

  if [ "$RUN_SEARCH_TESTS" = true ] && [ "$INDEX_EXISTS" = "200" ]; then
    run_search_concurrency_scenario "$ENGINE" "$K8S_NAMESPACE" "$RESULTS_BASE"
  fi

done

echo ""
echo "=========================================="
echo "🏁 COMBINED MULTI-ENGINE MATRIX SWEEP COMPLETE!"
echo "=========================================="
echo "Master execution payloads stored at: $GLOBAL_RESULTS_BASE"

# Made with Bob
