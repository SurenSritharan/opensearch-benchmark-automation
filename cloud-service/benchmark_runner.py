#!/usr/bin/env python3
"""Cloud-native benchmark runner - executes opensearch-benchmark directly"""
import subprocess
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from config_loader import ConfigLoader

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Executes opensearch-benchmark commands without kubectl dependencies"""
    
    def __init__(self, config_loader: ConfigLoader, results_dir: str = '/results', workloads_dir: str = '/datasets/opensearch-benchmark-workloads'):
        self.config = config_loader
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.workloads_dir = Path(workloads_dir)
    
    def update_workloads(self) -> bool:
        """
        Update workloads repository to latest version
        
        Returns:
            True if update successful, False otherwise
        """
        try:
            if not self.workloads_dir.exists():
                logger.warning(f"Workloads directory not found: {self.workloads_dir}")
                return False
            
            logger.info("Updating workloads repository...")
            result = subprocess.run(
                ['git', 'pull', 'origin', 'main'],
                cwd=str(self.workloads_dir),
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                logger.info(f"Workloads updated: {result.stdout.strip()}")
                return True
            else:
                logger.warning(f"Workloads update failed: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error updating workloads: {e}")
            return False
    
    def run_benchmark(
        self,
        dataset: str,
        engine: str,
        scenario: str = 'search',
        job_id: str = None,
        enable_profiling: bool = True,
        enable_metrics: bool = True
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
        
        Returns:
            Dictionary with execution results
        """
        try:
            # Update workloads to latest version
            self.update_workloads()
            
            # Get configuration
            target_host = self.config.get_target_host(engine)
            workload_path = self.config.get_workload_path(dataset)
            params_file = self.config.get_workload_params_file(dataset, engine)
            
            # Create results directory for this job
            job_results_dir = self.results_dir / job_id
            job_results_dir.mkdir(parents=True, exist_ok=True)
            results_file = job_results_dir / 'results.json'
            
            # Build opensearch-benchmark command
            cmd = [
                'opensearch-benchmark',
                'execute-test',
                '--workload-path', workload_path,
                '--target-hosts', target_host,
                '--client-options', 'timeout:300,use_ssl:true,verify_certs:false,basic_auth_user:admin,basic_auth_password:admin',
                '--test-procedure', scenario,
                '--workload-params', params_file,
                '--results-format', 'json',
                '--results-file', str(results_file),
                '--kill-running-processes'  # Clean up any stuck processes
            ]
            
            # Set up environment
            env = os.environ.copy()
            env['TERM'] = 'dumb'  # Disable terminal features
            env['BENCHMARK_HOME'] = '/datasets/opensearch-benchmark'
            
            logger.info(f"Executing benchmark: dataset={dataset}, engine={engine}, scenario={scenario}")
            logger.info(f"Command: {' '.join(cmd)}")
            logger.info(f"Target host: {target_host}")
            logger.info(f"Workload path: {workload_path}")
            logger.info(f"Params file: {params_file}")
            
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
                'results_file': str(results_file),
                'command': ' '.join(cmd),
                'stdout_tail': result.stdout[-5000:] if result.stdout else '',
                'stderr_tail': result.stderr[-5000:] if result.stderr else ''
            }
            
            if result.returncode != 0:
                logger.error(f"Benchmark failed with exit code {result.returncode}")
                logger.error(f"STDERR: {result.stderr[-1000:]}")
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
        param_files = dataset_config.get('param_files', {})
        if engine not in param_files:
            available = ', '.join(param_files.keys())
            return f"Engine '{engine}' not supported for dataset '{dataset}'. Available: {available}"
        
        # Check if params file exists
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

# Made with Bob
