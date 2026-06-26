#!/usr/bin/env python3
"""Cloud-native benchmark runner - executes opensearch-benchmark directly"""
import subprocess
import logging
import os
import json
import requests
import threading
import time
from requests.auth import HTTPBasicAuth
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import signal
from config_loader import ConfigLoader
from k8s_metrics_collector import K8sMetricsCollector


def _kill_proc_group(proc: subprocess.Popen) -> None:
    """
    Immediately obliterates all benchmark engine footprints across the container 
    using an uncatchable SIGKILL broad sweep.
    """

    # Broad patterns to catch the engine wrapper and the underlying actor layers
    try:
        for i in range(15):
            # Check if it's still running
            subprocess.run(["pkill", "-9", "-f", "opensearch-benchmark"])
            time.sleep(1) # Wait 100ms for the OS to process
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

    def run_benchmark(
        self,
        dataset: str,
        engine: str,
        scenario: str = 'search',
        job_id: Optional[str] = None,
        enable_profiling: bool = True,
        enable_metrics: bool = True,
        workload_params: Optional[Dict[str, Any]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> Dict[str, Any]:
        """
        Execute a benchmark using opensearch-benchmark CLI with cross-worker tracking support
        """
        try:
            # Generate job_id if not provided
            if job_id is None:
                import uuid
                job_id = str(uuid.uuid4())
            
            # Ensure the workload repo is up to date before every run so
            # workload.json changes (e.g. new corpus entries) are picked up.
            workloads_dir = self.config.workloads_dir
            self.config._git_pull_repo(workloads_dir, 'opensearch-benchmark-workloads')

            # Get configuration
            target_host = self.config.get_target_host(engine)
            workload_path = self.config.get_workload_path(dataset)
            
            # Get dataset configuration
            dataset_config = self.config.get_dataset_config(dataset)
            
            # Build base params from: common_params + engine_params
            base_params = {}
            
            # 1. Start with common_params (shared across all scenarios)
            common_params = dataset_config.get('common_params', {})
            if common_params:
                base_params.update(common_params)
                logger.info(f"Loaded common params: {list(common_params.keys())}")
            
            # 2. Add engine-specific params
            engine_params_config = dataset_config.get('engine_params', {})
            if engine_params_config and engine in engine_params_config:
                base_params.update(engine_params_config[engine])
                logger.info(f"Loaded engine params for {engine}: {list(engine_params_config[engine].keys())}")
            
            # Get the specific procedure configuration
            procedures = self.config.get_test_procedures(dataset)
            procedure_config = None
            parameter_sweeps = []
            has_sweeps = False
            
            for proc in procedures:
                if isinstance(proc, dict):
                    proc_name = proc.get('name')
                    if proc_name == scenario:
                        procedure_config = proc
                        # Found matching procedure, check for parameter sweeps
                        sweeps = proc.get('parameter_sweeps', [])
                        if sweeps:
                            parameter_sweeps = sweeps
                            has_sweeps = True
                            logger.info(f"Found {len(parameter_sweeps)} parameter sweeps for procedure '{scenario}'")
                        break
            
            # 3. Add procedure's base params (scenario-specific)
            procedure_base_params = {}
            if procedure_config:
                procedure_base_params = procedure_config.get('params', {}).copy()
                if procedure_base_params:
                    logger.info(f"Loaded procedure base params: {list(procedure_base_params.keys())}")
                
                # 4. Add procedure-level engine-specific params (e.g., method_name for index creation)
                procedure_engine_params = procedure_config.get('engine_params', {})
                if procedure_engine_params and engine in procedure_engine_params:
                    proc_engine_specific = procedure_engine_params[engine].copy()
                    procedure_base_params.update(proc_engine_specific)
                    logger.info(f"Loaded procedure engine params for {engine}: {list(procedure_engine_params[engine].keys())}")
            
            # If no parameter sweeps, run once with all base params + runtime params
            if not parameter_sweeps:
                # Merge: base_params + procedure_base_params + runtime params
                merged_params = base_params.copy()
                merged_params.update(procedure_base_params)
                if workload_params:
                    logger.info(f"Merged with runtime params: {list(workload_params.keys())}")

                final_params = self.config.resolve_workload_params(
                    dataset,
                    merged_params,
                    workload_params
                )
                parameter_sweeps = [{'params': final_params}] if final_params else [{}]
            
            # Run benchmark for each parameter sweep
            all_results = []
            for sweep_idx, sweep in enumerate(parameter_sweeps, 1):
                # Check for cancellation before starting each sweep
                if cancel_event and cancel_event.is_set():
                    logger.info(f"Job {job_id}: cancellation requested, skipping sweep {sweep_idx}/{len(parameter_sweeps)}")
                    break
                logger.info(f"Running parameter sweep {sweep_idx}/{len(parameter_sweeps)}")
                
                # Merge in order: base_params + procedure_base_params + sweep params + runtime params
                merged_params = base_params.copy()
                merged_params.update(procedure_base_params)
                
                sweep_params = sweep.get('params', {})
                if sweep_params:
                    merged_params.update(sweep_params)
                    logger.info(f"Sweep params: {list(sweep_params.keys())}")
                if workload_params:
                    logger.info(f"Runtime params: {list(workload_params.keys())}")

                final_params = self.config.resolve_workload_params(
                    dataset,
                    merged_params,
                    workload_params
                )
                
                # Download dataset files for THIS sweep's corpus_size and k value
                if dataset_config:
                    if not self.config.download_dataset_files(dataset, final_params):
                        return {
                            'status': 'failed',
                            'error': f'Failed to download dataset files for sweep {sweep_idx}. Check logs for details.'
                        }
            
                # Check cluster health before starting
                logger.info(f"Checking cluster health for {engine}...")
                cluster_ready = self._check_cluster_health(target_host)
                if not cluster_ready:
                    sweep_result = {
                        'status': 'failed',
                        'error': f'Cluster {target_host} is not ready. Please check cluster status.',
                        'exit_code': -1,
                        'sweep_index': sweep_idx,
                        'sweep_params': sweep_params
                    }
                    all_results.append(sweep_result)
                    continue
                
                if '/' in job_id:
                    if has_sweeps:
                        sweep_results_dir = self.results_dir / job_id / f"sweep-{sweep_idx}"
                    else:
                        sweep_results_dir = self.results_dir / job_id
                else:
                    if has_sweeps:
                        sweep_results_dir = self.results_dir / job_id / scenario / f"sweep-{sweep_idx}"
                    else:
                        sweep_results_dir = self.results_dir / job_id / scenario
                sweep_results_dir.mkdir(parents=True, exist_ok=True)
                
                if enable_metrics:
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
                
                self._clear_benchmark_logs()
                
                user_tags = {
                    'dataset': dataset,
                    'engine': engine,
                    'num_vectors': final_params.get('target_index_num_vectors'),
                    'dimension': dataset_config.get('dimension'),
                    'space_type': dataset_config.get('space_type'),
                    'query_k': final_params.get('query_k'),
                    'ef_search': final_params.get('ef_search'),
                    'search_clients': final_params.get('search_clients'),
                    'method_name': final_params.get('method_name')
                }
                user_tags = {k: v for k, v in user_tags.items() if v is not None}
                user_tags_str = ','.join([f"{k}:{v}" for k, v in user_tags.items()])
                
                client_timeout = final_params.pop('client_timeout', 300)
                cmd = [
                    'opensearch-benchmark',
                    'run',
                    '--workload-path', workload_path,
                    '--target-hosts', target_host,
                    '--client-options', f'timeout:{client_timeout},use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin',
                    '--test-procedure', scenario,
                    '--kill-running-processes',
                    f'--user-tag={user_tags_str}'
                ]
                
                params_file = sweep_results_dir / 'workload-params.json'
                with open(params_file, 'w') as f:
                    json.dump(final_params, f, indent=2)
                
                logger.info(f"Workload params written to: {params_file}")
                logger.info(f"Workload params content:\n{json.dumps(final_params, indent=2)}")
                logger.info(f"User tags: {user_tags_str}")
                
                # Pass the file path to opensearch-benchmark
                cmd.extend(['--workload-params', str(params_file)])
                
                env = os.environ.copy()
                env['TERM'] = 'dumb'
                env['BENCHMARK_HOME'] = '/datasets/opensearch-benchmark'
                
                logger.info(f"Executing benchmark sweep {sweep_idx}/{len(parameter_sweeps)}: dataset={dataset}, engine={engine}, scenario={scenario}")
                logger.info(f"Command: {' '.join(cmd)}")
                logger.info(f"Target host: {target_host}")
                logger.info(f"Workload path: {workload_path}")
                
                # Start metrics collection in background thread if enabled
                if self.metrics_collector:
                    logger.info("📊 Starting metrics collection in background...")
                    metrics_collector = self.metrics_collector
                    scenario_name = f"{dataset}-{scenario}-sweep{sweep_idx}"

                    def collect_metrics():
                        try:
                            metrics_collector.start_collection(scenario_name=scenario_name, interval=10, duration=None)
                            metrics_collector.save_metrics(scenario_name)
                        except Exception as e:
                            logger.error(f"Error in metrics collection thread: {e}")
                    
                    self.metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
                    self.metrics_thread.start()
                
                start_time = datetime.utcnow()
                stats_before = _fetch_node_stats(target_host)
                proc = None
                result = None
                active_key = job_id.split('/')[0] if '/' in job_id else job_id
                
                # Cross-worker Gunicorn Tracking Variables
                run_dir = Path("/workspace/run")
                run_dir.mkdir(parents=True, exist_ok=True)
                pgid_file = run_dir / f"job_{active_key}.pgid"

                try:
                    # Launch process group
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=env,
                        start_new_session=True  # Breaks into its own PGID
                    )
                    
                    # Track PGID right away for cross-Gunicorn worker access
                    pgid = os.getpgid(proc.pid)
                    pgid_file.write_text(str(pgid))
                    
                    with self._active_lock:
                        self._active[active_key] = (proc, cancel_event)
                    
                    # Timeout tracking (6 hours = 21600 seconds)
                    timeout_limit = 21600
                    poll_interval = 1.0
                    elapsed = 0.0
                    
                    # NON-BLOCKING POLLING LOOP: Frees thread to inspect cancel_event status
                    while proc.poll() is None:
                        if cancel_event and cancel_event.is_set():
                            logger.info(f"Job {job_id}: Cancel payload flag caught inside runner. Evicting process tree.")
                            _kill_proc_group(proc)
                            break
                        
                        if elapsed >= timeout_limit:
                            logger.warning(f"Job {job_id} hit hard 6-hour execution limit. Timeout triggered.")
                            _kill_proc_group(proc)
                            break
                            
                        time.sleep(poll_interval)
                        elapsed += poll_interval
                    
                    # Drain execution pipelines natively safely
                    stdout, stderr = proc.communicate()
                    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
                    
                finally:
                    # CRITICAL CROSS-WORKER CLEANUP: Ensure artifact is deleted
                    if pgid_file.exists():
                        try:
                            pgid_file.unlink()
                        except Exception:
                            pass

                    with self._active_lock:
                        self._active.pop(active_key, None)
                        
                    end_time = datetime.utcnow()
                    stats_after = _fetch_node_stats(target_host)
                    if stats_before and stats_after:
                        try:
                            server_stats = {
                                'captured_at_start': start_time.isoformat(),
                                'captured_at_end': end_time.isoformat(),
                                'node_deltas': _diff_node_stats(stats_before, stats_after),
                                'snapshots': {'before': stats_before, 'after': stats_after}
                            }
                            with open(sweep_results_dir / 'server_stats.json', 'w') as f:
                                json.dump(server_stats, f, indent=2)
                        except Exception as e:
                            logger.warning(f"Failed to save server_stats: {e}")

                    if self.metrics_collector and self.metrics_thread:
                        logger.info("📊 Stopping metrics collection...")
                        try:
                            self.metrics_collector.stop_collection()
                            self.metrics_thread.join(timeout=30)
                        except Exception as e:
                            logger.error(f"Error saving metrics: {e}")
                        finally:
                            self.metrics_thread = None

                duration = (end_time - start_time).total_seconds()

                if result is None:
                    all_results.append({
                        'status': 'failed',
                        'error': 'Process exited or failed to initialize output pipelines',
                        'sweep_index': sweep_idx,
                        'sweep_params': sweep_params,
                    })
                    continue

                self._download_artifacts(result.stdout, sweep_results_dir)
                
                # Check explicitly if it was killed by us (SIGKILL / exit code -9)
                is_cancelled = (cancel_event and cancel_event.is_set()) or result.returncode == -9
                
                sweep_result = {
                    'status': 'cancelled' if is_cancelled else ('completed' if result.returncode == 0 else 'failed'),
                    'exit_code': result.returncode,
                    'duration_seconds': duration,
                    'started_at': start_time.isoformat(),
                    'completed_at': end_time.isoformat(),
                    'results_dir': str(sweep_results_dir),
                    'sweep_index': sweep_idx,
                    'sweep_params': sweep_params,
                    'command': ' '.join(cmd),
                    'stdout_tail': result.stdout[-5000:] if result.stdout else '',
                    'stderr_tail': result.stderr[-5000:] if result.stderr else ''
                }
                
                all_results.append(sweep_result)
                
                # If this sweep was cancelled, break out of subsequent sweeps immediately
                if is_cancelled:
                    _kill_proc_group(proc)
                    break
            
            # Return aggregated results
            total_duration = sum(r.get('duration_seconds', 0) for r in all_results)
            failed_sweeps = [r for r in all_results if r.get('status') == 'failed']
            cancelled_sweeps = [r for r in all_results if r.get('status') == 'cancelled']
            
            if cancelled_sweeps:
                overall_status = 'cancelled'
            elif failed_sweeps:
                overall_status = 'partial_failure' if len(failed_sweeps) < len(all_results) else 'failed'
            else:
                overall_status = 'completed'

            return {
                'status': overall_status,
                'total_sweeps': len(all_results),
                'successful_sweeps': len(all_results) - len(failed_sweeps) - len(cancelled_sweeps),
                'failed_sweeps': len(failed_sweeps),
                'cancelled_sweeps': len(cancelled_sweeps),
                'total_duration_seconds': total_duration,
                'results_dir': str(self.results_dir / job_id),
                'sweep_results': all_results
            }
            
        except Exception as e:
            logger.error(f"Benchmark execution error: {e}", exc_info=True)
            return {
                'status': 'error',
                'error': str(e)
            }
    
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
        
        # If using param_files (legacy), check if file exists
        if engine in param_files:
            try:
                params_file = self.config.get_workload_params_file(dataset, engine)
                if not Path(params_file).exists():
                    return f"Workload params file not found: {params_file}"
            except Exception as e:
                return f"Error getting params file: {e}"
        
        # Check if workload path exists
        workload_path = self.config.get_workload_path(dataset)
        if not Path(workload_path).exists():
            return f"Workload path not found: {workload_path}"
        
        return None  # Valid
    
    def _check_cluster_health(self, target_host: str) -> bool:
        """Check if OpenSearch cluster is healthy and ready"""
        try:
            url = f"https://{target_host}/_cluster/health"
            response = requests.get(
                url,
                auth=HTTPBasicAuth('admin', 'admin'),
                verify=False,
                timeout=10
            )
            
            if response.status_code == 200:
                health = response.json()
                status = health.get('status')
                logger.info(f"Cluster status: {status}, nodes: {health.get('number_of_nodes')}")
                return status in ['green', 'yellow']
            else:
                logger.error(f"Cluster health check failed: HTTP {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error checking cluster health: {e}")
            return False
    
    def _clear_benchmark_logs(self):
        """Clear the benchmark.log file before starting a new benchmark run."""
        BENCHMARK_HOME = "/datasets/opensearch-benchmark"
        log_path = f"{BENCHMARK_HOME}/.osb/logs/benchmark.log"
        
        try:
            # Truncate the log file to zero bytes
            with open(log_path, 'w') as f:
                f.truncate(0)
            logger.info(f"Cleared benchmark.log")
        except FileNotFoundError:
            # Log file doesn't exist yet, that's fine
            logger.info(f"benchmark.log doesn't exist yet, will be created on first run")
        except Exception as e:
            logger.warning(f"Could not clear benchmark.log: {e}")
    
    def _download_artifacts(self, console_output: str, target_dir: Path):
        """
        Download benchmark artifacts from opensearch-benchmark home directory.
        Extracts test_run.json and benchmark.log from the benchmark execution.
        """
        import re
        
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
        
        # Download the benchmark log
        log_path = f"{BENCHMARK_HOME}/.osb/logs/benchmark.log"
        try:
            with open(log_path, 'r') as f:
                log_content = f.read()
            
            (target_dir / "benchmark.log").write_text(log_content, encoding="utf-8")
            logger.info(f"Downloaded benchmark.log")
        except FileNotFoundError:
            logger.warning(f"benchmark.log not found at {log_path}")
        except Exception as e:
            logger.error(f"Error downloading benchmark.log: {e}")

# Made with Bob

    
