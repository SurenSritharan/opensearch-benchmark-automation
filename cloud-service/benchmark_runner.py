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
import requests
from async_profiler import AsyncProfiler
from config_loader import ConfigLoader, get_os_namespace, sweep_directory_name
from event_tracer import RunTrace, trace_event
from metrics_collector import MetricsCollector
from node_stats_poller import diff_node_stats

_CORE_ENGINES = {'jvector', 'faiss', 'lucene'}

def _base_engine(engine: str) -> str:
    """Return the base engine name for dataset config / workload param lookups.

    "jvector-acl" is a separate worker pod but uses the same workload configs as
    "jvector".  Stripping the suffix here means datasets.yaml never needs an
    explicit "jvector-acl" section.
    """
    base = engine.split('-')[0]
    return base if base in _CORE_ENGINES else engine


@dataclass
class RunContext:
    """Everything needed to execute one benchmark sweep — fully resolved upfront."""
    target_host:    str
    workload_path:  str
    dataset_config: Dict[str, Any]
    params:         Dict[str, Any]   # merged base → procedure → sweep → runtime (no credentials)
    sweep_params:   Dict[str, Any]   # raw sweep-level params (for reporting only)
    results_dir:    Path             # pre-built results directory for this sweep
    username:       str = 'admin'   # cluster auth — kept off params so never sent to OSB
    password:       str = 'admin'
    client_timeout: int = 600       # HTTP client timeout (s) — runner-only, never sent to OSB
    pipeline_name:  str = ''        # originating pipeline name (e.g. "acl-sweep-1m") — runner-only


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
    

_CERT    = os.environ.get('OS_CERT', '/certs/admin.pem')
_KEY     = os.environ.get('OS_KEY', '/certs/admin-key.pem')
_CA      = os.environ.get('OS_CA', '/certs/root-ca.pem')
_BENCHMARK_HOME = Path(os.environ.get('BENCHMARK_HOME', '/datasets/opensearch-benchmark'))
_DATASETS_ROOT = Path(os.environ.get('DATASETS_ROOT', '/datasets'))


def _use_ssl() -> bool:
    return os.environ.get('USE_SSL', 'true').lower() == 'true'


def _request_kwargs(timeout: int) -> Dict[str, Any]:
    """Return GKE certificate or local basic-auth request settings."""
    if all(Path(path).exists() for path in (_CERT, _KEY, _CA)):
        return {'cert': (_CERT, _KEY), 'verify': _CA, 'timeout': timeout}
    return {
        'auth': (
            os.environ.get('AUTH_USER', 'admin'),
            os.environ.get('AUTH_PASS', 'admin'),
        ),
        'verify': os.environ.get('VERIFY_CERTS', 'false').lower() == 'true',
        'timeout': timeout,
    }


def _base_url(target_host: str) -> str:
    return f"{'https' if _use_ssl() else 'http'}://{target_host}"


def _save_index_snapshot(engine: str, target_host: str, index_name: str,
                         results_dir: Path) -> None:
    """Save index metadata beside the run that used this resolved index."""
    try:
        base_url = f"{_base_url(target_host)}/{index_name}"
        snapshot = {'index': index_name, 'engine': engine}
        for key, path in [('mapping', '/_mapping'), ('settings', '/_settings'), ('stats', '/_stats')]:
            response = requests.get(f'{base_url}{path}', **_request_kwargs(15))
            snapshot[key] = response.json() if response.ok else {'error': f'HTTP {response.status_code}'}

        results_dir.mkdir(parents=True, exist_ok=True)
        out = results_dir / 'index_snapshot.json'
        out.write_text(json.dumps(snapshot, indent=2))
        logger.info(f'Saved index snapshot for {index_name} to {out}')
    except Exception as e:
        logger.warning(f'Index snapshot failed for {index_name}: {e}')

def _fetch_node_stats(target_host: str, error_callback=None) -> Optional[Dict]:
    """Snapshot _nodes/stats from the OpenSearch cluster via REST API."""
    try:
        url = f"{_base_url(target_host)}/_nodes/stats/jvm,os,process,fs,thread_pool,indices"
        resp = requests.get(url, **_request_kwargs(15))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logging.getLogger(__name__).warning(f"Could not fetch _nodes/stats: {e}")
        if error_callback is not None:
            error_callback(str(e))
        return None


logger = logging.getLogger(__name__)
# Params consumed by the runner itself — must never be forwarded to OSB as workload
# params because the workload loader raises WorkloadConfigError on unknown parameters.
_RUNNER_ONLY_KEYS = frozenset({'client_timeout', 'ingest_max_attempts'})


