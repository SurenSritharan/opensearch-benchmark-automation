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
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

# Add cloud-service directory to sys.path to import ConfigLoader
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "cloud-service"))

from config_loader import ConfigLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("local-runner")


def _get(host: str, use_ssl: bool, user: str, password: str, path: str) -> Optional[Dict]:
    """GET a JSON endpoint from OpenSearch using basic auth."""
    if not _REQUESTS_AVAILABLE:
        return None
    proto = "https" if use_ssl else "http"
    try:
        resp = _requests.get(
            f"{proto}://{host}{path}",
            auth=(user, password), verify=False, timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Could not fetch {path}: {e}")
        return None


def _fetch_node_stats(host: str, use_ssl: bool, user: str, password: str) -> Optional[Dict]:
    """Snapshot /_nodes/stats."""
    return _get(host, use_ssl, user, password,
                "/_nodes/stats/jvm,os,process,fs,thread_pool,indices")


def _fetch_index_stats(host: str, use_ssl: bool, user: str, password: str,
                       index: str = "_all") -> Optional[Dict]:
    """Snapshot /<index>/_stats for segments, shards, and index-level counters."""
    return _get(host, use_ssl, user, password, f"/{index}/_stats")


def _diff_node_stats(before: Dict, after: Dict) -> Dict:
    """Diff two _nodes/stats snapshots, returning per-node deltas for counter fields."""
    result = {}
    for node_id, after_node in after.get("nodes", {}).items():
        before_node = before.get("nodes", {}).get(node_id, {})
        name = after_node.get("name", node_id)

        def delta(path):
            a, b = after_node, before_node
            for key in path:
                a = a.get(key, {}) if isinstance(a, dict) else {}
                b = b.get(key, {}) if isinstance(b, dict) else {}
            return (a - b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a

        result[name] = {
            "jvm": {
                "heap_used_percent":      after_node.get("jvm", {}).get("mem", {}).get("heap_used_percent"),
                "heap_used_mb":           round(after_node.get("jvm", {}).get("mem", {}).get("heap_used_in_bytes", 0) / 1048576, 1),
                "gc_young_count_delta":   delta(["jvm", "gc", "collectors", "young", "collection_count"]),
                "gc_young_time_ms_delta": delta(["jvm", "gc", "collectors", "young", "collection_time_in_millis"]),
                "gc_old_count_delta":     delta(["jvm", "gc", "collectors", "old", "collection_count"]),
                "gc_old_time_ms_delta":   delta(["jvm", "gc", "collectors", "old", "collection_time_in_millis"]),
            },
            "os": {
                "cpu_percent":      after_node.get("os", {}).get("cpu", {}).get("percent"),
                "load_1m":          after_node.get("os", {}).get("cpu", {}).get("load_average", {}).get("1m"),
                "mem_used_percent": after_node.get("os", {}).get("mem", {}).get("used_percent"),
            },
            "indices": {
                "search_query_count_delta":   delta(["indices", "search", "query_total"]),
                "search_query_time_ms_delta": delta(["indices", "search", "query_time_in_millis"]),
                "indexing_count_delta":       delta(["indices", "indexing", "index_total"]),
                "indexing_time_ms_delta":     delta(["indices", "indexing", "index_time_in_millis"]),
                "segments_count":             after_node.get("indices", {}).get("segments", {}).get("count"),
                "segments_memory_mb":         round(after_node.get("indices", {}).get("segments", {}).get("memory_in_bytes", 0) / 1048576, 1),
                "merge_count_delta":          delta(["indices", "merges", "total"]),
                "merge_time_ms_delta":        delta(["indices", "merges", "total_time_in_millis"]),
                "refresh_count_delta":        delta(["indices", "refresh", "total"]),
                "refresh_time_ms_delta":      delta(["indices", "refresh", "total_time_in_millis"]),
                "flush_count_delta":          delta(["indices", "flush", "total"]),
                "flush_time_ms_delta":        delta(["indices", "flush", "total_time_in_millis"]),
            },
            "thread_pool": {
                "search_queue":    after_node.get("thread_pool", {}).get("search", {}).get("queue"),
                "search_rejected": delta(["thread_pool", "search", "rejected"]),
                "write_queue":     after_node.get("thread_pool", {}).get("write", {}).get("queue"),
                "write_rejected":  delta(["thread_pool", "write", "rejected"]),
            },
        }
    return result


class NodeStatsPoller:
    """Polls /_nodes/stats on a background thread and produces k8s_metrics.json.

    Produces the same summary.nodes schema as K8sMetricsCollector so the
    dashboard can read local run results without code changes.
    CPU is mapped from os.cpu.percent, memory from os.mem.used_in_bytes.
    """

    def __init__(self, host: str, use_ssl: bool, user: str, password: str,
                 interval: int = 10):
        self._host = host
        self._use_ssl = use_ssl
        self._user = user
        self._password = password
        self._interval = interval
        self._stop = threading.Event()
        self._cpu_samples: Dict[str, List[float]] = defaultdict(list)
        self._mem_samples: Dict[str, List[float]] = defaultdict(list)
        self._thread: Optional[threading.Thread] = None
        self._total_samples = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            snap = _fetch_node_stats(self._host, self._use_ssl, self._user, self._password)
            if not snap:
                continue
            for node_data in snap.get("nodes", {}).values():
                name = node_data.get("name", "unknown")
                cpu = node_data.get("os", {}).get("cpu", {}).get("percent")
                mem_bytes = node_data.get("os", {}).get("mem", {}).get("used_in_bytes")
                mem_total = node_data.get("os", {}).get("mem", {}).get("total_in_bytes")
                if cpu is not None:
                    self._cpu_samples[name].append(float(cpu))
                if mem_bytes is not None and mem_total:
                    self._mem_samples[name].append(mem_bytes / 1024 / 1024)  # MiB
            self._total_samples += 1

    def save(self, scenario: str, start_time: str, end_time: str,
             results_dir: Path) -> None:
        """Write k8s_metrics.json in the same schema as K8sMetricsCollector."""
        if not self._total_samples:
            return
        nodes: Dict[str, Dict] = {}
        all_names = set(self._cpu_samples) | set(self._mem_samples)
        for name in all_names:
            cpu_list = self._cpu_samples.get(name, [])
            mem_list = self._mem_samples.get(name, [])
            nodes[name] = {
                "node_pool": "local",
                "cpu_avg": round(sum(cpu_list) / len(cpu_list), 3) if cpu_list else 0.0,
                "cpu_max": round(max(cpu_list), 3) if cpu_list else 0.0,
                "cpu_min": round(min(cpu_list), 3) if cpu_list else 0.0,
                "memory_avg": round(sum(mem_list) / len(mem_list), 1) if mem_list else 0.0,
                "memory_max": round(max(mem_list), 1) if mem_list else 0.0,
                "memory_min": round(min(mem_list), 1) if mem_list else 0.0,
            }
        payload = {
            "scenario":        scenario,
            "namespace":       "local",
            "start_time":      start_time,
            "end_time":        end_time,
            "duration_seconds": None,
            "summary": {
                "nodes":         nodes,
                "pods":          {},
                "total_samples": self._total_samples,
                "gc_timeline":   [],
            },
        }
        out = results_dir / "k8s_metrics.json"
        out.write_text(json.dumps(payload, indent=2))
        logger.info(f"✓ Saved k8s_metrics.json ({self._total_samples} samples) to {results_dir}")


def _save_index_snapshot(host: str, use_ssl: bool, user: str, password: str,
                         index_name: str, results_dir: Path) -> None:
    """Fetch mapping, settings and stats for index_name and save to index_snapshot.json.

    Mirrors app.py _save_index_snapshot — same file name and structure so results
    are compatible with the dashboard and analysis tooling used for cloud runs.
    """
    try:
        proto = "https" if use_ssl else "http"
        base_url = f"{proto}://{host}/{index_name}"
        snapshot = {"index": index_name}
        for key, path in [("mapping", "/_mapping"), ("settings", "/_settings"), ("stats", "/_stats")]:
            r = _requests.get(base_url + path, auth=(user, password), verify=False, timeout=15)
            snapshot[key] = r.json() if r.ok else {"error": f"HTTP {r.status_code}"}
        out = results_dir / "index_snapshot.json"
        out.write_text(json.dumps(snapshot, indent=2))
        logger.info(f"✓ Saved index_snapshot.json for {index_name} to {results_dir}")
    except Exception as e:
        logger.warning(f"index snapshot failed for {index_name}: {e}")


def _find_benchmark_artifact(roots: List[Path], subpaths: List[str]) -> Optional[Path]:
    """Find the first existing file among root candidate directories and subpaths."""
    for root in roots:
        for subpath in subpaths:
            candidate = root / subpath
            if candidate.exists():
                return candidate
    return None


def _save_server_stats(host: str, use_ssl: bool, user: str, password: str,
                       start_time: str, end_time: str,
                       stats_before: Dict, results_dir: Path) -> None:
    """Diff node stats before/after and write server_stats.json.

    Mirrors benchmark_runner.py _save_server_stats — same file name and structure.
    """
    try:
        stats_after = _fetch_node_stats(host, use_ssl, user, password)
        if not stats_after:
            return
        server_stats = {
            "captured_at_start": start_time,
            "captured_at_end":   end_time,
            "node_deltas":       _diff_node_stats(stats_before, stats_after),
            "snapshots":         {"before": stats_before, "after": stats_after},
        }
        out = results_dir / "server_stats.json"
        out.write_text(json.dumps(server_stats, indent=2))
        logger.info(f"✓ Saved server_stats.json to {results_dir}")
    except Exception as e:
        logger.warning(f"Failed to save server_stats: {e}")


def _save_rest_telemetry(host: str, use_ssl: bool, user: str, password: str,
                         results_dir: Path) -> None:
    """Capture cluster REST telemetry snapshots to server-logs/telemetry/.

    Mirrors the telemetry capture in cloud-service/scripts/run-pipeline.sh so
    local runs have identical REST telemetry artifacts compatible with the dashboard.
    """
    if not _REQUESTS_AVAILABLE:
        return

    tel_dir = results_dir / "server-logs" / "telemetry"
    tel_dir.mkdir(parents=True, exist_ok=True)

    endpoints = [
        ("/_cluster/health?pretty", "cluster-health.json"),
        ("/_cluster/stats?pretty", "cluster-stats.json"),
        ("/_cluster/settings?include_defaults=true&flat_settings=true&pretty", "cluster-settings.json"),
        ("/_nodes/stats?pretty", "nodes-stats.json"),
        ("/_cat/nodes?v&h=name,heap.percent,heap.current,heap.max,ram.percent,cpu,load_1m,load_5m", "nodes.txt"),
        ("/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,largest,completed", "thread-pools.txt"),
        ("/_cat/tasks?v&detailed", "tasks.txt"),
        ("/_cat/segments?v", "segments.txt"),
    ]

    proto = "https" if use_ssl else "http"
    for endpoint, filename in endpoints:
        try:
            resp = _requests.get(
                f"{proto}://{host}{endpoint}",
                auth=(user, password),
                verify=False,
                timeout=15,
            )
            out_file = tel_dir / filename
            if resp.ok:
                out_file.write_text(resp.text, encoding="utf-8")
            else:
                logger.warning(f"Telemetry GET {endpoint} returned status {resp.status_code}")
        except Exception as e:
            logger.warning(f"Telemetry failed for {endpoint}: {e}")

    logger.info(f"✓ Saved REST telemetry to {tel_dir}")


def _download_dataset_files(loader: ConfigLoader, pipeline_data: Dict) -> None:
    """Download HTTP/S3-backed data_files (base vectors, queries) for all corpus-requiring steps.

    Mirrors the download logic in benchmark_runner.py so the local runner does not
    require files to be pre-placed at /datasets/* by hand.
    Skips datasets that have no ``data_files`` entry in datasets.yaml.
    """
    steps = pipeline_data.get("steps", [])
    pipeline_params = pipeline_data.get("params", {})

    CORPUS_REQUIRING_SCENARIOS = {"bulk-ingest-data", "bulk-ingest-and-search", "vector-search"}

    # Collect unique (dataset, params) pairs so we download each corpus size once.
    seen: set = set()
    for step in steps:
        scenario = step.get("scenario")
        if scenario not in CORPUS_REQUIRING_SCENARIOS:
            continue
        dataset_name = step.get("dataset")
        if not dataset_name:
            continue

        step_params = step.get("params", {})
        # Sweeps may each target a different corpus_size — handle them individually.
        sweeps = step_params.get("parameter_sweeps") or []
        candidates = [step_params] if not sweeps else [
            {**step_params, **sweep.get("params", {})} for sweep in sweeps
        ]

        for candidate in candidates:
            merged = {**pipeline_params, **candidate}
            corpus_size = merged.get("corpus_size", "1m")
            key = (dataset_name, str(corpus_size))
            if key in seen:
                continue
            seen.add(key)

            dataset_cfg = loader.get_dataset_config(dataset_name)
            if not dataset_cfg or not dataset_cfg.get("data_files"):
                continue

            logger.info(f"📦 Ensuring dataset files for '{dataset_name}' corpus_size={corpus_size}...")
            ok = loader.download_dataset_files(dataset_name, merged)
            if not ok:
                logger.error(
                    f"Failed to download dataset files for '{dataset_name}' (corpus_size={corpus_size}). "
                    "Continuing — the benchmark step will fail if the file is missing."
                )


def _seed_gcs_cache_files(loader: ConfigLoader, pipeline_data: Dict, benchmark_home: Path) -> None:
    """Pre-seed dataset cache files listed in datasets.yaml (gcs_cache_files).

    Only seeds when scenarios that actually require the corpus file
    (e.g. bulk-ingest-data, bulk-ingest-and-search) are present in the pipeline.
    Resolves target paths relative to BENCHMARK_HOME and uses gcloud or gsutil to download if missing.
    """
    steps = pipeline_data.get("steps", [])
    pipeline_params = pipeline_data.get("params", {})

    # Scenarios that actually consume the underlying HDF5 corpus file (documents, test queries, and ground truth)
    CORPUS_REQUIRING_SCENARIOS = {"bulk-ingest-data", "bulk-ingest-and-search", "vector-search"}

    # Map dataset -> required corpus sizes specifically for corpus-requiring scenarios
    dataset_corpus_sizes: Dict[str, set] = defaultdict(set)
    global_corpus_size = str(pipeline_params["corpus_size"]) if pipeline_params.get("corpus_size") else None

    for step in steps:
        scenario = step.get("scenario")
        if scenario not in CORPUS_REQUIRING_SCENARIOS:
            continue

        dataset = step.get("dataset")
        if not dataset:
            continue

        step_params = step.get("params", {})
        step_size = step_params.get("corpus_size")
        if step_size:
            dataset_corpus_sizes[dataset].add(str(step_size))
        elif global_corpus_size:
            dataset_corpus_sizes[dataset].add(global_corpus_size)

        for sweep in step_params.get("parameter_sweeps", []) or []:
            sweep_size = sweep.get("params", {}).get("corpus_size")
            if sweep_size:
                dataset_corpus_sizes[dataset].add(str(sweep_size))

    if not dataset_corpus_sizes:
        return

    files_to_seed = []
    for dataset_name, corpus_sizes in dataset_corpus_sizes.items():
        dataset_cfg = loader.get_dataset_config(dataset_name)
        gcs_cache_files = dataset_cfg.get("gcs_cache_files", [])
        for entry in gcs_cache_files:
            c_size = entry.get("corpus_size")
            # Only seed if this specific GCS file's corpus size (e.g. 5m, 8m) was requested
            if c_size and c_size in corpus_sizes:
                files_to_seed.append(entry)

    if not files_to_seed:
        return

    # Check for available GCS CLI tool (gcloud or gsutil)
    gcs_tool = shutil.which("gcloud") or shutil.which("gsutil")

    for entry in files_to_seed:
        gcs_path = entry.get("gcs_path")
        orig_target_path = entry.get("target_path", "")
        if not gcs_path:
            continue

        file_name = gcs_path.split("/")[-1]

        # Remap /datasets/opensearch-benchmark prefix to local benchmark_home
        if orig_target_path.startswith("/datasets/opensearch-benchmark/"):
            rel_path = orig_target_path[len("/datasets/opensearch-benchmark/"):]
            local_target = benchmark_home / rel_path
        else:
            corpus_size = entry.get("corpus_size", "")
            corpus_folder = f"cohere-{corpus_size}" if corpus_size else ""
            local_target = benchmark_home / ".osb" / "benchmarks" / "data" / corpus_folder / file_name

        if local_target.exists() and local_target.stat().st_size > 0:
            logger.info(f"✓ GCS cache file already present: {local_target} ({local_target.stat().st_size:,} bytes)")
            continue

        if not gcs_tool:
            logger.warning(
                f"⚠ Target file missing: {local_target}, but neither 'gcloud' nor 'gsutil' was found in PATH. "
                f"Please install Google Cloud SDK or manually place {file_name} from {gcs_path} at {local_target}"
            )
            continue

        local_target.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"📥 Seeding {file_name} from GCS: {gcs_path} -> {local_target}")

        if "gcloud" in gcs_tool:
            cmd = ["gcloud", "storage", "cp", gcs_path, str(local_target)]
        else:
            cmd = ["gsutil", "-q", "cp", gcs_path, str(local_target)]

        try:
            subprocess.run(cmd, check=True)
            if local_target.exists():
                size_gb = local_target.stat().st_size / (1024 ** 3)
                logger.info(f"✓ Successfully seeded {file_name} ({size_gb:.2f} GB)")
            else:
                logger.error(f"Failed to seed {file_name}: target file not created after copy")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to copy {gcs_path} to {local_target}: {e}")
        except Exception as e:
            logger.error(f"Error seeding {file_name}: {e}")


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

    # 3. Download HTTP/S3-backed dataset files (base vectors, queries, ground truth)
    _download_dataset_files(loader, pipeline_data)

    # 4. Pre-seed any GCS dataset cache files (e.g. Cohere 5M/8M HDF5)
    _seed_gcs_cache_files(loader, pipeline_data, benchmark_home)

    for idx, step in enumerate(steps):
        dataset_name = step["dataset"]
        scenario     = step["scenario"]
        label        = step.get("label", scenario)
        step_params  = step.get("params", {}).copy()

        # Extract sweeps before building base params — mirrors benchmark_runner._get_run_contexts
        raw_sweeps = step_params.pop("parameter_sweeps", None) or []
        has_sweeps = bool(raw_sweeps)

        # Mirror app.py param layering:
        # 1. common_params  2. engine params  3. procedure base  4. pipeline step params
        dataset_cfg = loader.get_dataset_config(dataset_name)
        base_params = dataset_cfg.get("common_params", {}).copy()
        base_params.update(loader.get_workload_params(dataset_name, args.engine))

        procedures = loader.get_test_procedures(dataset_name)
        matched_proc = next(
            (p for p in procedures if isinstance(p, dict) and p.get("name") == scenario),
            None
        )
        if matched_proc:
            base_params.update(matched_proc.get("params", {}))
            proc_engine_params = matched_proc.get("engine_params", {}).get(args.engine, {})
            if proc_engine_params:
                base_params.update(proc_engine_params)

        combined_runtime_params = {**pipeline_params, **step_params}
        workload_path = loader.get_workload_path(dataset_name)

        # Expand into one run per sweep (or a single run when no sweeps defined)
        sweep_list = raw_sweeps if has_sweeps else [{}]
        for sweep_idx, sweep in enumerate(sweep_list, 1):
            sweep_params = sweep.get("params", {}) if has_sweeps else {}
            merged_runtime = {**combined_runtime_params, **sweep_params}

            final_params = loader.resolve_workload_params(
                dataset_name,
                base_params,
                merged_runtime,
                scenario=scenario
            )

            step_label = f"{idx:02d}_{label}"
            step_results_dir = (
                results_dir / step_label / f"sweep-{sweep_idx}"
                if has_sweeps else
                results_dir / step_label
            )
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

            sweep_suffix = f" sweep {sweep_idx}/{len(sweep_list)}" if has_sweeps else ""
            logger.info(f"\n▶ [{idx + 1}/{len(steps)}]{sweep_suffix} Step: {label} ({dataset_name})")
            logger.info(f"Workload path: {workload_path}")
            logger.info(f"Params: {json.dumps(final_params)}")

            # Truncate benchmark.log before each step so the copy captures only this step's output
            benchmark_roots = list(dict.fromkeys([benchmark_home, Path.home() / ".benchmark", REPO_ROOT / ".benchmark"]))
            log_subpaths = [".osb/logs/benchmark.log", "logs/benchmark.log"]

            active_log = _find_benchmark_artifact(benchmark_roots, log_subpaths)
            if active_log:
                active_log.write_text("", encoding="utf-8")
                logger.info(f"Cleared benchmark.log at {active_log}")

            # Snapshot node stats before the step + start background poller for k8s_metrics
            stats_before = _fetch_node_stats(args.target_host, use_ssl, args.auth_user, args.auth_pass)
            start_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            poller = NodeStatsPoller(args.target_host, use_ssl, args.auth_user, args.auth_pass)
            if _REQUESTS_AVAILABLE:
                poller.start()

            # Execute OSB process and stream output live
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            stdout_lines = []
            if process.stdout:
                for line in process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    stdout_lines.append(line)

            process.wait()
            end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
            poller.stop()
            full_stdout = "".join(stdout_lines)

            # server_stats.json — node deltas + full before/after snapshots (mirrors cloud)
            if stats_before:
                _save_server_stats(
                    args.target_host, use_ssl, args.auth_user, args.auth_pass,
                    start_time, end_time, stats_before, step_results_dir,
                )

            # k8s_metrics.json — CPU/memory time-series in same schema as K8sMetricsCollector
            poller.save(scenario, start_time, end_time, step_results_dir)

            # index_snapshot.json — mapping, settings, stats for the benchmark index (mirrors cloud)
            index_name = final_params.get("index", final_params.get("target_index_name", ""))
            if index_name and _REQUESTS_AVAILABLE:
                _save_index_snapshot(
                    args.target_host, use_ssl, args.auth_user, args.auth_pass,
                    index_name, step_results_dir,
                )

            # REST telemetry dump (cluster-health, cluster-stats, nodes, thread-pools, segments, tasks)
            if _REQUESTS_AVAILABLE:
                _save_rest_telemetry(
                    args.target_host, use_ssl, args.auth_user, args.auth_pass,
                    step_results_dir,
                )

            # Save stdout log
            (step_results_dir / "stdout.log").write_text(full_stdout, encoding="utf-8")

            # Extract test run UUID and copy artifacts
            uuid_match = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", full_stdout)
            if uuid_match:
                run_id = uuid_match.group(0)
                logger.info(f"Captured Test Run ID: {run_id}")

                test_run_subpaths = [
                    f".osb/benchmarks/test-runs/{run_id}/test_run.json",
                    f"benchmarks/test-runs/{run_id}/test_run.json",
                ]
                test_run_file = _find_benchmark_artifact(benchmark_roots, test_run_subpaths)
                if test_run_file:
                    shutil.copy(test_run_file, step_results_dir / "test_run.json")
                    logger.info(f"✓ Copied test_run.json to {step_results_dir}")
                else:
                    logger.warning(f"Could not find test_run.json for ID {run_id} under {benchmark_home}")

            # Copy OSB benchmark log into step results directory
            log_to_copy = _find_benchmark_artifact(benchmark_roots, log_subpaths)
            if log_to_copy:
                shutil.copy(log_to_copy, step_results_dir / "benchmark.log")
                logger.info(f"✓ Copied benchmark.log to {step_results_dir}")
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
