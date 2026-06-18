#!/bin/bash
# Automated benchmark execution script using cloud-native service
# This script provisions clusters and triggers benchmarks via REST API

set -e

# Configuration
BENCHMARK_API_NAMESPACE="${BENCHMARK_API_NAMESPACE:-benchmark-api}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

# Get API endpoint
get_api_endpoint() {
    local external_ip=$(kubectl get svc benchmark-api -n "$BENCHMARK_API_NAMESPACE" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
    
    if [ -n "$external_ip" ]; then
        echo "http://$external_ip"
    else
        log_warning "LoadBalancer IP not available, using port-forward"
        # Start port-forward in background
        kubectl port-forward -n "$BENCHMARK_API_NAMESPACE" svc/benchmark-api 8080:80 >/dev/null 2>&1 &
        PORT_FORWARD_PID=$!
        sleep 2
        echo "http://localhost:8080"
    fi
}

# Check if API is healthy
check_api_health() {
    local api_endpoint=$1
    local max_attempts=30
    local attempt=1
    
    log_info "Checking API health..."
    
    while [ $attempt -le $max_attempts ]; do
        if curl -s -f "$api_endpoint/health" >/dev/null 2>&1; then
            log_success "API is healthy"
            return 0
        fi
        
        log_info "Waiting for API to be ready (attempt $attempt/$max_attempts)..."
        sleep 2
        ((attempt++))
    done
    
    log_error "API health check failed after $max_attempts attempts"
    return 1
}

# Provision cluster for an engine
provision_cluster() {
    local engine=$1
    local namespace="os-$engine"
    
    log_info "Provisioning cluster for engine: $engine (namespace: $namespace)"
    
    # Check if cluster already exists
    if kubectl get namespace "$namespace" >/dev/null 2>&1; then
        log_info "Namespace $namespace already exists, checking cluster health..."
        
        # Check if pods are running
        local ready_pods=$(kubectl get pods -n "$namespace" -l app=opensearch-data --field-selector=status.phase=Running 2>/dev/null | grep -c "Running" || echo "0")
        
        if [ "$ready_pods" -gt 0 ]; then
            log_success "Cluster $namespace is already running ($ready_pods pods)"
            return 0
        fi
    fi
    
    # Deploy cluster
    log_info "Deploying cluster to $namespace..."
    if [ -f "$PROJECT_ROOT/gke-manifest/deploy-namespace-cluster.sh" ]; then
        "$PROJECT_ROOT/gke-manifest/deploy-namespace-cluster.sh" "$namespace" --force
        log_success "Cluster $namespace deployed"
    else
        log_error "Deployment script not found"
        return 1
    fi
}

# Trigger benchmark via API
trigger_benchmark() {
    local api_endpoint=$1
    local dataset=$2
    local engine=$3
    local scenario=$4
    
    log_info "Triggering benchmark: dataset=$dataset, engine=$engine, scenario=$scenario"
    
    local payload=$(cat <<EOF
{
  "dataset": "$dataset",
  "engines": "$engine",
  "scenarios": "$scenario"
}
EOF
)
    
    local response=$(curl -s -X POST "$api_endpoint/api/v1/benchmark" \
        -H "Content-Type: application/json" \
        -d "$payload")
    
    local job_id=$(echo "$response" | jq -r '.job_id // empty')
    
    if [ -n "$job_id" ]; then
        log_success "Benchmark job started: $job_id"
        echo "$job_id"
        return 0
    else
        log_error "Failed to start benchmark"
        echo "$response" | jq '.' 2>/dev/null || echo "$response"
        return 1
    fi
}

# Wait for job completion
wait_for_job() {
    local api_endpoint=$1
    local job_id=$2
    local timeout=${3:-3600}  # Default 1 hour timeout
    
    log_info "Waiting for job $job_id to complete (timeout: ${timeout}s)..."
    
    local start_time=$(date +%s)
    local last_status=""
    
    while true; do
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [ $elapsed -gt $timeout ]; then
            log_error "Job $job_id timed out after ${timeout}s"
            return 1
        fi
        
        local response=$(curl -s "$api_endpoint/api/v1/benchmark/$job_id")
        local status=$(echo "$response" | jq -r '.status // "unknown"')
        
        # Show status change
        if [ "$status" != "$last_status" ]; then
            log_info "Job $job_id status: $status"
            last_status=$status
        fi
        
        case "$status" in
            "completed")
                local duration=$(echo "$response" | jq -r '.duration_seconds // "N/A"')
                log_success "Job $job_id completed in ${duration}s"
                return 0
                ;;
            "failed"|"error")
                log_error "Job $job_id failed"
                echo "$response" | jq '.error // .stderr_tail' 2>/dev/null
                return 1
                ;;
            "running"|"queued")
                # Still running, continue waiting
                sleep 10
                ;;
            *)
                log_warning "Unknown status: $status"
                sleep 10
                ;;
        esac
    done
}

