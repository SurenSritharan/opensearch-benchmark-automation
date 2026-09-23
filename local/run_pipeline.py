#!/usr/bin/env python3
"""Local pipeline runner for OpenSearch Benchmark Automation.

Executes a pipeline JSON file against a local or remote OpenSearch instance
without requiring Kubernetes, GKE, or the Flask cloud-service API server.

Reuses ConfigLoader to resolve parameter hierarchy, Jinja templates, and dynamically
strip unused workload parameters for each scenario.

Usage:
    python3 local/run_pipeline.py \\
        --pipeline parquet-50k \\
        --engine jvector \\
        --target-host at-opensearch.dev.fyre.ibm.com:9200 \\
        --workloads-dir /path/to/opensearch-benchmark-workloads \\
        --results-dir ./results/1
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Add cloud-service directory to sys.path to import ConfigLoader
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "cloud-service"))

from config_loader import ConfigLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("local-runner")


def parse_args():
    parser = argparse.ArgumentParser(description="Run benchmark pipelines locally without K8s")
    parser.add_argument("--pipeline", required=True, help="Pipeline name (e.g. parquet-50k or pipelines/parquet-50k.json)")
    parser.add_argument("--engine", default="jvector", help="Engine target (jvector, faiss, lucene)")
    parser.add_argument("--target-host", default=os.environ.get("TARGET_HOST", "127.0.0.1:9200"), help="OpenSearch host:port")
    parser.add_argument("--workloads-dir", default=os.environ.get("WORKLOADS_DIR", str(REPO_ROOT.parent / "opensearch-benchmark-workloads")), help="Path to workloads repository")
    parser.add_argument("--results-dir", default=os.environ.get("RESULTS_DIR", str(REPO_ROOT / "results" / "local")), help="Results output directory")
    parser.add_argument("--use-ssl", default=os.environ.get("USE_SSL", "true"), help="Use SSL/HTTPS (true/false)")
    parser.add_argument("--auth-user", default=os.environ.get("AUTH_USER", "admin"), help="OpenSearch Basic Auth Username")
    parser.add_argument("--auth-pass", default=os.environ.get("AUTH_PASS", "admin"), help="OpenSearch Basic Auth Password")
    parser.add_argument("--timeout", default="300", help="Client timeout in seconds")
    return parser.parse_args()


def main():
    args = parse_args()
    workspace = REPO_ROOT
    workloads_dir = Path(args.workloads_dir).resolve()
    results_dir = Path(args.results_dir).resolve()
    benchmark_home = Path(os.environ.get("BENCHMARK_HOME", Path.home() / ".benchmark")).resolve()

    if not workloads_dir.exists():
        logger.error(f"Workloads directory not found at: {workloads_dir}")
        sys.exit(1)

    # 1. Initialize ConfigLoader
    loader = ConfigLoader(workspace_dir=str(workspace))
    loader.workloads_dir = workloads_dir

    # 2. Locate and load pipeline JSON
    pipeline_name = args.pipeline
    if pipeline_name.endswith(".json"):
        pipeline_file = Path(pipeline_name)
        if not pipeline_file.is_absolute():
            pipeline_file = workspace / pipeline_file
    else:
        pipeline_file = workspace / "pipelines" / f"{pipeline_name}.json"

    if not pipeline_file.exists():
        logger.error(f"Pipeline definition not found: {pipeline_file}")
        sys.exit(1)

    with open(pipeline_file) as f:
        pipeline_data = json.load(f)

    pipeline_params = pipeline_data.get("params", {}).copy()

    use_ssl = str(args.use_ssl).lower() == "true"
    client_opts = (
        f"timeout:{args.timeout},"
        f"use_ssl:{str(use_ssl).lower()},"
        f"verify_certs:false,"
        f"basic_auth_user:{args.auth_user},"
        f"basic_auth_password:{args.auth_pass}"
    )

    steps = pipeline_data.get("steps", [])
    logger.info("=" * 60)
    logger.info(f" Pipeline:     {pipeline_file.name}")
    logger.info(f" Engine:       {args.engine}")
    logger.info(f" Target Host:  {args.target_host}")
    logger.info(f" Total Steps:  {len(steps)}")
    logger.info(f" Results Dir:  {results_dir}")
    logger.info("=" * 60)

    for idx, step in enumerate(steps):
        dataset_name = step["dataset"]
        scenario = step["scenario"]
        step_params = step.get("params", {})

        combined_runtime_params = {**pipeline_params, **step_params}
        base_params = loader.get_workload_params(dataset_name, args.engine)

        # Resolve templates and filter unused parameters
        final_params = loader.resolve_workload_params(
            dataset_name,
            base_params,
            combined_runtime_params,
            scenario=scenario
        )

        workload_path = loader.get_workload_path(dataset_name)
        step_results_dir = results_dir / f"{idx:02d}_{scenario}"
        step_results_dir.mkdir(parents=True, exist_ok=True)

        params_file = step_results_dir / "workload-params.json"
        with open(params_file, "w") as pf:
            json.dump(final_params, pf, indent=2)

        cmd = [
            "opensearch-benchmark", "run",
            "--workload-path", str(workload_path),
            "--test-procedure", scenario,
            "--target-hosts", args.target_host,
            "--client-options", client_opts,
            "--workload-params", str(params_file),
            "--kill-running-processes"
        ]

        logger.info(f"\n▶ [{idx + 1}/{len(steps)}] Step: {scenario} ({dataset_name})")
        logger.info(f"Workload path: {workload_path}")
        logger.info(f"Params: {json.dumps(final_params)}")

        # Execute OSB process and stream output live
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        stdout_lines = []
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            stdout_lines.append(line)

        process.wait()
        full_stdout = "".join(stdout_lines)

        # Save stdout log
        (step_results_dir / "stdout.log").write_text(full_stdout, encoding="utf-8")

        # Extract test run UUID and copy artifacts
        uuid_match = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", full_stdout)
        if uuid_match:
            run_id = uuid_match.group(0)
            logger.info(f"Captured Test Run ID: {run_id}")

            # Possible OSB test_run.json locations
            candidate_paths = [
                benchmark_home / ".osb" / "benchmarks" / "test-runs" / run_id / "test_run.json",
                benchmark_home / "benchmarks" / "test-runs" / run_id / "test_run.json",
                Path.home() / ".benchmark" / ".osb" / "benchmarks" / "test-runs" / run_id / "test_run.json",
                Path.home() / ".benchmark" / "benchmarks" / "test-runs" / run_id / "test_run.json",
            ]

            copied = False
            for test_run_file in candidate_paths:
                if test_run_file.exists():
                    shutil.copy(test_run_file, step_results_dir / "test_run.json")
                    logger.info(f"✓ Copied test_run.json to {step_results_dir}")
                    copied = True
                    break

            if not copied:
                logger.warning(f"Could not find test_run.json for ID {run_id} under {benchmark_home}")

        # Copy OSB benchmark log into step results directory
        benchmark_log_candidates = [
            benchmark_home / "logs" / "benchmark.log",
            Path.home() / ".benchmark" / "logs" / "benchmark.log",
        ]
        for log_path in benchmark_log_candidates:
            if log_path.exists():
                shutil.copy(log_path, step_results_dir / "benchmark.log")
                logger.info(f"✓ Copied benchmark.log to {step_results_dir}")
                break
        else:
            logger.warning("Could not find benchmark.log to copy")

        if process.returncode != 0:
            logger.error(f"✖ Step {scenario} failed with exit code {process.returncode}")
            sys.exit(process.returncode)

    logger.info("\n" + "=" * 60)
    logger.info("✓ Pipeline completed successfully!")
    logger.info(f"Results archived at: {results_dir}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