class BenchmarkRunner:
    """Executes opensearch-benchmark commands without kubectl dependencies"""
    
    def __init__(self, config_loader: ConfigLoader, results_dir: Optional[str] = None):
        self.config = config_loader
        self.results_dir = Path(results_dir or os.environ.get('RESULTS_DIR', '/results'))
        self.benchmark_home = _BENCHMARK_HOME
        self.datasets_root = _DATASETS_ROOT
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_collector = None
        self.profiler = AsyncProfiler()
        # Maps job_id -> (Popen process, cancel_event) for active benchmarks
        self._active: Dict[str, tuple] = {}
        self._active_lock = threading.Lock()
        # Callbacks injected by app.py to read/write job state without a circular import.
        # Set via set_job_callbacks() before running any benchmarks.
        self._get_job_fn = None
        self._save_job_fn = None
        # Atomic heap-dump append callback — avoids the read-modify-write race
        # between _check_and_dump_heap and process_batch_job's scenario saves.
        self._append_heap_dump_fn = None

    def set_job_callbacks(self, get_job_fn, save_job_fn) -> None:
        """Inject get_job / save_job from app.py so benchmark_runner can signal
        heap dump requests without importing app.py (which would be circular)."""
        self._get_job_fn = get_job_fn
        self._save_job_fn = save_job_fn

    def set_append_heap_dump_callback(self, append_heap_dump_fn) -> None:
        """Inject the atomic append_heap_dump_request callback from app.py.
        When set, _check_and_dump_heap uses this instead of a full read-modify-write
        so heap_dump_requests are never lost to a concurrent scenario save."""
        self._append_heap_dump_fn = append_heap_dump_fn

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
        cfg_engine = _base_engine(engine)
        if engine_params_config and cfg_engine in engine_params_config:
            base_params.update(engine_params_config[cfg_engine])
            logger.info(f"Loaded engine params for {engine} (config key: {cfg_engine}): {list(engine_params_config[cfg_engine].keys())}")

        # Extract runner-only keys before they can bleed into workload params sent to OSB.
        # username/password are popped to prevent mutation leaking into subsequent calls.
        # client_timeout uses .get() — it is already blocked from ctx.params by _RUNNER_ONLY_KEYS.
        username       = (workload_params or {}).pop('username', None) or os.environ.get('AUTH_USER', 'admin')
        password       = (workload_params or {}).pop('password', None) or os.environ.get('AUTH_PASS', 'admin')
        step_u         = (workload_params or {}).pop('step_username',  '')
        step_p         = (workload_params or {}).pop('step_password',  '')
        pipeline_name  = (workload_params or {}).pop('pipeline_name',  '')
        client_timeout = (workload_params or {}).get('client_timeout', 600)

        # Per-step credentials override the job-level credentials when present.
        # global_u retains the original job-level username for credential-source logging below.
        global_u = username
        if step_u:
            username = step_u
            password = step_p or password

        # ACL tracking — log exactly which credential source won so it is visible
        # in the worker log for every step.
        if step_u:
            logger.info(
                f"[ACL] _get_run_contexts: dataset={dataset} scenario={scenario} "
                f"credential source=STEP step_username={step_u!r}"
            )
        elif global_u != 'admin':
            logger.info(
                f"[ACL] _get_run_contexts: dataset={dataset} scenario={scenario} "
                f"credential source=GLOBAL username={global_u!r}"
            )
        else:
            logger.info(
                f"[ACL] _get_run_contexts: dataset={dataset} scenario={scenario} "
                f"credential source=DEFAULT (admin/admin) — no step_username or global username set"
            )

        # Procedure config: scenario-level params + engine-specific overrides
        # workload_params has already had credentials popped above; use it directly.
        wp               = workload_params or {}
        runtime_sweeps   = wp.pop('parameter_sweeps', None)
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
            cfg_engine = _base_engine(engine)
            if proc_engine_params and cfg_engine in proc_engine_params:
                procedure_base_params.update(proc_engine_params[cfg_engine])
                logger.info(f"Loaded procedure engine params for {engine} (config key: {cfg_engine}): {list(proc_engine_params[cfg_engine].keys())}")

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

        if wp:
            logger.info(f"Runtime params: {list(wp.keys())}")

        # Build one RunContext per sweep — all params resolved, results dir created
        sweeps = []
        for idx, raw_sweep in enumerate(raw_sweeps, 1):
            sweep_params = raw_sweep.get('params', {})
            sweep_dir_name = sweep_directory_name(idx, raw_sweep)
            if sweep_params:
                logger.info(f"Sweep {idx} params: {list(sweep_params.keys())}")
            merged       = {**base_params, **procedure_base_params, **sweep_params}
            final_params = self.config.resolve_workload_params(dataset, merged, workload_params, scenario=scenario)
            final_params = {k: v for k, v in final_params.items() if k not in _RUNNER_ONLY_KEYS}

            if '/' in job_id:
                base = self.results_dir / job_id
                results_dir = base / sweep_dir_name if has_sweeps else base
            else:
                base = self.results_dir / job_id / scenario
                results_dir = base / sweep_dir_name if has_sweeps else base
            results_dir.mkdir(parents=True, exist_ok=True)

            sweeps.append(RunContext(
                target_host    = target_host,
                workload_path  = workload_path,
                dataset_config = dataset_config,
                params         = final_params,
                sweep_params   = sweep_params,
                results_dir    = results_dir,
                username       = username,
                password       = password,
                client_timeout = client_timeout,
                pipeline_name  = pipeline_name,
            ))

        return sweeps

    # ── 2. Pre-flight ─────────────────────────────────────────────────────────

    def _check_cluster_health(self, target_host: str, retries: int = 30, retry_delay: int = 20) -> bool:
        """Check if OpenSearch cluster is healthy and ready.

        Retries up to `retries` times with `retry_delay` seconds between
        attempts so a transiently red/initializing cluster (e.g. just after
        scale-up) doesn't immediately fail all scenarios.
        """
        url = f"{_base_url(target_host)}/_cluster/health"
        for attempt in range(1, retries + 1):
            try:
                response = requests.get(
                    url,
                    **_request_kwargs(10)
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
        self, engine: str, target_host: str, sweep_results_dir: Path,
        dataset: str, scenario: str, sweep_idx: int,
    ) -> None:
        if self.metrics_collector is not None:
            if not self._stop_metrics_collection():
                message = (
                    "Previous metrics collection is still running; "
                    "skipping metrics collection for this sweep"
                )
                logger.warning(message)
                trace_event(
                    sweep_results_dir,
                    "metrics_collection_skipped",
                    reason="previous_collector_still_running",
                    dataset=dataset,
                    scenario=scenario,
                    sweep=sweep_idx,
                )
                return
        # Keep metrics best-effort: initialization, tracing, or thread startup
        # must not prevent the benchmark itself from running.
        try:
            collector = MetricsCollector(
                engine=engine, target_host=target_host, results_dir=sweep_results_dir,
                fetch_node_stats=_fetch_node_stats,
            )
            self.metrics_collector = collector
            collector.start(dataset=dataset, scenario=scenario, sweep=sweep_idx)
        except Exception as e:
            logger.warning("Could not start metrics collection: %s", e, exc_info=True)
            try:
                trace_event(
                    sweep_results_dir,
                    "metrics_collection_start_failed",
                    error=str(e),
                    dataset=dataset,
                    scenario=scenario,
                    sweep=sweep_idx,
                )
            except Exception:
                logger.debug("Could not write metrics start failure trace", exc_info=True)

    def _clear_benchmark_logs(self):
        """Clear benchmark log files and reset logging.json before each run."""
        osb_dir = self.benchmark_home / '.osb'
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
        test_runs_dir = self.benchmark_home / '.osb' / 'benchmarks' / 'test-runs'
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

        cmd = [
            'opensearch-benchmark', 'run',
            '--workload-path',   ctx.workload_path,
            '--target-hosts',    ctx.target_host,
            '--client-options',  f'timeout:{ctx.client_timeout},use_ssl:{str(_use_ssl()).lower()},verify_certs:{str(os.environ.get("VERIFY_CERTS", "false").lower() == "true").lower()},basic_auth_user:{ctx.username},basic_auth_password:{ctx.password}',
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
        # ACL tracking — log the username that will appear in --client-options.
        # This is the ground truth of who OSB actually authenticates as.
        logger.info(
            f"[ACL] _build_osb_command: scenario={scenario} "
            f"OSB will authenticate as user={ctx.username!r}"
        )

        cmd.extend(['--workload-params', str(params_file)])
        return cmd

    @contextlib.contextmanager
    def _log_level_override(self, log_level: Optional[str]):
        """Context manager that patches OSB's logging.json for the duration of
        a sweep and restores the original on exit (even if the sweep fails)."""
        benchmark_home  = self.benchmark_home
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
        run_dir = Path(os.environ.get('RUN_DIR', str(self.benchmark_home / 'run')))
        run_dir.mkdir(parents=True, exist_ok=True)
        pgid_file = run_dir / f"job_{active_key}.pgid"

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(self.datasets_root),
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
        engine: str = '',
        results_dir: Optional[Path] = None,
    ) -> subprocess.CompletedProcess:
        """Poll the benchmark subprocess until it finishes, is cancelled, times
        out, or the cluster health watchdog decides to kill it.

        The watchdog uses two separate counters to avoid killing a healthy but
        CPU-busy cluster (e.g. jvector force-merge saturating all cores so the
        Netty HTTP thread pool can't answer /_cluster/health in time):

          red_consecutive  — connection refused/reset (process is dead)
                             → kill after RED_TOLERANCE checks
          busy_consecutive — read timeout (process alive, HTTP thread queued)
                             → kill after BUSY_TOLERANCE checks (extended grace)

        The distinction is made entirely from the exception type raised by the
        HTTP request — no kubectl or external dependencies required.

        When heap usage on any node exceeds HEAP_DUMP_THRESHOLD_PCT a heap dump
        is triggered once per node per run via jcmd inside the opensearch
        container, then copied to results_dir/heap-dumps/.

        Returns a CompletedProcess with the exit code, stdout, and stderr.
        """
        TIMEOUT_SECONDS        = 86400   # 24-hour hard limit
        POLL_INTERVAL          = 1.0     # seconds between loop iterations
        HEALTH_INTERVAL        = 60.0    # seconds between cluster health checks
        RED_TOLERANCE          = 5       # consecutive connection failures → kill
        BUSY_TOLERANCE         = 60      # consecutive read timeouts → kill
                                         # 60 × 60 s = 60 min grace; the 24-hour hard limit
                                         # is the real backstop for CPU-bound work like
                                         # jvector force-merge (final graph build can take
                                         # 2-4 h on small nodes for a 1.67M-vector shard)
        HEAP_DUMP_THRESHOLD_PCT = 95     # trigger a heap dump when any node exceeds this

        elapsed            = 0.0
        health_elapsed     = 0.0
        red_consecutive    = 0
        busy_consecutive   = 0
        heap_dumped_nodes: set = set()   # guard: at most one dump per node per run
        run_dir            = Path(os.environ.get('RUN_DIR', str(self.benchmark_home / 'run')))
        pgid_file          = run_dir / f"job_{active_key}.pgid"

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

                    # ── Heap-dump check ────────────────────────────────────────
                    # Requires the atomic append callback (preferred) or at minimum
                    # the legacy get/save pair so _check_and_dump_heap can persist
                    # the signal.
                    if engine and results_dir and (
                        self._append_heap_dump_fn or
                        (self._get_job_fn and self._save_job_fn)
                    ):
                        self._check_and_dump_heap(
                            target_host, engine, results_dir,
                            heap_dumped_nodes, HEAP_DUMP_THRESHOLD_PCT, job_id,
                        )

                    status, timed_out = self._poll_cluster_health(target_host)
                    if status in ('green', 'yellow'):
                        red_consecutive  = 0
                        busy_consecutive = 0
                    elif timed_out:
                        # Read timeout: the TCP connection was accepted but no response
                        # arrived within the window. The process is alive but its HTTP
                        # threads are CPU-starved (e.g. jvector graph build monopolising
                        # all cores). Use the longer busy tolerance.
                        busy_consecutive += 1
                        red_consecutive   = 0
                        logger.warning(
                            f"Job {job_id}: cluster health timed out (HTTP thread busy) "
                            f"— waiting... ({busy_consecutive}/{BUSY_TOLERANCE})"
                        )
                        if busy_consecutive >= BUSY_TOLERANCE:
                            logger.error(
                                f"Job {job_id}: cluster HTTP unresponsive for "
                                f"{busy_consecutive} consecutive checks — killing benchmark process."
                            )
                            _kill_proc_group(proc)
                            return subprocess.CompletedProcess(
                                cmd, -1, '',
                                'Killed: cluster HTTP unresponsive (read timeout)'
                            )
                    else:
                        # Connection refused/reset or other hard error: process is dead.
                        busy_consecutive  = 0
                        red_consecutive  += 1
                        logger.warning(
                            f"Job {job_id}: cluster health is '{status}' (connection failed) "
                            f"({red_consecutive}/{RED_TOLERANCE} consecutive checks)"
                        )
                        if red_consecutive >= RED_TOLERANCE:
                            logger.error(
                                f"Job {job_id}: cluster has been unreachable for "
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

    def _poll_cluster_health(self, target_host: str) -> tuple:
        """Single non-retrying cluster health check used by the mid-run watchdog.

        Returns (status, timed_out) where:
          status    — 'green'/'yellow'/'red' or None if unreachable
          timed_out — True if the failure was a read timeout (process alive but
                      HTTP thread busy), False if connection was refused/reset
                      (process likely dead)
        """
        try:
            response = requests.get(
                f"{_base_url(target_host)}/_cluster/health",
                **_request_kwargs(10),
            )
            if response.status_code == 200:
                return response.json().get('status'), False
            return None, False
        except requests.exceptions.ReadTimeout:
            return None, True   # TCP connected, HTTP thread didn't respond in time
        except Exception:
            return None, False  # connection refused, reset, DNS failure, etc.

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
                    'node_deltas':       diff_node_stats(stats_before, stats_after),
                    'snapshots':         {'before': stats_before, 'after': stats_after},
                }
                with open(sweep_results_dir / 'server_stats.json', 'w') as f:
                    json.dump(server_stats, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save server_stats: {e}")

    def _stop_metrics_collection(self) -> bool:
        if self.metrics_collector is None:
            return True
        try:
            if self.metrics_collector.stop():
                self.metrics_collector = None
                return True
        except Exception as e:
            logger.warning("Could not stop metrics collection cleanly: %s", e, exc_info=True)
        return False

    def _start_profiling(self, engine: str, results_dir: Path, duration: int = 60) -> None:
        self.profiler.start(engine, results_dir, duration)

    def _check_and_dump_heap(
        self,
        target_host: str,
        engine: str,
        results_dir: Path,
        dumped_nodes: set,
        threshold_pct: int,
        job_id: str,
    ) -> None:
        """Check heap usage on all nodes; signal Jenkins to collect a heap dump
        for any node that exceeds threshold_pct and has not already been dumped
        this run.

        Uses self._append_heap_dump_fn (preferred — atomic under db_lock) or falls
        back to self._get_job_fn / self._save_job_fn when the atomic callback is not
        set. Errors are logged as warnings — a failed signal never affects the run.
        """
        stats = _fetch_node_stats(target_host)
        if not stats:
            return

        namespace = get_os_namespace(engine)

        # job_id may be a nested path like "20260917-024756-71a65b16/jvector/scenario"
        # — the top-level job key is the first segment.
        job_key = job_id.split('/')[0]

        for node_id, node in stats.get('nodes', {}).items():
            node_name = node.get('name', node_id)
            heap_pct  = node.get('jvm', {}).get('mem', {}).get('heap_used_percent', 0)

            if heap_pct < threshold_pct or node_name in dumped_nodes:
                continue

            logger.warning(
                f"[heap-dump] Job {job_id}: node {node_name!r} heap at {heap_pct}% "
                f"(>= {threshold_pct}%) — signalling Jenkins to collect heap dump"
            )
            dumped_nodes.add(node_name)  # mark before writing so a slow write isn't re-triggered

            entry = {'node': node_name, 'namespace': namespace, 'heap_pct': heap_pct}

            if self._append_heap_dump_fn:
                # Preferred path: atomic append under db_lock in app.py.
                # Avoids the race where process_batch_job's concurrent save_job call
                # overwrites heap_dump_requests with a stale copy that lacks this entry.
                try:
                    self._append_heap_dump_fn(job_key, entry)
                except Exception as e:
                    logger.warning(f"[heap-dump] Could not save heap dump request for {node_name}: {e}")
            else:
                # Fallback: full read-modify-write (original behaviour).
                try:
                    job_data = self._get_job_fn(job_key)
                    if job_data is None:
                        continue
                    job_data.setdefault('heap_dump_requests', []).append(entry)
                    self._save_job_fn(job_key, job_data)
                except Exception as e:
                    logger.warning(f"[heap-dump] Could not save heap dump request for {node_name}: {e}")

    def _stop_profiling(self) -> None:
        self.profiler.stop()

    def _add_run_context(self, test_run_data: Dict[str, Any], ctx: 'RunContext') -> Dict[str, Any]:
        """
        Inject live run context into test_run.json so each result file is
        self-describing: who ran it, against what, and what the index contained.

        All values come directly from the RunContext resolved at job submission
        time — no static data, no ACL file parsing.

        visible_doc_count is fetched as the same user that ran the benchmark so
        for DLS users it reflects the DLS-filtered doc count, not the total index
        size. Falls back to None if the cluster is unreachable — does NOT fail
        the run.

        run_context is only written for ACL pipelines (pipeline name starts with "acl-").
        Non-ACL runs are returned unchanged.
        """
        if ctx is None:
            return test_run_data

        if not ctx.pipeline_name.startswith('acl-'):
            return test_run_data

        try:
            params = ctx.params or {}

            index = params.get('target_index_name') or ''

            visible_doc_count = None
            if index:
                try:
                    url  = f"{_base_url(ctx.target_host)}/{index}/_count"
                    resp = requests.post(
                        url,
                        auth=(ctx.username, ctx.password),
                        verify=False,
                        timeout=10,
                        json={"query": {"match_all": {}}},
                    )
                    if resp.status_code == 200:
                        visible_doc_count = resp.json().get('count')
                    else:
                        logger.warning(
                            f"[run_context] _count returned HTTP {resp.status_code} "
                            f"for index={index!r} user={ctx.username!r}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[run_context] Could not fetch _count for index={index!r} "
                        f"user={ctx.username!r}: {e}"
                    )

            space_type = (
                params.get('target_index_space_type')
                or (ctx.dataset_config or {}).get('space_type', '')
            )

            test_run_data['run_context'] = {
                'username':                    ctx.username,
                'index':                       index,
                'engine':                      params.get('engine', ''),
                'space_type':                  space_type,
                'query_k':                     params.get('query_k'),
                'hnsw_ef_search':              params.get('hnsw_ef_search'),
                'search_clients':              params.get('search_clients'),
                'num_vectors':                 params.get('num_vectors'),
                'visible_doc_count':           visible_doc_count,
                'target_host':                 ctx.target_host,
            }

            logger.info(
                f"[run_context] user={ctx.username!r} index={index!r} "
                f"visible_doc_count={visible_doc_count}"
            )

        except Exception as e:
            logger.error(f"Error adding run_context to test_run.json: {e}")

        return test_run_data



    def _download_artifacts(self, console_output: str, target_dir: Path,
                            ctx: 'RunContext' = None, stderr: str = ''):
        """
        Download benchmark artifacts from opensearch-benchmark home directory.
        Extracts test_run.json and benchmark.log from the benchmark execution.

        If the OSB benchmark.log is empty (e.g. OSB crashed before writing
        anything), falls back to writing stdout+stderr so the UI always has
        something to show.
        """
        BENCHMARK_HOME = self.benchmark_home
        
        # Regex match UUIDs for the target test execution run
        uuid_pattern = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
        match = re.search(uuid_pattern, console_output)
        
        if match:
            run_id = match.group(0)
            remote_json_path = f"{BENCHMARK_HOME}/.osb/benchmarks/test-runs/{run_id}/test_run.json"
            
            try:
                # Read the test_run.json file
                with open(remote_json_path, 'r') as f:
                    test_run_data = json.load(f)
                
                # Enhance with live run context metadata
                test_run_data = self._add_run_context(test_run_data, ctx)
                
                # Save to results directory
                (target_dir / "test_run.json").write_text(json.dumps(test_run_data, indent=2), encoding="utf-8")
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
        
        # Check if engine is supported for this dataset.
        # Use the base engine name so "jvector-acl" matches the "jvector" entry in datasets.yaml.
        engine_params = dataset_config.get('engine_params', {})
        param_files = dataset_config.get('param_files', {})
        cfg_engine = _base_engine(engine)
        if cfg_engine not in engine_params and cfg_engine not in param_files:
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
        enable_profiling: bool = False,
        profiling_duration: int = 60,
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
            env        = {**os.environ, 'TERM': 'dumb', 'BENCHMARK_HOME': str(self.benchmark_home)}
            active_key = job_id.split('/')[0] if '/' in job_id else job_id

            run_root = self.results_dir / job_id
            trace_event(run_root, "run_started", job_id=job_id, dataset=dataset,
                         engine=engine, scenario=scenario, sweeps=len(sweeps),
                         metrics_enabled=enable_metrics,
                         profiling_enabled=enable_profiling)

            all_results = []
            for idx, ctx in enumerate(sweeps, 1):
                if cancel_event and cancel_event.is_set():
                    logger.info(f"Job {job_id}: cancellation requested before sweep {idx}")
                    trace_event(run_root, "run_cancelled_before_sweep", sweep=idx)
                    break

                logger.info(f"Running sweep {idx}/{len(sweeps)}: dataset={dataset}, engine={engine}, scenario={scenario}")
                trace_event(ctx.results_dir, "sweep_started", job_id=job_id,
                             dataset=dataset, engine=engine, scenario=scenario,
                             sweep=idx, resolved_param_keys=sorted(ctx.params))

                if not self.config.seed_gcs_cache_files(dataset, ctx.params):
                    trace_event(ctx.results_dir, "sweep_failed", phase="seed_dataset_cache",
                                 error="Failed to seed required GCS cache files")
                    trace_event(run_root, "run_finished", status="failed",
                                 failed_phase="seed_dataset_cache", sweep=idx)
                    return {
                        'status': 'failed',
                        'error': f'Failed to seed required GCS cache files for sweep {idx}.',
                    }

                if ctx.dataset_config and not self.config.download_dataset_files(dataset, ctx.params):
                    trace_event(ctx.results_dir, "sweep_failed", phase="download_dataset",
                                 error="Failed to download dataset files")
                    trace_event(run_root, "run_finished", status="failed",
                                 failed_phase="download_dataset", sweep=idx)
                    return {'status': 'failed', 'error': f'Failed to download dataset files for sweep {idx}.'}

                logger.info(f"Checking cluster health for {engine}...")
                cluster_healthy = self._check_cluster_health(ctx.target_host)
                trace_event(ctx.results_dir, "cluster_health_checked", healthy=cluster_healthy)
                if not cluster_healthy:
                    trace_event(ctx.results_dir, "sweep_failed", phase="cluster_health",
                                 error="Cluster is not ready")
                    all_results.append({
                        'status': 'failed', 'error': f'Cluster {ctx.target_host} is not ready.',
                        'exit_code': -1, 'sweep_index': idx, 'sweep_params': ctx.sweep_params,
                        'results_dir': str(ctx.results_dir),
                    })
                    continue

                if enable_metrics:
                    self._start_metrics_collection(engine, ctx.target_host, ctx.results_dir, dataset, scenario, idx)
                else:
                    trace_event(ctx.results_dir, "metrics_collection_skipped", reason="disabled_for_run")
                if enable_profiling:
                    self._start_profiling(engine, ctx.results_dir, profiling_duration)

                self._clear_benchmark_logs()
                cmd = self._build_osb_command(ctx, scenario, dataset, engine)
                logger.info(f"Command: {' '.join(cmd)}")
                trace_event(ctx.results_dir, "benchmark_process_starting",
                             executable=cmd[0], procedure=scenario,
                             workload_path=ctx.workload_path,
                             workload_params_file=str(ctx.results_dir / "workload-params.json"))

                start_time   = datetime.utcnow()
                stats_before = _fetch_node_stats(ctx.target_host)
                result       = None

                with self._log_level_override(log_level):
                    try:
                        with RunTrace(ctx.results_dir, job_id=job_id, dataset=dataset,
                                      engine=engine, scenario=scenario, sweep=idx) as trace:
                            with trace.span("benchmark_process", procedure=scenario):
                                proc = self._launch_process(cmd, env, active_key, cancel_event)
                                result = self._poll_until_done(proc, cmd, job_id, active_key, ctx.target_host, cancel_event, engine=engine, results_dir=ctx.results_dir)
                            trace.event("benchmark_process_finished",
                                        exit_code=result.returncode if result else None)
                    finally:
                        end_time = datetime.utcnow()
                        self._save_server_stats(ctx.results_dir, start_time, end_time, ctx.target_host, stats_before)
                        self._stop_metrics_collection()
                        self._stop_profiling()

                duration = (end_time - start_time).total_seconds()

                if result is None:
                    trace_event(ctx.results_dir, "sweep_failed", phase="benchmark_process",
                                 error="Process failed to initialize")
                    all_results.append({
                        'status': 'failed', 'error': 'Process failed to initialize',
                        'sweep_index': idx, 'sweep_params': ctx.sweep_params,
                        'results_dir': str(ctx.results_dir),
                    })
                    continue

                self._download_artifacts(result.stdout, ctx.results_dir, ctx, stderr=result.stderr)
                trace_event(ctx.results_dir, "artifacts_collected",
                             test_run=(ctx.results_dir / "test_run.json").exists(),
                             benchmark_log=(ctx.results_dir / "benchmark.log").exists(),
                             metrics_file=(ctx.results_dir / "k8s_metrics.json").exists())

                is_cancelled = (cancel_event and cancel_event.is_set()) or result.returncode == -9
                # Snapshot from this sweep's resolved RunContext, so telemetry
                # uses the same index name and output directory as the OSB run.
                if result.returncode == 0 and not is_cancelled:
                    index_name = ctx.params.get('target_index_name') or ctx.params.get('index')
                    if index_name:
                        _save_index_snapshot(engine, ctx.target_host, index_name, ctx.results_dir)

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
                trace_event(ctx.results_dir, "sweep_finished",
                             status="cancelled" if is_cancelled else ("completed" if result.returncode == 0 else "failed"),
                             exit_code=result.returncode, duration_seconds=duration)

                if is_cancelled:
                    _kill_proc_group(proc)
                    break

            summary = self._aggregate_results(all_results, job_id)
            trace_event(run_root, "run_finished", status=summary.get("status"),
                         total_sweeps=summary.get("total_sweeps"),
                         failed_sweeps=summary.get("failed_sweeps"),
                         cancelled_sweeps=summary.get("cancelled_sweeps"))
            return summary

        except Exception as e:
            logger.error(f"Benchmark execution error: {e}", exc_info=True)
            trace_job_id = job_id if job_id is not None else str(uuid.uuid4())
            trace_event(self.results_dir / trace_job_id, "run_failed", phase="benchmark_execution",
                         error=str(e))
            return {'status': 'error', 'error': str(e)}

    # ------------------------------------------------------------------
    # Ingest helpers
    # ------------------------------------------------------------------

    def _get_doc_count(self, target_host: str, index: str) -> Optional[int]:
        """Return the doc count for *index* via GET /{index}/_count, or None on error."""
        try:
            url  = f"{_base_url(target_host)}/{index}/_count"
            resp = requests.get(url, **_request_kwargs(15))
            resp.raise_for_status()
            return resp.json().get("count")
        except Exception as e:
            logger.warning(f"Could not fetch _count for [{index}]: {e}")
            return None

    def _refresh_index(self, target_host: str, index: str) -> None:
        """POST /{index}/_refresh so the doc count is current before verification."""
        try:
            url  = f"{_base_url(target_host)}/{index}/_refresh"
            resp = requests.post(url, **_request_kwargs(30))
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Could not refresh [{index}]: {e}")

    def _wait_for_stable_count(
        self,
        target_host: str,
        index: str,
        expected: int,
        stable_for: int = 3,
        poll_interval: int = 10,
        timeout: int = 300,
    ) -> Optional[int]:
        """Refresh repeatedly until the doc count either reaches *expected* or
        stabilises (does not change for *stable_for* consecutive polls).

        Large indices with ``refresh_interval: -1`` may still be flushing
        segments after OSB exits — a single refresh call returns as soon as the
        refresh *starts*, not when all buffered docs are visible.  Polling until
        the count stops moving gives an accurate final count before we decide
        whether to retry the ingest.

        Returns the last observed count, or None if every poll failed.
        """
        deadline    = time.monotonic() + timeout
        last_count  = None
        stable_runs = 0

        while time.monotonic() < deadline:
            self._refresh_index(target_host, index)
            count = self._get_doc_count(target_host, index)

            if count is None:
                time.sleep(poll_interval)
                continue

            if count >= expected:
                logger.info(f"[{index}] doc count reached {count}/{expected} — done.")
                return count

            if count == last_count:
                stable_runs += 1
                logger.info(
                    f"[{index}] doc count stable at {count}/{expected} "
                    f"({stable_runs}/{stable_for} stable polls)"
                )
                if stable_runs >= stable_for:
                    logger.info(f"[{index}] count stabilised at {count} — stopping poll.")
                    return count
            else:
                if last_count is not None:
                    logger.info(
                        f"[{index}] doc count still growing: {last_count} → {count} "
                        f"(need {expected})"
                    )
                stable_runs = 0

            last_count = count
            time.sleep(poll_interval)

        logger.warning(
            f"[{index}] _wait_for_stable_count timed out after {timeout}s "
            f"— last count: {last_count}"
        )
        return last_count

    def run_ingest(
        self,
        dataset: str,
        engine: str,
        job_id: Optional[str] = None,
        enable_profiling: bool = False,
        profiling_duration: int = 60,
        enable_metrics: bool = True,
        workload_params: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None,
        log_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run the bulk-ingest-data test procedure and verify the resulting doc count
        matches ``target_index_num_vectors``.

        On a count mismatch the index is deleted and re-created, then the full
        ingest is retried up to ``ingest_max_attempts`` times (default 3).
        Each attempt's OSB result is stored so timing data is never lost.

        A pre-flight refresh + count is performed first: if the index already
        has the expected number of docs the ingest is skipped entirely.

        Parameters
        ----------
        workload_params : dict, optional
            Must contain ``target_index_num_vectors`` and ``target_index_name``
            for doc-count verification to be active.  If either is absent,
            the method falls back to a plain single-attempt ingest.
        """
        if job_id is None:
            job_id = str(uuid.uuid4())

        trace_dir = self.results_dir / job_id
        trace_event(trace_dir, "ingest_started", job_id=job_id, dataset=dataset,
                     engine=engine, metrics_enabled=enable_metrics)

        wp           = workload_params or {}
        expected     = wp.get("target_index_num_vectors")
        index        = wp.get("target_index_name")
        max_attempts = wp.get("ingest_max_attempts", 3)
        target_host  = self._resolve_target_host(engine)

        # No verification params → plain ingest, no retry logic needed
        if not expected or not index:
            logger.info("target_index_num_vectors / target_index_name not set — skipping doc-count verification")
            trace_event(trace_dir, "ingest_verification_unavailable",
                         reason="target_index_num_vectors or target_index_name missing")
            result = self.run_benchmark(
                dataset, engine, scenario="bulk-ingest-data",
                job_id=job_id, enable_profiling=enable_profiling,
                profiling_duration=profiling_duration,
                enable_metrics=enable_metrics, workload_params=workload_params,
                cancel_event=cancel_event, log_level=log_level,
            )
            trace_event(trace_dir, "ingest_finished", status=result.get("status"),
                         verification="unavailable", results_dir=result.get("results_dir"))
            return result

        # ----------------------------------------------------------------
        # Pre-flight check: wait for stable count — skip ingest if already complete.
        # Exception: parquet-format datasets generate their queries/ground-truth file
        # as a side-effect of bulk-ingest-data, so we must always run ingest for them
        # even when the index already contains the expected number of documents.
        # ----------------------------------------------------------------
        dataset_format = self.config.get_dataset_config(dataset).get('format', '')
        pre_count = self._wait_for_stable_count(target_host, index, expected)
        trace_event(trace_dir, "ingest_preflight_checked", index=index,
                     expected_docs=expected, actual_docs=pre_count,
                     dataset_format=dataset_format)
        if pre_count is not None and pre_count >= expected and dataset_format != 'parquet':
            logger.info(
                f"Index [{index}] already has {pre_count}/{expected} docs — skipping ingest."
            )
            trace_event(trace_dir, "ingest_skipped", reason="index_already_complete",
                         expected_docs=expected, actual_docs=pre_count)
            trace_event(trace_dir, "metrics_collection_skipped",
                         reason="ingest_skipped_because_index_already_complete")
            trace_event(trace_dir, "ingest_finished", status="completed", skipped=True)
            return {
                "status":          "completed",
                "ingest_attempts": 0,
                "expected_docs":   expected,
                "actual_docs":     pre_count,
                "all_attempts":    [],
                "skipped":         True,
            }
        if pre_count is not None and pre_count >= expected and dataset_format == 'parquet':
            logger.info(
                f"Index [{index}] already has {pre_count}/{expected} docs but dataset format is "
                f"'parquet' — running bulk-ingest-data to ensure queries/ground-truth file exists."
            )

        all_attempt_results = []

        for attempt in range(1, max_attempts + 1):
            if cancel_event and cancel_event.is_set():
                trace_event(trace_dir, "ingest_cancelled", before_attempt=attempt)
                break

            logger.info(f"Ingest attempt {attempt}/{max_attempts}: dataset={dataset} engine={engine} index={index}")
            trace_event(trace_dir, "ingest_attempt_started", attempt=attempt,
                         max_attempts=max_attempts, index=index)

            # On retry: wipe and re-create the index for a clean slate
            if attempt > 1:
                logger.info(f"Re-creating index [{index}] before retry attempt {attempt}")
                create_result = self.run_benchmark(
                    dataset, engine, scenario="create-index",
                    job_id=f"{job_id}/create-{attempt}",
                    enable_profiling=False, enable_metrics=False,
                    workload_params=workload_params,
                    cancel_event=cancel_event, log_level=log_level,
                )
                if create_result.get("status") != "completed":
                    logger.error(f"create-index failed on attempt {attempt}: {create_result.get('error')}")
                    trace_event(trace_dir, "retry_index_creation_failed", attempt=attempt,
                                 error=create_result.get("error"),
                                 results_dir=create_result.get("results_dir"))
                    all_attempt_results.append(create_result)
                    break

            ingest_result = self.run_benchmark(
                dataset, engine, scenario="bulk-ingest-data",
                job_id=f"{job_id}/ingest-{attempt}",
                enable_profiling=enable_profiling,
                profiling_duration=profiling_duration,
                enable_metrics=enable_metrics,
                workload_params=workload_params,
                cancel_event=cancel_event, log_level=log_level,
            )
            all_attempt_results.append(ingest_result)
            trace_event(trace_dir, "ingest_attempt_benchmark_finished", attempt=attempt,
                         status=ingest_result.get("status"),
                         results_dir=ingest_result.get("results_dir"),
                         error=ingest_result.get("error"))

            if ingest_result.get("status") != "completed":
                logger.error(f"bulk-ingest-data failed on attempt {attempt}: {ingest_result.get('error')}")
                break

            # Wait for the count to stabilise before deciding whether to retry
            actual = self._wait_for_stable_count(target_host, index, expected)
            trace_event(trace_dir, "ingest_attempt_count_checked", attempt=attempt,
                         expected_docs=expected, actual_docs=actual)
            if actual is None:
                logger.warning(f"Could not verify doc count on attempt {attempt} — accepting ingest result")
                break

            shortfall = expected - actual
            if shortfall <= 0:
                logger.info(
                    f"Doc count verified: {actual}/{expected} docs in [{index}] "
                    f"after attempt {attempt}."
                )
                break

            logger.warning(
                f"Doc count mismatch after attempt {attempt}: "
                f"expected {expected}, got {actual} (shortfall {shortfall}). "
                + ("Retrying..." if attempt < max_attempts else "Max attempts reached — ingest incomplete.")
            )

        # Final stable count to determine true outcome.
        # OSB exits 0 even on a partial ingest (it ingested what it could), so
        # we must derive status from the doc count, not the OSB exit code.
        final_actual = self._wait_for_stable_count(target_host, index, expected)
        last         = all_attempt_results[-1] if all_attempt_results else {}

        if final_actual is not None and final_actual >= expected:
            final_status = "completed"
        elif last.get("status") in ("cancelled",):
            final_status = "cancelled"
        else:
            final_status = "failed"
            logger.error(
                f"Ingest failed after {len(all_attempt_results)} attempt(s): "
                f"expected {expected} docs in [{index}], got {final_actual}."
            )

        trace_event(trace_dir, "ingest_finished", status=final_status,
                     attempts=len(all_attempt_results), expected_docs=expected,
                     actual_docs=final_actual)
        return {
            **last,
            "status":          final_status,
            "ingest_attempts": len(all_attempt_results),
            "expected_docs":   expected,
            "actual_docs":     final_actual,
            "all_attempts":    all_attempt_results,
        }

    def _resolve_target_host(self, engine: str) -> str:
        """Return the target host for *engine* from config."""
        return self.config.get_target_host(engine)
