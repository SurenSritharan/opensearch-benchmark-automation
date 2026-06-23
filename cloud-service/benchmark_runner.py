#!/usr/bin/env python3
"""Cloud-native benchmark runner - executes opensearch-benchmark directly"""
import subprocess
import logging
import os
import json
import requests
import threading
from requests.auth import HTTPBasicAuth
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from config_loader import ConfigLoader
from k8s_metrics_collector import K8sMetricsCollector

logger = logging.getLogger(__name__)


def _parse_corpus_size(corpus_size) -> int:
    """Parse corpus size to number of vectors
    
    Supports both string formats ("1m", "10m", "1b") and raw numbers (1000000, 10000000)
    
    Args:
        corpus_size: Size string like "1k", "100k", "1m", "10m", "1b" OR raw number
        
    Returns:
        Number of vectors as integer
    """
    # If already an integer, return it directly
    if isinstance(corpus_size, int):
        return corpus_size
    
    corpus_size_str = str(corpus_size).lower().strip()
    
    try:
        # Try parsing as string format first
        if corpus_size_str.endswith('b'):
            return int(float(corpus_size_str[:-1]) * 1_000_000_000)
        elif corpus_size_str.endswith('m'):
            return int(float(corpus_size_str[:-1]) * 1_000_000)
        elif corpus_size_str.endswith('k'):
            return int(float(corpus_size_str[:-1]) * 1_000)
        else:
            # Try parsing as raw number
            return int(corpus_size_str)
    except (ValueError, AttributeError):
        logger.warning(f"Could not parse corpus_size '{corpus_size}', defaulting to 1000000")
        return 1000000


def _format_corpus_size(num_vectors) -> str:
    """Convert number of vectors to corpus size string format
    
    Args:
        num_vectors: Number of vectors (int or string)
        
    Returns:
        Formatted string like "5m", "10m", "1b"
    """
    # If already a string, return as-is
    if isinstance(num_vectors, str):
        return num_vectors
    
    # Convert number to string format
    if num_vectors >= 1_000_000_000:
        return f"{num_vectors / 1_000_000_000:.0f}b"
    elif num_vectors >= 1_000_000:
        return f"{num_vectors / 1_000_000:.0f}m"
    elif num_vectors >= 1_000:
        return f"{num_vectors / 1_000:.0f}k"
    else:
        return str(num_vectors)