# Main execution
main() {
    echo "=========================================="
    echo "🚀 Automated Benchmark Execution"
    echo "=========================================="
    echo ""
    
    # Parse arguments
    local dataset="${1:-cohere-1m-768-dp}"
    local engines="${2:-jvector}"
    local scenario="${3:-search}"
    
    log_info "Configuration:"
    log_info "  Dataset: $dataset"
    log_info "  Engines: $engines"
    log_info "  Scenario: $scenario"
    echo ""
    
    # Step 1: Get API endpoint
    log_info "Step 1: Getting API endpoint..."
    API_ENDPOINT=$(get_api_endpoint)
    log_success "API endpoint: $API_ENDPOINT"
    echo ""
    
    # Step 2: Check API health
    log_info "Step 2: Checking API health..."
    if ! check_api_health "$API_ENDPOINT"; then
        log_error "API is not healthy, aborting"
        exit 1
    fi
    echo ""
    
    # Step 3: Provision clusters
    log_info "Step 3: Provisioning clusters..."
    IFS=',' read -ra ENGINE_ARRAY <<< "$engines"
    for engine in "${ENGINE_ARRAY[@]}"; do
        engine=$(echo "$engine" | xargs)  # Trim whitespace
        if ! provision_cluster "$engine"; then
            log_error "Failed to provision cluster for $engine"
            exit 1
        fi
    done
    echo ""
    
    # Step 4: Trigger benchmarks
    log_info "Step 4: Triggering benchmarks..."
    declare -a JOB_IDS
    for engine in "${ENGINE_ARRAY[@]}"; do
        engine=$(echo "$engine" | xargs)
        job_id=$(trigger_benchmark "$API_ENDPOINT" "$dataset" "$engine" "$scenario")
        if [ $? -eq 0 ]; then
            JOB_IDS+=("$job_id")
        else
            log_error "Failed to trigger benchmark for $engine"
        fi
    done
    echo ""
    
    # Step 5: Wait for completion
    log_info "Step 5: Waiting for benchmarks to complete..."
    local all_success=true
    for job_id in "${JOB_IDS[@]}"; do
        if ! wait_for_job "$API_ENDPOINT" "$job_id"; then
            all_success=false
        fi
    done
    echo ""
    
    # Cleanup
    if [ -n "$PORT_FORWARD_PID" ]; then
        kill $PORT_FORWARD_PID 2>/dev/null || true
    fi
    
    # Summary
    echo "=========================================="
    if [ "$all_success" = true ]; then
        log_success "All benchmarks completed successfully!"
        echo ""
        log_info "View results:"
        log_info "  Web UI: $API_ENDPOINT"
        log_info "  API: $API_ENDPOINT/api/v1/benchmark"
    else
        log_error "Some benchmarks failed"
        exit 1
    fi
    echo "=========================================="
}

# Run main function
main "$@"

# Made with Bob
