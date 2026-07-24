#!/usr/bin/env python3
"""Cloud-native benchmark runner - executes opensearch-benchmark directly"""
import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from requests.auth import HTTPBasicAuth
import requests
from config_loader import ConfigLoader
from k8s_metrics_collector import K8sMetricsCollector


@dataclass
class RunContext:
    """Everything needed to execute one benchmark sweep — fully resolved upfront."""
    target_host:    str
    workload_path:  str
    dataset_config: Dict[str, Any]
    params:         Dict[str, Any]   # merged base → procedure → sweep → runtime
    sweep_params:   Dict[str, Any]   # raw sweep-level params (for reporting only)
    results_dir:    Path             # pre-built results directory for this sweep


def _kill_proc_group(proc: subprocess.Popen) -> None:
    """
    Immediately obliterates all benchmark engine footprints across the container 
    using an uncatchable SIGKILL broad sweep.
    """

    # Broad patterns to catch the engine wrapper and the underlying actor layers
    try:
        for i in range(15):
            subprocess.run(["pkill", "-9", "-f", "opensearch-benchmark"])
            time.sleep(1)
    except Exception as e:
        pass

    # Fallback: Clean up the local wrapper handle if it's still tracked by the thread
    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1)
    except Exception:
        pass
    

def _fetch_node_stats(target_host: str) -> Optional[Dict]:
    """Snapshot _nodes/stats from the OpenSearch cluster via REST API."""
    try:
        url = f"https://{target_host}/_nodes/stats/jvm,os,process,fs,thread_pool,indices"
        resp = requests.get(url, auth=HTTPBasicAuth('admin', 'admin'),
                            verify=False, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logging.getLogger(__name__).warning(f"Could not fetch _nodes/stats: {e}")
        return None


def _diff_node_stats(before: Dict, after: Dict) -> Dict:
    """Diff two _nodes/stats snapshots, returning deltas for counter fields."""
    result = {}
    for node_id, after_node in after.get('nodes', {}).items():
        before_node = before.get('nodes', {}).get(node_id, {})
        name = after_node.get('name', node_id)

        def delta(path: list):
            """Walk a dotted path and return after - before for numeric values."""
            a, b = after_node, before_node
            for key in path:
                a = a.get(key, {}) if isinstance(a, dict) else {}
                b = b.get(key, {}) if isinstance(b, dict) else {}
            return (a - b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a

        result[name] = {
            'jvm': {
                'heap_used_percent':      after_node.get('jvm', {}).get('mem', {}).get('heap_used_percent'),
                'heap_used_mb':           round(after_node.get('jvm', {}).get('mem', {}).get('heap_used_in_bytes', 0) / 1048576, 1),
                'gc_young_count_delta':   delta(['jvm', 'gc', 'collectors', 'young', 'collection_count']),
                'gc_young_time_ms_delta': delta(['jvm', 'gc', 'collectors', 'young', 'collection_time_in_millis']),
                'gc_old_count_delta':     delta(['jvm', 'gc', 'collectors', 'old', 'collection_count']),
                'gc_old_time_ms_delta':   delta(['jvm', 'gc', 'collectors', 'old', 'collection_time_in_millis']),
            },
            'os': {
                'cpu_percent':     after_node.get('os', {}).get('cpu', {}).get('percent'),
                'load_1m':         after_node.get('os', {}).get('cpu', {}).get('load_average', {}).get('1m'),
                'mem_used_percent': after_node.get('os', {}).get('mem', {}).get('used_percent'),
            },
            'indices': {
                'search_query_count_delta':   delta(['indices', 'search', 'query_total']),
                'search_query_time_ms_delta': delta(['indices', 'search', 'query_time_in_millis']),
                'search_fetch_count_delta':   delta(['indices', 'search', 'fetch_total']),
                'indexing_count_delta':       delta(['indices', 'indexing', 'index_total']),
                'indexing_time_ms_delta':     delta(['indices', 'indexing', 'index_time_in_millis']),
            },
            'thread_pool': {
                'search_queue':    after_node.get('thread_pool', {}).get('search', {}).get('queue'),
                'search_rejected': delta(['thread_pool', 'search', 'rejected']),
                'write_queue':     after_node.get('thread_pool', {}).get('write', {}).get('queue'),
                'write_rejected':  delta(['thread_pool', 'write', 'rejected']),
            },
        }
    return result

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Executes opensearch-benchmark commands without kubectl dependencies"""
    
    def __init__(self, config_loader: ConfigLoader, results_dir: str = '/results'):
        self.config = config_loader
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_collector = None
        self.metrics_thread = None
        # Maps job_id -> (Popen process, cancel_event) for active benchmarks
        self._active: Dict[str, tuple] = {}
        self._active_lock = threading.Lock()

    # ── 1. Setup ──────────────────────────────────────────────────────────────

    def _get_run_contexts(
        self,
        job_id: str,
        dataset: str,
        engine: str,
        scenario: str,
        workload_params: Optional[Dict],
    ) -> List[RunContext]:
        """Sync workloads repo and return one fully-resolved RunContext per sweep.

        All param merging and directory creation happens here so the benchmark
        loop just iterates sweeps and executes — no resolution work inline.
        """
        self.config._git_pull_repo(self.config.workloads_dir, 'opensearch-benchmark-workloads')

        dataset_config = self.config.get_dataset_config(dataset)
        target_host    = self.config.get_target_host(engine)
        workload_path  = self.config.get_workload_path(dataset)

        # Base params: common → engine-specific
        base_params = {}
        common_params = dataset_config.get('common_params', {})
        if common_params:
            base_params.update(common_params)
            logger.info(f"Loaded common params: {list(common_params.keys())}")
        engine_params_config = dataset_config.get('engine_params', {})
        if engine_params_config and engine in engine_params_config:
            base_params.update(engine_params_config[engine])
            logger.info(f"Loaded engine params for {engine}: {list(engine_params_config[engine].keys())}")

        # Procedure config: scenario-level params + engine-specific overrides
        runtime_sweeps   = workload_params.pop('parameter_sweeps', None) if workload_params else None
        procedure_config = next(
            (p for p in self.config.get_test_procedures(dataset)
             if isinstance(p, dict) and p.get('name') == scenario),
            None
        )
        procedure_base_params = {}
        if procedure_config:
            procedure_base_params = procedure_config.get('params', {}).copy()
            if procedure_base_params:
                logger.info(f"Loaded procedure base params: {list(procedure_base_params.keys())}")
            proc_engine_params = procedure_config.get('engine_params', {})
            if proc_engine_params and engine in proc_engine_params:
                procedure_base_params.update(proc_engine_params[engine])
                logger.info(f"Loaded procedure engine params for {engine}: {list(proc_engine_params[engine].keys())}")

        # Raw sweeps from config or runtime request
        raw_sweeps = runtime_sweeps if runtime_sweeps is not None else (
            procedure_config.get('parameter_sweeps', []) if procedure_config else []
        )
        has_sweeps = bool(raw_sweeps)
        if has_sweeps:
            source = "request" if runtime_sweeps is not None else "config"
            logger.info(f"Found {len(raw_sweeps)} parameter sweeps for '{scenario}' (source: {source})")
        else:
            raw_sweeps = [{}]

        if workload_params:
            logger.info(f"Runtime params: {list(workload_params.keys())}")

        # Build one RunContext per sweep — all params resolved, results dir created
        sweeps = []
        for idx, raw_sweep in enumerate(raw_sweeps, 1):
            sweep_params = raw_sweep.get('params', {})
            if sweep_params:
                logger.info(f"Sweep {idx} params: {list(sweep_params.keys())}")
            merged       = {**base_params, **procedure_base_params, **sweep_params}
            final_params = self.config.resolve_workload_params(dataset, merged, workload_params)

            if '/' in job_id:
                base = self.results_dir / job_id
                results_dir = base / f"sweep-{idx}" if has_sweeps else base
            else:
                base = self.results_dir / job_id / scenario
                results_dir = base / f"sweep-{idx}" if has_sweeps else base
            results_dir.mkdir(parents=True, exist_ok=True)

            sweeps.append(RunContext(
                target_host    = target_host,
                workload_path  = workload_path,
                dataset_config = dataset_config,
                params         = final_params,
                sweep_params   = sweep_params,
                results_dir    = results_dir,
            ))

        return sweeps

    # ── 2. Pre-flight ─────────────────────────────────────────────────────────

    def _check_cluster_health(self, target_host: str, retries: int = 6, retry_delay: int = 10) -> bool:
        """Check if OpenSearch cluster is healthy and ready.

        Retries up to `retries` times with `retry_delay` seconds between
        attempts so a transiently red/initializing cluster (e.g. just after
        scale-up) doesn't immediately fail all scenarios.
        """
        url = f"https://{target_host}/_cluster/health"
        for attempt in range(1, retries + 1):
            try:
                response = requests.get(
                    url,
                    auth=HTTPBasicAuth('admin', 'admin'),
                    verify=False,
                    timeout=10
                )
                if response.status_code == 200:
                    health = response.json()
                    status = health.get('status')
                    logger.info(f"Cluster health (attempt {attempt}/{retries}): {status}, nodes: {health.get('number_of_nodes')}")
                    if status in ['green', 'yellow']:
                        return True
                    logger.warning(f"Cluster status is '{status}' — waiting {retry_delay}s before retry")
                else:
                    logger.warning(f"Cluster health check HTTP {response.status_code} (attempt {attempt}/{retries}) — waiting {retry_delay}s")
            except Exception as e:
                logger.warning(f"Cluster health check error (attempt {attempt}/{retries}): {e} — waiting {retry_delay}s")
            if attempt < retries:
                time.sleep(retry_delay)
        logger.error(f"Cluster {target_host} not healthy after {retries} attempts")
        return False

    # ── 3. Per-sweep start ────────────────────────────────────────────────────

    def _start_metrics_collection(
        self,
        engine: str,
        target_host: str,
        sweep_results_dir: Path,
        dataset: str,
        scenario: str,
        sweep_idx: int,
    ) -> None:
        """Initialise and start the K8s metrics collector in a background thread."""
        namespace = f"os-{engine}"
        logger.info(f"📊 Initializing metrics collection for namespace: {namespace}")
        try:
            self.metrics_collector = K8sMetricsCollector(
                namespace=namespace,
                results_dir=sweep_results_dir,
                enabled=True,
                opensearch_host=target_host,
            )
            logger.info("✓ Metrics collector initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize metrics collector: {e}")
            self.metrics_collector = None
            return

        scenario_name = f"{dataset}-{scenario}-sweep{sweep_idx}"
        metrics_collector = self.metrics_collector

        def collect_metrics():
            try:
                metrics_collector.start_collection(scenario_name=scenario_name, interval=10, duration=None)
                metrics_collector.save_metrics(scenario_name)
            except Exception as e:
                logger.error(f"Error in metrics collection thread: {e}")

        self.metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
        self.metrics_thread.start()
        logger.info("📊 Metrics collection started in background.")

    def _clear_benchmark_logs(self):
        """Clear benchmark log files and reset logging.json before each run."""
        BENCHMARK_HOME = "/datasets/opensearch-benchmark"
        osb_dir = Path(f"{BENCHMARK_HOME}/.osb")
        log_dir = osb_dir / 'logs'

        # Truncate log files so previous run output doesn't bleed into the next run
        for log_name in ("benchmark.log", "http-trace.log"):
            log_path = log_dir / log_name
            try:
                with open(log_path, 'w') as f:
                    f.truncate(0)
                logger.info(f"Cleared {log_name}")
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Could not clear {log_name}: {e}")

        # Delete any patched logging.json and its backup so that if log_level is
        # not set on this run, OSB starts fresh with its built-in default config.
        # (If log_level IS set, the patched config is written right after this.)
        for f in (osb_dir / 'logging.json', osb_dir / 'logging.json.bak'):
            try:
                f.unlink()
                logger.info(f"Removed {f.name}")
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.warning(f"Could not remove {f.name}: {e}")

        # Remove result directories older than 21 days from the PVC.
        # Job IDs are YYYYMMDD-HHMMSS so age is derived directly from the name.
        self._purge_old_results(self.results_dir, max_age_days=21)

    def _purge_old_results(self, results_dir: Path, max_age_days: int = 21) -> None:
        """Delete old job result directories and OSB test-run entries from the PVC."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        # 1. /results/<job_id>/ — job IDs are YYYYMMDD-HHMMSS, age from name
        deleted, errors = 0, 0
        try:
            for entry in results_dir.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    job_date = datetime.strptime(entry.name[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue  # not a job directory, skip
                if job_date < cutoff:
                    try:
                        shutil.rmtree(entry)
                        logger.info(f"Purged old result dir: {entry.name} ({job_date.date()})")
                        deleted += 1
                    except Exception as e:
                        logger.warning(f"Could not purge {entry.name}: {e}")
                        errors += 1
        except Exception as e:
            logger.warning(f"Error scanning results dir for purge: {e}")
        if deleted:
            logger.info(f"Purged {deleted} result dir(s) older than {max_age_days}d ({errors} errors)")

        # 2. .osb/benchmarks/test-runs/<uuid>/ — UUID-named so use mtime
        test_runs_dir = Path("/datasets/opensearch-benchmark/.osb/benchmarks/test-runs")
        cutoff_ts = cutoff.timestamp()
        deleted, errors = 0, 0
        try:
            for entry in test_runs_dir.iterdir():
                if not entry.is_dir():
                    continue
                try:
                    if entry.stat().st_mtime < cutoff_ts:
                        shutil.rmtree(entry)
                        deleted += 1
                except Exception as e:
                    logger.warning(f"Could not purge test-run {entry.name}: {e}")
                    errors += 1
        except Exception as e:
            logger.warning(f"Error scanning test-runs dir for purge: {e}")
        if deleted:
            logger.info(f"Purged {deleted} OSB test-run dir(s) older than {max_age_days}d ({errors} errors)")

    def _build_osb_command(
        self,
        ctx: RunContext,
        scenario: str,
        dataset: str,
        engine: str,
    ) -> list:
        """Assemble the opensearch-benchmark CLI command for one sweep."""
        user_tags = {
            'dataset':        dataset,
            'engine':         engine,
            'num_vectors':    ctx.params.get('target_index_num_vectors'),
            'dimension':      ctx.dataset_config.get('dimension'),
            'space_type':     ctx.dataset_config.get('space_type'),
            'query_k':        ctx.params.get('query_k'),
            'ef_search':      ctx.params.get('ef_search'),
            'search_clients': ctx.params.get('search_clients'),
            'method_name':    ctx.params.get('method_name'),
        }
        user_tags_str  = ','.join(f"{k}:{v}" for k, v in user_tags.items() if v is not None)
        client_timeout = ctx.params.get('client_timeout', 300)

        cmd = [
            'opensearch-benchmark', 'run',
            '--workload-path',   ctx.workload_path,
            '--target-hosts',    ctx.target_host,
            '--client-options',  f'timeout:{client_timeout},use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin',
            '--test-procedure',  scenario,
            '--kill-running-processes',
            f'--user-tag={user_tags_str}',
        ]

        params_file = ctx.results_dir / 'workload-params.json'
        with open(params_file, 'w') as f:
            json.dump(ctx.params, f, indent=2)
        logger.info(f"Workload params written to: {params_file}")
        logger.info(f"Workload params content:\n{json.dumps(ctx.params, indent=2)}")
        logger.info(f"User tags: {user_tags_str}")

        cmd.extend(['--workload-params', str(params_file)])
        return cmd

    @contextlib.contextmanager
    def _log_level_override(self, log_level: Optional[str]):
        """Context manager that patches OSB's logging.json for the duration of
        a sweep and restores the original on exit (even if the sweep fails)."""
        benchmark_home  = Path('/datasets/opensearch-benchmark')
        log_path        = benchmark_home / '.osb' / 'logging.json'
        backup_path     = benchmark_home / '.osb' / 'logging.json.bak'

        valid_levels = {'debug', 'info', 'warning', 'error'}
        if not log_level or log_level.lower() not in valid_levels:
            if log_level:
                logger.warning(f"Ignoring unknown log_level '{log_level}'; valid: {valid_levels}")
            yield   # no-op — nothing to patch or restore
            return

        patched = False
        try:
            if log_path.exists():
                shutil.copy2(log_path, backup_path)

            level_upper = log_level.upper()
            log_dir     = benchmark_home / '.osb' / 'logs'
            log_dir.mkdir(parents=True, exist_ok=True)
            log_config = {
                "version": 1,
                "formatters": {
                    "normal":  {"format": "%(asctime)s,%(msecs)d %(actorAddress)s/PID:%(process)d %(name)s %(levelname)s %(message)s", "datefmt": "%Y-%m-%d %H:%M:%S", "()": "osbenchmark.log.configure_utc_formatter"},
                    "profile": {"format": "%(asctime)s,%(msecs)d PID:%(process)d %(name)s %(levelname)s %(message)s",                "datefmt": "%Y-%m-%d %H:%M:%S", "()": "osbenchmark.log.configure_utc_formatter"},
                    "trace":   {"format": "%(asctime)s %(message)s", "datefmt": "%Y-%m-%d %H:%M:%S"},
                },
                "filters":  {"isActorLog": {"()": "thespian.director.ActorAddressLogFilter"}},
                "handlers": {
                    "benchmark_log_handler":     {"class": "logging.handlers.WatchedFileHandler", "filename": str(log_dir / "benchmark.log"),  "encoding": "UTF-8", "formatter": "normal",  "filters": ["isActorLog"]},
                    "benchmark_profile_handler": {"class": "logging.FileHandler",                 "filename": str(log_dir / "profile.log"),     "delay": True, "encoding": "UTF-8", "formatter": "profile"},
                    "http_trace_handler":        {"class": "logging.handlers.WatchedFileHandler", "filename": str(log_dir / "http-trace.log"),  "encoding": "UTF-8", "formatter": "trace"},
                },
                "root":    {"handlers": ["benchmark_log_handler"], "level": level_upper},
                "loggers": {
                    "opensearch":         {"handlers": ["benchmark_log_handler"], "level": level_upper, "propagate": False},
                    "opensearchpy.trace": {"handlers": ["http_trace_handler"],    "level": level_upper, "propagate": False},
                    "benchmark.profile":  {"handlers": ["benchmark_profile_handler"], "level": "INFO", "propagate": False},
                },
            }
            with open(log_path, 'w') as f:
                json.dump(log_config, f, indent=2)
            patched = True
            logger.info(f"Logging config patched: level={level_upper}")
            yield
        except Exception as e:
            logger.warning(f"Failed to patch logging.json: {e}")
            yield
        finally:
            if patched:
                try:
                    if backup_path.exists():
                        shutil.move(str(backup_path), str(log_path))
                    else:
                        log_path.unlink(missing_ok=True)
                except Exception as e:
                    logger.warning(f"Failed to restore logging.json: {e}")

    # ── 4. Process lifecycle ──────────────────────────────────────────────────

    def _launch_process(
        self,
        cmd: list,
        env: dict,
        active_key: str,
        cancel_event: Optional[threading.Event],
    ) -> subprocess.Popen:
        """Launch the opensearch-benchmark subprocess in its own process group,
        register it for cross-Gunicorn-worker cancellation, and write its PGID
        to disk so other workers can kill it if needed."""
        run_dir = Path("/workspace/run")
        run_dir.mkdir(parents=True, exist_ok=True)
        pgid_file = run_dir / f"job_{active_key}.pgid"

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,  # Breaks into its own PGID
        )

        pgid = os.getpgid(proc.pid)
        pgid_file.write_text(str(pgid))

        with self._active_lock:
            self._active[active_key] = (proc, cancel_event)

        return proc

    def _poll_until_done(
        self,
        proc: subprocess.Popen,
        cmd: list,
        job_id: str,
        active_key: str,
        target_host: str,
        cancel_event: Optional[threading.Event],
    ) -> subprocess.CompletedProcess:
        """Poll the benchmark subprocess until it finishes, is cancelled, times
        out, or the cluster health watchdog decides to kill it.

        Returns a CompletedProcess with the exit code, stdout, and stderr.
        """
        TIMEOUT_SECONDS    = 21600   # 6-hour hard limit
        POLL_INTERVAL      = 1.0     # seconds between loop iterations
        HEALTH_INTERVAL    = 60.0    # seconds between cluster health checks
        RED_TOLERANCE      = 5       # consecutive red checks before killing

        elapsed            = 0.0
        health_elapsed     = 0.0
        red_consecutive    = 0
        pgid_file          = Path("/workspace/run") / f"job_{active_key}.pgid"

        try:
            while proc.poll() is None:
                if cancel_event and cancel_event.is_set():
                    logger.info(f"Job {job_id}: cancel requested — killing process tree.")
                    _kill_proc_group(proc)
                    break

                if elapsed >= TIMEOUT_SECONDS:
                    logger.warning(f"Job {job_id}: hit 6-hour hard limit — killing process tree.")
                    _kill_proc_group(proc)
                    break

                health_elapsed += POLL_INTERVAL
                if health_elapsed >= HEALTH_INTERVAL:
                    health_elapsed = 0.0
                    status = self._poll_cluster_health(target_host)
                    if status in ('green', 'yellow'):
                        red_consecutive = 0
                    else:
                        red_consecutive += 1
                        logger.warning(
                            f"Job {job_id}: cluster health is '{status}' "
                            f"({red_consecutive}/{RED_TOLERANCE} consecutive checks)"
                        )
                        if red_consecutive >= RED_TOLERANCE:
                            logger.error(
                                f"Job {job_id}: cluster has been red/unreachable for "
                                f"{red_consecutive} consecutive checks — killing benchmark process."
                            )
                            _kill_proc_group(proc)
                            return subprocess.CompletedProcess(
                                cmd, -1, '', 'Killed: cluster went red during execution'
                            )

                time.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL

            stdout, stderr = proc.communicate()
            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

        finally:
            # Always clean up the PGID file and deregister the process
            if pgid_file.exists():
                try:
                    pgid_file.unlink()
                except Exception:
                    pass
            with self._active_lock:
                self._active.pop(active_key, None)

    def _poll_cluster_health(self, target_host: str) -> Optional[str]:
        """Single non-retrying cluster health check used by the mid-run watchdog.
        Returns the cluster status string ('green', 'yellow', 'red') or None if
        the cluster is unreachable."""
        try:
            response = requests.get(
                f"https://{target_host}/_cluster/health",
                auth=HTTPBasicAuth('admin', 'admin'),
                verify=False,
                timeout=10,
            )
            if response.status_code == 200:
                return response.json().get('status')
        except Exception:
            pass
        return None

    # ── 5. Post-sweep cleanup ─────────────────────────────────────────────────

    def _save_server_stats(
        self,
        sweep_results_dir: Path,
        start_time: datetime,
        end_time: datetime,
        target_host: str,
        stats_before: Optional[Dict],
    ) -> None:
        """Diff node stats before/after and write server_stats.json."""
        stats_after = _fetch_node_stats(target_host)
        if stats_before and stats_after:
            try:
                server_stats = {
                    'captured_at_start': start_time.isoformat(),
                    'captured_at_end':   end_time.isoformat(),
                    'node_deltas':       _diff_node_stats(stats_before, stats_after),
                    'snapshots':         {'before': stats_before, 'after': stats_after},
                }
                with open(sweep_results_dir / 'server_stats.json', 'w') as f:
                    json.dump(server_stats, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save server_stats: {e}")

    def _stop_metrics_collection(self) -> None:
        """Stop the background metrics thread and wait for it to finish."""
        if self.metrics_collector and self.metrics_thread:
            logger.info("📊 Stopping metrics collection...")
            try:
                self.metrics_collector.stop_collection()
                self.metrics_thread.join(timeout=30)
            except Exception as e:
                logger.error(f"Error stopping metrics collection: {e}")
            finally:
                self.metrics_thread = None

    def _download_artifacts(self, console_output: str, target_dir: Path,
                            stderr: str = ''):
        """
        Download benchmark artifacts from opensearch-benchmark home directory.
        Extracts test_run.json and benchmark.log from the benchmark execution.

        If the OSB benchmark.log is empty (e.g. OSB crashed before writing
        anything), falls back to writing stdout+stderr so the UI always has
        something to show.
        """
        BENCHMARK_HOME = "/datasets/opensearch-benchmark"
        
        # Regex match UUIDs for the target test execution run
        uuid_pattern = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
        match = re.search(uuid_pattern, console_output)
        
        if match:
            run_id = match.group(0)
            remote_json_path = f"{BENCHMARK_HOME}/.osb/benchmarks/test-runs/{run_id}/test_run.json"
            
            try:
                # Read the test_run.json file
                with open(remote_json_path, 'r') as f:
                    test_run_content = f.read()
                
                # Save to results directory
                (target_dir / "test_run.json").write_text(test_run_content, encoding="utf-8")
                logger.info(f"Downloaded test_run.json for run {run_id}")
            except FileNotFoundError:
                logger.warning(f"test_run.json not found at {remote_json_path}")
            except Exception as e:
                logger.error(f"Error downloading test_run.json: {e}")
        else:
            logger.warning("Could not find run UUID in benchmark output")
        
        # Move benchmark.log into the results directory using a filesystem rename
        # so the full file is preserved without ever loading it into Python memory.
        # This avoids OOMKill when debug logging produces multi-GB log files.
        # _clear_benchmark_logs() will recreate a fresh empty file before the next run.
        log_src = Path(f"{BENCHMARK_HOME}/.osb/logs/benchmark.log")
        log_dst = target_dir / "benchmark.log"
        moved = False
        try:
            if log_src.exists() and log_src.stat().st_size > 0:
                shutil.move(str(log_src), str(log_dst))
                logger.info(f"Moved benchmark.log ({log_dst.stat().st_size / 1024 / 1024:.1f} MB)")
                moved = True
        except Exception as e:
            logger.error(f"Error moving benchmark.log: {e}")

        # If the log is missing or empty (fast crash / pre-launch failure), fall back
        # to writing stdout+stderr so the UI always has something to show.
        if not moved:
            fallback_parts = []
            if console_output and console_output.strip():
                fallback_parts.append("=== stdout ===\n" + console_output)
            if stderr and stderr.strip():
                fallback_parts.append("=== stderr ===\n" + stderr)
            if fallback_parts:
                log_dst.write_text('\n'.join(fallback_parts), encoding="utf-8")
                logger.info("benchmark.log was empty — writing stdout/stderr fallback")

    # ── 6. Aggregation ────────────────────────────────────────────────────────

    def _aggregate_results(
        self,
        all_results: list,
        job_id: str,
    ) -> Dict[str, Any]:
        """Collapse per-sweep results into a single job-level summary dict."""
        failed_sweeps    = [r for r in all_results if r.get('status') == 'failed']
        cancelled_sweeps = [r for r in all_results if r.get('status') == 'cancelled']
        total_duration   = sum(r.get('duration_seconds', 0) for r in all_results)

        if cancelled_sweeps:
            overall_status = 'cancelled'
        elif failed_sweeps:
            overall_status = 'partial_failure' if len(failed_sweeps) < len(all_results) else 'failed'
        else:
            overall_status = 'completed'

        return {
            'status':             overall_status,
            'total_sweeps':       len(all_results),
            'successful_sweeps':  len(all_results) - len(failed_sweeps) - len(cancelled_sweeps),
            'failed_sweeps':      len(failed_sweeps),
            'cancelled_sweeps':   len(cancelled_sweeps),
            'total_duration_seconds': total_duration,
            'results_dir':        str(self.results_dir / job_id),
            'sweep_results':      all_results,
        }

    # ── Public methods ────────────────────────────────────────────────────────

    def cancel(self, job_id: str) -> None:
        """Signal cancellation for job_id: set its event and kill its running process group."""
        with self._active_lock:
            entry = self._active.get(job_id)
        if entry is None:
            return
        proc, event = entry
        if event is not None:
            event.set()
        _kill_proc_group(proc)

    def validate_benchmark_request(
        self,
        dataset: str,
        engine: str,
        scenario: str
    ) -> Optional[str]:
        """
        Validate benchmark request parameters
        
        Returns:
            Error message if invalid, None if valid
        """
        # Check if dataset exists
        dataset_config = self.config.get_dataset_config(dataset)
        if not dataset_config:
            return f"Dataset '{dataset}' not found in configuration"
        
        # Check if engine is supported for this dataset
        engine_params = dataset_config.get('engine_params', {})
        param_files = dataset_config.get('param_files', {})
        
        if engine not in engine_params and engine not in param_files:
            available_engines = list(engine_params.keys()) if engine_params else list(param_files.keys())
            available = ', '.join(available_engines)
            return f"Engine '{engine}' not supported for dataset '{dataset}'. Available: {available}"
        
        return None  # Valid — filesystem checks happen at execution time on the worker

    def run_benchmark(
        self,
        dataset: str,
        engine: str,
        scenario: str = 'search',
        job_id: Optional[str] = None,
        enable_profiling: bool = True,
        enable_metrics: bool = True,
        workload_params: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
        log_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a benchmark scenario across all parameter sweeps."""
        try:
            if job_id is None:
                job_id = str(uuid.uuid4())

            sweeps     = self._get_run_contexts(job_id, dataset, engine, scenario, workload_params)
            env        = {**os.environ, 'TERM': 'dumb', 'BENCHMARK_HOME': '/datasets/opensearch-benchmark'}
            active_key = job_id.split('/')[0] if '/' in job_id else job_id

            all_results = []
            for idx, ctx in enumerate(sweeps, 1):
                if cancel_event and cancel_event.is_set():
                    logger.info(f"Job {job_id}: cancellation requested before sweep {idx}")
                    break

                logger.info(f"Running sweep {idx}/{len(sweeps)}: dataset={dataset}, engine={engine}, scenario={scenario}")

                if ctx.dataset_config and not self.config.download_dataset_files(dataset, ctx.params):
                    return {'status': 'failed', 'error': f'Failed to download dataset files for sweep {idx}.'}

                logger.info(f"Checking cluster health for {engine}...")
                if not self._check_cluster_health(ctx.target_host):
                    all_results.append({
                        'status': 'failed', 'error': f'Cluster {ctx.target_host} is not ready.',
                        'exit_code': -1, 'sweep_index': idx, 'sweep_params': ctx.sweep_params,
                    })
                    continue

                if enable_metrics:
                    self._start_metrics_collection(engine, ctx.target_host, ctx.results_dir, dataset, scenario, idx)

                self._clear_benchmark_logs()
                cmd = self._build_osb_command(ctx, scenario, dataset, engine)
                logger.info(f"Command: {' '.join(cmd)}")

                start_time   = datetime.utcnow()
                stats_before = _fetch_node_stats(ctx.target_host)
                result       = None

                with self._log_level_override(log_level):
                    try:
                        proc   = self._launch_process(cmd, env, active_key, cancel_event)
                        result = self._poll_until_done(proc, cmd, job_id, active_key, ctx.target_host, cancel_event)
                    finally:
                        end_time = datetime.utcnow()
                        self._save_server_stats(ctx.results_dir, start_time, end_time, ctx.target_host, stats_before)
                        self._stop_metrics_collection()

                duration = (end_time - start_time).total_seconds()

                if result is None:
                    all_results.append({
                        'status': 'failed', 'error': 'Process failed to initialize',
                        'sweep_index': idx, 'sweep_params': ctx.sweep_params,
                    })
                    continue

                self._download_artifacts(result.stdout, ctx.results_dir, stderr=result.stderr)

                is_cancelled = (cancel_event and cancel_event.is_set()) or result.returncode == -9
                all_results.append({
                    'status':           'cancelled' if is_cancelled else ('completed' if result.returncode == 0 else 'failed'),
                    'exit_code':        result.returncode,
                    'duration_seconds': duration,
                    'started_at':       start_time.isoformat(),
                    'completed_at':     end_time.isoformat(),
                    'results_dir':      str(ctx.results_dir),
                    'sweep_index':      idx,
                    'sweep_params':     ctx.sweep_params,
                    'command':          ' '.join(cmd),
                    'stdout_tail':      result.stdout[-5000:] if result.stdout else '',
                    'stderr_tail':      result.stderr[-5000:] if result.stderr else '',
                })

                if is_cancelled:
                    _kill_proc_group(proc)
                    break

            return self._aggregate_results(all_results, job_id)

        except Exception as e:
            logger.error(f"Benchmark execution error: {e}", exc_info=True)
            return {'status': 'error', 'error': str(e)}
