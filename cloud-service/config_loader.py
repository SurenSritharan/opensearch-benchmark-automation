#!/usr/bin/env python3
"""Configuration loader for cloud-native benchmark service"""
import yaml
from pathlib import Path
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and parses dataset and workload configurations"""
    
    def __init__(self, workspace_dir: str = '/workspace'):
        self.workspace_dir = Path(workspace_dir)
        self.config_dir = self.workspace_dir / 'config'
        self.workloads_dir = self.workspace_dir / 'workloads'
        self.datasets_config = self._load_datasets_config()
    
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
        
        # Return absolute path
        param_file = param_files[engine]
        return str(self.workspace_dir / param_file)
    
    def get_workload_path(self, dataset_name: str) -> str:
        """Get the workload directory path for a dataset"""
        dataset_config = self.get_dataset_config(dataset_name)
        workload = dataset_config.get('workload', 'vectorsearch')
        return str(self.workloads_dir / workload)
    
    def get_target_host(self, engine: str) -> str:
        """Get the OpenSearch cluster endpoint for an engine"""
        # Kubernetes service DNS format
        namespace = f"os-{engine}"
        return f"opensearch-cluster.{namespace}.svc.cluster.local:9200"
    
    def get_test_procedures(self, dataset_name: str) -> List[str]:
        """Get available test procedures for a dataset"""
        dataset_config = self.get_dataset_config(dataset_name)
        
        # Get from config or return defaults
        procedures = dataset_config.get('test_procedures', [])
        if not procedures:
            # Default procedures
            procedures = ['search', 'ingest', 'search-and-ingest']
        
        return procedures

# Made with Bob