def download_dataset_files(dataset_config: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> bool:
    """
    Download dataset files if they don't exist locally.
    Automatically calculates byte range for base vector files based on corpus_size.
    
    Args:
        dataset_config: Dataset configuration from datasets.yaml
        params: Optional parameters for template resolution (must include resolved corpus_size)
        
    Returns:
        True if all files are ready, False if download failed
    """
    # If params provided, update dataset_config's corpus_size
    if params:
        import copy
        dataset_config = copy.deepcopy(dataset_config)
        
        # Update corpus_size from params if provided
        if 'corpus_size' in params:
            dataset_config['corpus_size'] = params['corpus_size']
            logger.info(f"Using corpus_size from params: {params['corpus_size']}")
        elif 'num_vectors' in params:
            # Convert num_vectors to corpus_size format using helper function
            corpus_size = _format_corpus_size(params['num_vectors'])
            dataset_config['corpus_size'] = corpus_size
            logger.info(f"Converted num_vectors to corpus_size: {corpus_size}")
        
        # Resolve template variables in data_files
        # NOTE: Files with {{k}} are SKIPPED here and downloaded on-demand per sweep
        base_url = dataset_config.get('base_url', '')
        corpus_size = dataset_config.get('corpus_size', '1m')
        
        if 'data_files' in dataset_config:
            resolved_files = []
            for file_info in dataset_config['data_files']:
                # Check if this file has {{k}} template
                has_k_template = any('{{k}}' in str(v) for v in file_info.values())
                
                if has_k_template:
                    # Skip ground truth files - they'll be downloaded per-sweep based on actual k value
                    logger.info(f"Skipping ground truth file template (will download on-demand per sweep): {file_info.get('name', 'unknown')}")
                    continue
                
                # Regular file without {{k}} - download now
                resolved_file = {}
                for key, value in file_info.items():
                    if isinstance(value, str):
                        value = value.replace('{{base_url}}', base_url)
                        value = value.replace('{{corpus_size}}', corpus_size)
                    resolved_file[key] = value
                resolved_files.append(resolved_file)
            
            dataset_config['data_files'] = resolved_files
            logger.info(f"Resolved {len(resolved_files)} base data files (ground truth files handled per-sweep)")
    
    data_files = dataset_config.get('data_files', [])
    if not data_files:
        logger.info("No data files to download")
        return True
    
    data_dir = dataset_config.get('data_dir', '/datasets')
    data_dir_path = Path(data_dir)
    
    logger.info(f"Checking dataset files in {data_dir}...")
    
    # Create data directory if it doesn't exist
    data_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Get corpus size configuration for dynamic range calculation
    corpus_size = dataset_config.get('corpus_size', '1m')
    dimension = dataset_config.get('dimension', 1024)
    num_vectors = _parse_corpus_size(corpus_size)
    
    for file_info in data_files:
        file_name = file_info.get('name')
        file_url = file_info.get('url')
        file_range = file_info.get('range')
        
        # Templates should already be resolved by _resolve_dataset_config_templates
        if not file_name or not file_url:
            logger.warning(f"Skipping file with missing name or URL: {file_info}")
            continue
        
        target_file_name = file_name
        
        # For base vector files, append corpus_size to filename for clarity
        # e.g., cohere_msmarco_base.fvec → cohere_msmarco_base_5m.fvec
        if "_base.fvec" in file_name:
            target_file_name = file_name.replace('.fvec', f'_{corpus_size}.fvec')
            
            # fvec format: 4 bytes (dimension) + (dimension * 4 bytes per float)
            bytes_per_vector = 4 + (dimension * 4)
            byte_range_end = (num_vectors * bytes_per_vector) - 1
            file_range = f"0-{byte_range_end}"
            logger.info(f"Using corpus-specific filename: {target_file_name}")
            logger.info(f"Auto-calculated range for {target_file_name}: {file_range}")
            logger.info(f"  Corpus size: {corpus_size} ({num_vectors:,} vectors)")
            logger.info(f"  Bytes per vector: {bytes_per_vector}")
            logger.info(f"  Total bytes: {byte_range_end + 1:,} ({(byte_range_end + 1) / (1024**3):.2f} GB)")
        elif file_range:
            path_obj = Path(file_name)
            target_file_name = f"{path_obj.stem}.range-{file_range}{path_obj.suffix}"
        
        file_path = data_dir_path / target_file_name
        
        # Calculate expected file size from range
        expected_size = None
        if file_range:
            try:
                start, end = file_range.split('-')
                expected_size = int(end) - int(start) + 1
            except Exception as e:
                logger.warning(f"Could not parse range '{file_range}': {e}")
        
        if file_path.exists():
            current_size = file_path.stat().st_size
            if expected_size is None or current_size == expected_size:
                logger.info(f"✓ File already exists: {target_file_name} ({current_size / (1024**3):.2f} GB)")
                continue
            
            logger.warning(f"File size mismatch for existing file {target_file_name}")
            logger.warning(f"  Current: {current_size} bytes, Expected: {expected_size} bytes")
            logger.warning("  Preserving existing file and skipping overwrite")
            continue
        
        logger.info(f"File not found: {target_file_name}, will download")
        logger.info(f"📥 Downloading {target_file_name}...")
        logger.info(f"  URL: {file_url}")
        if file_range:
            if expected_size:
                size_gb = expected_size / (1024**3)
                logger.info(f"  Range: {file_range} ({size_gb:.2f} GB)")
            else:
                logger.info(f"  Range: {file_range}")
        
        try:
            # Use wget for downloading with range support
            wget_cmd = ['wget', '-q', '--show-progress', '-O', str(file_path)]
            
            # Add range header if specified
            if file_range:
                wget_cmd.extend(['--header', f'Range: bytes={file_range}'])
            
            wget_cmd.append(file_url)
            
            # Execute download
            result = subprocess.run(
                wget_cmd,
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout for large files
            )
            
            if result.returncode != 0:
                logger.error(f"Failed to download {target_file_name}")
                logger.error(f"STDERR: {result.stderr}")
                return False
            
            # Verify download completed
            if file_path.exists():
                downloaded_size = file_path.stat().st_size
                downloaded_gb = downloaded_size / (1024**3)
                logger.info(f"✓ Downloaded: {target_file_name} ({downloaded_gb:.2f} GB)")
            else:
                logger.error(f"Download completed but file not found: {file_path}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"Download timed out for {target_file_name}")
            return False
        except Exception as e:
            logger.error(f"Failed to download {target_file_name}: {e}")
            return False
    
    logger.info("✓ All dataset files are ready")
    return True


class BenchmarkRunner:
    """Executes opensearch-benchmark commands without kubectl dependencies"""
    
    def __init__(self, config_loader: ConfigLoader, results_dir: str = '/results'):
        self.config = config_loader
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_collector = None
        self.metrics_thread = None
    
    def run_benchmark(
        self,
        dataset: str,
        engine: str,
        scenario: str = 'search',
        job_id: Optional[str] = None,
        enable_profiling: bool = True,
        enable_metrics: bool = True,
        workload_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute a benchmark using opensearch-benchmark CLI
        
        Args:
            dataset: Dataset name from config/datasets.yaml
            engine: Engine name (jvector, faiss, lucene)
            scenario: Test procedure name
            job_id: Unique job identifier
            enable_profiling: Enable profiling (not implemented in cloud-native version)
            enable_metrics: Enable metrics collection (not implemented in cloud-native version)
            workload_params: Additional workload parameters for parameter sweeps
        
        Returns:
            Dictionary with execution results
        """
        try:
            # Generate job_id if not provided
            if job_id is None:
                import uuid
                job_id = str(uuid.uuid4())
            
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
            
            # Extract ground_truth_files mapping before removing it
            ground_truth_files_map = base_params.pop('ground_truth_files', None)
            
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
                # Resolve template variables in procedure params
                procedure_base_params = self._resolve_template_vars(procedure_base_params, dataset_config)
                if procedure_base_params:
                    logger.info(f"Loaded procedure base params: {list(procedure_base_params.keys())}")
                
                # 4. Add procedure-level engine-specific params (e.g., method_name for index creation)
                procedure_engine_params = procedure_config.get('engine_params', {})
                if procedure_engine_params and engine in procedure_engine_params:
                    proc_engine_specific = procedure_engine_params[engine].copy()
                    # Resolve template variables in procedure engine params
                    proc_engine_specific = self._resolve_template_vars(proc_engine_specific, dataset_config)
                    procedure_base_params.update(proc_engine_specific)
                    logger.info(f"Loaded procedure engine params for {engine}: {list(procedure_engine_params[engine].keys())}")
            
            # If no parameter sweeps, run once with all base params + runtime params
            if not parameter_sweeps:
                # Merge: base_params + procedure_base_params + runtime params
                merged_params = base_params.copy()
                merged_params.update(procedure_base_params)
                if workload_params:
                    merged_params.update(workload_params)
                    logger.info(f"Merged with runtime params: {list(workload_params.keys())}")
                
                final_params = merged_params if merged_params else None
                parameter_sweeps = [{'params': final_params}] if final_params else [{}]
            
            # Run benchmark for each parameter sweep
            all_results = []
            for sweep_idx, sweep in enumerate(parameter_sweeps, 1):
                logger.info(f"Running parameter sweep {sweep_idx}/{len(parameter_sweeps)}")
                
                # Merge in order: base_params + procedure_base_params + sweep params + runtime params
                final_params = base_params.copy()
                final_params.update(procedure_base_params)
                
                sweep_params = sweep.get('params', {})
                if sweep_params:
                    final_params.update(sweep_params)
                    logger.info(f"Sweep params: {list(sweep_params.keys())}")
                if workload_params:
                    final_params.update(workload_params)
                    logger.info(f"Runtime params: {list(workload_params.keys())}")
                
                # Download dataset files for THIS sweep's corpus_size and k value
                # Files are checked for existence, so no re-downloading if already present
                if dataset_config:
                    logger.info(f"Checking dataset files for sweep {sweep_idx} (corpus_size from params)...")
                    if not download_dataset_files(dataset_config, final_params):
                        return {
                            'status': 'failed',
                            'error': f'Failed to download dataset files for sweep {sweep_idx}. Check logs for details.'
                        }
                
                # Resolve ground_truth_file based on query_k value
                if ground_truth_files_map:
                    # Check if it's a dict (mapping k values to files) or a string (single file for all k)
                    if isinstance(ground_truth_files_map, dict):
                        # Dict mapping: look up based on query_k
                        if 'query_k' in final_params:
                            query_k = final_params.get('query_k')
                            # Convert to int if it's a string
                            if isinstance(query_k, str):
                                query_k = int(query_k)
                            
                            # Look up the ground truth file for this k value
                            if query_k in ground_truth_files_map:
                                ground_truth_file = ground_truth_files_map[query_k]
                                final_params['ground_truth_file'] = ground_truth_file
                                logger.info(f"Resolved ground_truth_file for k={query_k}: {ground_truth_file}")
                            else:
                                logger.warning(f"No ground truth file found for k={query_k}, available: {list(ground_truth_files_map.keys())}")
                                # Set empty string as fallback (benchmark will skip recall calculation)
                                final_params['ground_truth_file'] = ''
                        else:
                            logger.warning("ground_truth_files is a dict but query_k not found in params")
                            final_params['ground_truth_file'] = ''
                    elif isinstance(ground_truth_files_map, str):
                        # Single file for all k values
                        final_params['ground_truth_file'] = ground_truth_files_map
                        logger.info(f"Using default ground_truth_file for all k values: {ground_truth_files_map}")
                    else:
                        logger.warning(f"ground_truth_files has unexpected type: {type(ground_truth_files_map)}")
                        final_params['ground_truth_file'] = ''
            
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
                
                # Create results directory for this sweep
                # For batch jobs (job_id contains '/'), don't add scenario subdirectory
                # For regular jobs, add scenario subdirectory
                if '/' in job_id:
                    # Batch job - job_id already includes full path structure
                    if has_sweeps:
                        sweep_results_dir = self.results_dir / job_id / f"sweep-{sweep_idx}"
                    else:
                        sweep_results_dir = self.results_dir / job_id
                else:
                    # Regular job - add scenario subdirectory
                    if has_sweeps:
                        sweep_results_dir = self.results_dir / job_id / scenario / f"sweep-{sweep_idx}"
                    else:
                        sweep_results_dir = self.results_dir / job_id / scenario
                sweep_results_dir.mkdir(parents=True, exist_ok=True)
                
                # Initialize metrics collector if enabled
                if enable_metrics:
                    # Determine namespace from engine
                    namespace = f"os-{engine}"
                    logger.info(f"📊 Initializing metrics collection for namespace: {namespace}")
                    
                    try:
                        self.metrics_collector = K8sMetricsCollector(
                            namespace=namespace,
                            results_dir=sweep_results_dir,
                            enabled=True
                        )
                        logger.info("✓ Metrics collector initialized")
                    except Exception as e:
                        logger.warning(f"Failed to initialize metrics collector: {e}")
                        logger.warning("Continuing without metrics collection")
                        self.metrics_collector = None
                
                # Clear benchmark logs before starting this sweep
                self._clear_benchmark_logs()
                
                # Build user tags for metadata
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
                # Remove None values
                user_tags = {k: v for k, v in user_tags.items() if v is not None}
                user_tags_str = ','.join([f"{k}:{v}" for k, v in user_tags.items()])
                
                # Build opensearch-benchmark command
                cmd = [
                    'opensearch-benchmark',
                    'run',
                    '--workload-path', workload_path,
                    '--target-hosts', target_host,
                    '--client-options', 'timeout:300,use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin',
                    '--test-procedure', scenario,
                    '--kill-running-processes',  # Clean up any stuck processes
                    '--user-tags', user_tags_str  # Add metadata tags
                ]
                
                # Write workload params to a JSON file for this sweep
                params_file = sweep_results_dir / 'workload-params.json'
                with open(params_file, 'w') as f:
                    json.dump(final_params, f, indent=2)
                
                logger.info(f"Workload params written to: {params_file}")
                logger.info(f"Workload params content:\n{json.dumps(final_params, indent=2)}")
                logger.info(f"User tags: {user_tags_str}")
                
                # Pass the file path to opensearch-benchmark
                cmd.extend(['--workload-params', str(params_file)])
                
                # Set up environment
                env = os.environ.copy()
                env['TERM'] = 'dumb'  # Disable terminal features
                env['BENCHMARK_HOME'] = '/datasets/opensearch-benchmark'
                
                logger.info(f"Executing benchmark sweep {sweep_idx}/{len(parameter_sweeps)}: dataset={dataset}, engine={engine}, scenario={scenario}")
                logger.info(f"Command: {' '.join(cmd)}")
                logger.info(f"Target host: {target_host}")
                logger.info(f"Workload path: {workload_path}")
                
                # Start metrics collection in background thread if enabled
                if self.metrics_collector:
                    logger.info("📊 Starting metrics collection in background...")
                    
                    def collect_metrics():
                        try:
                            # Collect metrics continuously during benchmark
                            self.metrics_collector.start_collection(
                                scenario_name=f"{dataset}-{scenario}-sweep{sweep_idx}",
                                interval=10,  # Collect every 10 seconds
                                duration=None  # Run until benchmark completes
                            )
                        except Exception as e:
                            logger.error(f"Error in metrics collection thread: {e}")
                    
                    self.metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
                    self.metrics_thread.start()
                    logger.info("✓ Metrics collection started")
                
                # Execute benchmark in its own process group for proper signal handling
                # This allows us to kill the entire process tree when cancelling
                start_time = datetime.utcnow()
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=21600,  # 6 hour timeout
                    env=env,
                    start_new_session=True  # Create new process group
                )
                end_time = datetime.utcnow()
                
                # Stop metrics collection
                if self.metrics_collector and self.metrics_thread:
                    logger.info("📊 Stopping metrics collection...")
                    # The collector will stop when we save metrics
                    try:
                        self.metrics_collector.save_metrics(f"{dataset}-{scenario}-sweep{sweep_idx}")
                        logger.info("✓ Metrics saved")
                    except Exception as e:
                        logger.error(f"Error saving metrics: {e}")
                
                duration = (end_time - start_time).total_seconds()
                
                # Download artifacts (test_run.json and benchmark.log) to results directory
                self._download_artifacts(result.stdout, sweep_results_dir)
                
                # Parse results for this sweep
                sweep_result = {
                    'status': 'completed' if result.returncode == 0 else 'failed',
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
                
                if result.returncode != 0:
                    logger.error(f"Sweep {sweep_idx} failed with exit code {result.returncode}")
                    logger.error(f"STDERR:")
                    if result.stderr:
                        logger.error(result.stderr[-2000:])
                    else:
                        logger.error("(empty)")
                    logger.error(f"STDOUT (last 1000 chars):")
                    if result.stdout:
                        logger.error(result.stdout[-1000:])
                    else:
                        logger.error("(empty)")
                else:
                    logger.info(f"Sweep {sweep_idx} completed successfully in {duration:.1f}s")
                
                all_results.append(sweep_result)
            
            # Return aggregated results
            total_duration = sum(r.get('duration_seconds', 0) for r in all_results)
            failed_sweeps = [r for r in all_results if r.get('status') != 'completed']
            
            return {
                'status': 'completed' if not failed_sweeps else 'partial_failure',
                'total_sweeps': len(all_results),
                'successful_sweeps': len(all_results) - len(failed_sweeps),
                'failed_sweeps': len(failed_sweeps),
                'total_duration_seconds': total_duration,
                'results_dir': str(self.results_dir / job_id),
                'sweep_results': all_results
            }
            
        except subprocess.TimeoutExpired:
            logger.error(f"Benchmark timed out after 6 hours")
            return {
                'status': 'timeout',
                'error': 'Benchmark execution timed out after 6 hours'
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

    
    def _resolve_template_vars(self, params: Dict[str, Any], dataset_config: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve template variables like {{corpus_size}} in parameters
        
        Args:
            params: Parameters dictionary that may contain template variables
            dataset_config: Dataset configuration containing variable values
            
        Returns:
            Parameters with resolved template variables
        """
        # Get template variable values from dataset config
        corpus_size = dataset_config.get('corpus_size', '1m')
        corpus_size_map = dataset_config.get('corpus_size_map', {
            '1m': 1000000,
            '10m': 10000000,
            '100m': 100000000,
            '1b': 1000000000
        })
        
        # Get num_vectors from corpus_size_map
        num_vectors = corpus_size_map.get(corpus_size, 1000000)
        
        # For cohere datasets, determine the appropriate source file
        source_file = self._select_source_file(corpus_size, dataset_config)
        
        # Corpus name for dynamic corpus definitions
        corpus_name = f"cohere-{corpus_size}" if 'cohere' in dataset_config.get('workload', '') else corpus_size
        
        template_vars = {
            'corpus_size': corpus_size,
            'corpus_name': corpus_name,
            'num_vectors': num_vectors,
            'source_file': source_file
        }
        
        def resolve_value(value: Any) -> Any:
            """Recursively resolve template variables in a value"""
            if isinstance(value, str):
                # Replace {{variable}} patterns
                for var_name, var_value in template_vars.items():
                    pattern = f'{{{{{var_name}}}}}'
                    if pattern in value:
                        value = value.replace(pattern, str(var_value))
                return value
            elif isinstance(value, dict):
                return {k: resolve_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [resolve_value(item) for item in value]
            else:
                return value
        
        resolved_params = resolve_value(params)
        
        # Ensure we return a dict
        if not isinstance(resolved_params, dict):
            logger.warning(f"Template resolution did not return a dict, returning original params")
            return params
        
        # Add num_vectors if not already present (for bulk ingestion)
        if 'target_index_num_vectors' not in resolved_params and 'num_vectors' in template_vars:
            resolved_params['target_index_num_vectors'] = template_vars['num_vectors']
            logger.debug(f"Added target_index_num_vectors={template_vars['num_vectors']} based on corpus_size={corpus_size}")
        
        return resolved_params
    
    def _select_source_file(self, corpus_size: str, dataset_config: Dict[str, Any]) -> str:
        """Select the appropriate source file for a given corpus size
        
        For sizes that match exactly, use the corresponding file.
        For sizes in between, use the next larger file.
        
        Args:
            corpus_size: The requested corpus size (e.g., "5m")
            dataset_config: Dataset configuration
            
        Returns:
            Source file name (e.g., "documents-10m.hdf5.bz2")
        """
        source_file_map = dataset_config.get('source_file_map', {})
        corpus_size_map = dataset_config.get('corpus_size_map', {})
        
        # If exact match exists, use it
        if corpus_size in source_file_map:
            return source_file_map[corpus_size]
        
        # Otherwise, find the smallest file that contains enough vectors
        requested_count = corpus_size_map.get(corpus_size, 1000000)
        
        # Sort available sizes by document count
        available_sizes = sorted(
            [(size, corpus_size_map.get(size, 0), source_file_map.get(size, ''))
             for size in source_file_map.keys()],
            key=lambda x: x[1]
        )
        
        # Find the smallest file that has enough documents
        for size, count, file in available_sizes:
            if count >= requested_count:
                logger.info(f"Selected source file '{file}' (contains {count} docs) for corpus_size '{corpus_size}' ({requested_count} docs)")
                return file
        
        # Fallback to largest available file
        if available_sizes:
            largest_file = available_sizes[-1][2]
            logger.warning(f"No file large enough for {requested_count} docs, using largest: {largest_file}")
            return largest_file
        
        # Ultimate fallback
        return "documents-1m.hdf5.bz2"
