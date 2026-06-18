#!/usr/bin/env python3
"""Configuration loader for cloud-native benchmark service"""
import yaml
import subprocess
from pathlib import Path
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and parses dataset and workload configurations"""
    
    def __init__(self, workspace_dir: str = '/workspace', workloads_dir: str = '/datasets/opensearch-benchmark-workloads'):
        self.workspace_dir = Path(workspace_dir)
        self.config_dir = self.workspace_dir / 'config'
        self.workloads_dir = Path(workloads_dir)
        self.datasets_config = self._load_datasets_config()
    
    def reload_config(self, git_pull: bool = True) -> Dict[str, Any]:
        """Reload the datasets configuration from disk, optionally pulling latest from git
        
        Args:
            git_pull: If True, perform git pull before reloading config
            
        Returns:
            Dict with reload status and dataset information
        """
        result = {
            'git_pull_attempted': git_pull,
            'git_pull_success': False,
            'git_output': None,
            'datasets_count': 0,
            'datasets': []
        }
        
        # Attempt git pull if requested
        if git_pull:
            try:
                logger.info("Pulling latest changes from git...")
                git_result = subprocess.run(
                    ['git', 'pull', 'origin', 'main'],
                    cwd=str(self.workspace_dir),
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                result['git_pull_success'] = git_result.returncode == 0
                result['git_output'] = git_result.stdout + git_result.stderr
                
                if git_result.returncode == 0:
                    logger.info(f"Git pull successful: {git_result.stdout.strip()}")
                else:
                    logger.warning(f"Git pull failed (exit {git_result.returncode}): {git_result.stderr}")
                    
            except subprocess.TimeoutExpired:
                logger.error("Git pull timed out after 30 seconds")
                result['git_output'] = "Git pull timed out"
            except Exception as e:
                logger.error(f"Git pull error: {e}")
                result['git_output'] = f"Error: {str(e)}"
        
        # Reload configuration from disk
        logger.info("Reloading configuration from disk...")
        self.datasets_config = self._load_datasets_config()
        result['datasets_count'] = len(self.datasets_config.get('datasets', {}))
        result['datasets'] = list(self.datasets_config.get('datasets', {}).keys())
        
        logger.info(f"Configuration reloaded: {result['datasets_count']} datasets")
        return result
    
    def _load_datasets_config(self) -> Dict[str, Any]:
        """Load datasets.yaml configuration"""
        config_file = self.config_dir / 'datasets.yaml'
        
        if not config_file.exists():
            logger.warning(f"Config file not found: {config_file}")
            return {'datasets': {}}
        
        try:
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
                logger.info(f"Loaded configuration for {len(config.get('datasets', {}))} datasets")
                return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return {'datasets': {}}
    
    def get_datasets(self) -> List[Dict[str, Any]]:
        """Get list of available datasets with their engines"""
        datasets = []
        
        for name, config in self.datasets_config.get('datasets', {}).items():
            param_files = config.get('param_files', {})
            engines = list(param_files.keys())
            
            datasets.append({
                'name': name,
                'engines': engines,
                'workload': config.get('workload', 'vectorsearch'),
                'dimension': config.get('dimension'),
                'space_type': config.get('space_type')
            })
        
        return datasets
    
    def get_dataset_config(self, dataset_name: str) -> Dict[str, Any]:
        """Get configuration for a specific dataset"""
        return self.datasets_config.get('datasets', {}).get(dataset_name, {})
    
    def get_workload_params_file(self, dataset_name: str, engine: str) -> str:
        """Get the workload parameters file path for a dataset and engine"""
        dataset_config = self.get_dataset_config(dataset_name)
        param_files = dataset_config.get('param_files', {})
        
        if engine not in param_files:
            raise ValueError(f"Engine '{engine}' not found for dataset '{dataset_name}'")
        
        param_file = param_files[engine]
        
        # Check if it's an absolute path
        if param_file.startswith('/'):
            return param_file
        
        # Get workload name and construct path in workloads directory
        workload = dataset_config.get('workload', 'vectorsearch')
        full_path = self.workloads_dir / workload / param_file
        logger.debug(f"Params file path for {dataset_name}/{engine}: {full_path}")
        return str(full_path)
    
    def get_workload_path(self, dataset_name: str) -> str:
        """Get the workload directory path for a dataset"""
        dataset_config = self.get_dataset_config(dataset_name)
        
        # Get workload name and construct path in workloads directory
        workload = dataset_config.get('workload', 'vectorsearch')
        full_path = self.workloads_dir / workload
        logger.debug(f"Workload path for {dataset_name}: {full_path}")
        return str(full_path)
    
    def get_target_host(self, engine: str) -> str:
        """Get the OpenSearch cluster endpoint for an engine"""
        # Kubernetes service DNS format
        namespace = f"os-{engine}"
        return f"opensearch-cluster.{namespace}.svc.cluster.local:9200"
    
    def get_test_procedures(self, dataset_name: str) -> List[Dict[str, Any]]:
        """Get available test procedures for a dataset with their configurations"""
        dataset_config = self.get_dataset_config(dataset_name)
        return dataset_config.get('test_procedures', [])
    
    def get_test_procedure_names(self, dataset_name: str) -> List[str]:
        """Get list of test procedure names for a dataset"""
        procedures = self.get_test_procedures(dataset_name)
        return [p.get('name') if isinstance(p, dict) else p for p in procedures]

# Made with Bob
