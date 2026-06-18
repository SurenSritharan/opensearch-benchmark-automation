#!/usr/bin/env python3
"""Cloud-native benchmark runner - executes opensearch-benchmark directly"""
import subprocess
import logging
import os
import json
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from config_loader import ConfigLoader

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Executes opensearch-benchmark commands without kubectl dependencies"""
    
    def __init__(self, config_loader: ConfigLoader, results_dir: str = '/results'):
        self.config = config_loader
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
    
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
            
            # Get default params from dataset config
            # Get merged params (default_params + engine_params for this engine)
            merged_params = self.config.get_workload_params(dataset, engine)
            
            if merged_params:
                # Remove ground_truth_files - it's metadata, not a workload parameter
                merged_params.pop('ground_truth_files', None)
                logger.info(f"Loaded merged params for {engine}: {list(merged_params.keys())}")
            else:
                merged_params = {}
                logger.warning(f"No params found for {dataset}/{engine}")
            
            # Merge with runtime workload_params (these override everything)
            if workload_params:
                merged_params.update(workload_params)
                logger.info(f"Merged with runtime params: {list(workload_params.keys())}")
            
            # Use merged params for the benchmark
            final_params = merged_params if merged_params else None
            
            # Check cluster health before starting
            logger.info(f"Checking cluster health for {engine}...")
            cluster_ready = self._check_cluster_health(target_host)
            if not cluster_ready:
                return {
                    'status': 'failed',
                    'error': f'Cluster {target_host} is not ready. Please check cluster status.',
                    'exit_code': -1
                }
            
            # Create results directory for this job (opensearch-benchmark will save results here automatically)
            job_results_dir = self.results_dir / job_id
            job_results_dir.mkdir(parents=True, exist_ok=True)
            
            # Build opensearch-benchmark command
            cmd = [
                'opensearch-benchmark',
                'run',
                '--workload-path', workload_path,
                '--target-hosts', target_host,
                '--client-options', 'timeout:300,use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin',
                '--test-procedure', scenario,
                '--kill-running-processes'  # Clean up any stuck processes
            ]
            
            # Write workload params to a temporary JSON file for better debugging and complex type support
            params_file = None
            if final_params:
                # Create temp file in job results directory
                params_file = job_results_dir / 'workload-params.json'
                with open(params_file, 'w') as f:
                    json.dump(final_params, f, indent=2)
                
                logger.info(f"Workload params written to: {params_file}")
                logger.info(f"Workload params content:\n{json.dumps(final_params, indent=2)}")
                
                # Pass the file path to opensearch-benchmark
                cmd.extend(['--workload-params', str(params_file)])
            
            # Set up environment
            env = os.environ.copy()
            env['TERM'] = 'dumb'  # Disable terminal features
            env['BENCHMARK_HOME'] = '/datasets/opensearch-benchmark'
            
            logger.info(f"Executing benchmark: dataset={dataset}, engine={engine}, scenario={scenario}")
            logger.info(f"Command: {' '.join(cmd)}")
            logger.info(f"Target host: {target_host}")
            logger.info(f"Workload path: {workload_path}")
            
            # Execute benchmark
            start_time = datetime.utcnow()
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=21600,  # 6 hour timeout
                env=env
            )
            end_time = datetime.utcnow()
            
            duration = (end_time - start_time).total_seconds()
            
            # Parse results
            execution_result = {
                'status': 'completed' if result.returncode == 0 else 'failed',
                'exit_code': result.returncode,
                'duration_seconds': duration,
                'started_at': start_time.isoformat(),
                'completed_at': end_time.isoformat(),
                'results_dir': str(job_results_dir),
                'command': ' '.join(cmd),
                'stdout_tail': result.stdout[-5000:] if result.stdout else '',
                'stderr_tail': result.stderr[-5000:] if result.stderr else ''
            }
            
            if result.returncode != 0:
                logger.error(f"Benchmark failed with exit code {result.returncode}")
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
                logger.info(f"Benchmark completed successfully in {duration:.1f}s")
            
            return execution_result
            
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

# Made with Bob
