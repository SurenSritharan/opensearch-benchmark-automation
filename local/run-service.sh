#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKLOADS_DIR="${WORKLOADS_DIR:-${REPO_ROOT}/../opensearch-benchmark-workloads}"
RESULTS_DIR="${RESULTS_DIR:-${REPO_ROOT}/results/local-service}"

export WORKSPACE_DIR="${WORKSPACE_DIR:-${REPO_ROOT}}"
export WORKLOADS_DIR
export RESULTS_DIR
export DB_PATH="${DB_PATH:-${RESULTS_DIR}/jobs.db}"
export LOCKS_DIR="${LOCKS_DIR:-${RESULTS_DIR}/locks}"
export DATASETS_ROOT="${DATASETS_ROOT:-${REPO_ROOT}/datasets}"
export BENCHMARK_HOME="${BENCHMARK_HOME:-${DATASETS_ROOT}/opensearch-benchmark}"
export RUN_DIR="${RUN_DIR:-${BENCHMARK_HOME}/run}"
export TEMP_DIR="${TEMP_DIR:-${RESULTS_DIR}/tmp}"
export APP_DIR="${APP_DIR:-${REPO_ROOT}/cloud-service}"
export TARGET_HOST="${TARGET_HOST:-127.0.0.1:9200}"
export USE_SSL="${USE_SSL:-false}"
export VERIFY_CERTS="${VERIFY_CERTS:-false}"
export AUTH_USER="${AUTH_USER:-admin}"
export AUTH_PASS="${AUTH_PASS:-admin}"
export WORKER_ENGINES="${WORKER_ENGINES:-jvector}"
export WORKER_MODE="${WORKER_MODE:-combined}"
export ENABLE_K8S_METRICS="${ENABLE_K8S_METRICS:-false}"

mkdir -p "${RESULTS_DIR}" "${BENCHMARK_HOME}" "${DATASETS_ROOT}" "${TEMP_DIR}"

cd "${REPO_ROOT}/cloud-service"
exec gunicorn \
  --bind "${BIND_ADDRESS:-127.0.0.1:8080}" \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-7200}" \
  --access-logfile - \
  app:app
