#!/usr/bin/env bash
# run-benchmark-parallel.sh - Wrapper for parallel benchmark execution

# Run setup script to ensure venv is ready
./setup-venv.sh

# Activate the virtual environment
source venv/bin/activate

# Hand off all arguments to the parallel execution script with --yes flag
python3 run_benchmark_parallel.py "$@"

# Made with Bob
