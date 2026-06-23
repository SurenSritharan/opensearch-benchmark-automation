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
    
    def _git_pull_repo(self, repo_path: Path, repo_name: str) -> Dict[str, Any]:
        """Pull latest changes from a git repository
        
        Args:
            repo_path: Path to the git repository
            repo_name: Human-readable name for logging
            
        Returns:
            Dict with pull status and output
        """
        result = {
            'success': False,
            'output': None
        }
        
        try:
            logger.info(f"Pulling latest changes from {repo_name}...")
            git_result = subprocess.run(
                ['git', 'pull', 'origin', 'main'],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=30
            )
            result['success'] = git_result.returncode == 0
            result['output'] = git_result.stdout + git_result.stderr
            
            if git_result.returncode == 0:
                logger.info(f"{repo_name} git pull successful: {git_result.stdout.strip()}")
            else:
                logger.warning(f"{repo_name} git pull failed (exit {git_result.returncode}): {git_result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error(f"{repo_name} git pull timed out after 30 seconds")
            result['output'] = "Git pull timed out"
        except Exception as e:
            logger.error(f"{repo_name} git pull error: {e}")
            result['output'] = f"Error: {str(e)}"
        
        return result
    
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
            'workloads_git_pull_success': False,
            'workloads_git_output': None,
            'datasets_count': 0,
            'datasets': []
        }
        
        # Attempt git pull if requested
        if git_pull:
            # Pull opensearch-benchmark-automation
            automation_result = self._git_pull_repo(self.workspace_dir, "opensearch-benchmark-automation")
            result['git_pull_success'] = automation_result['success']
            result['git_output'] = automation_result['output']
            
            # Pull opensearch-benchmark-workloads
            workloads_result = self._git_pull_repo(self.workloads_dir, "opensearch-benchmark-workloads")
            result['workloads_git_pull_success'] = workloads_result['success']
            result['workloads_git_output'] = workloads_result['output']
        
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
            # Support both param_files (legacy) and engine_params (new)
            param_files = config.get('param_files', {})
            engine_params = config.get('engine_params', {})
            
            # Get engines from either source
            engines = list(engine_params.keys()) if engine_params else list(param_files.keys())
            
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
    
    def get_workload_params(self, dataset_name: str, engine: str) -> Dict[str, Any]:
        """Get merged workload parameters for a dataset and engine
        
        Merges default_params with engine-specific params from engine_params.
        Falls back to reading param_files if engine_params not available (legacy support).
        Resolves template variables like {{corpus_size}}.
        """
        dataset_config = self.get_dataset_config(dataset_name)
        
        # Check if using new engine_params structure
        engine_params_config = dataset_config.get('engine_params', {})
        if engine_params_config and engine in engine_params_config:
            # Merge default_params with engine-specific params
            merged_params = dataset_config.get('default_params', {}).copy()
            merged_params.update(engine_params_config[engine])
            logger.debug(f"Using engine_params for {dataset_name}/{engine}")
            # Resolve template variables
            merged_params = self._resolve_template_vars(merged_params, dataset_config)
            return merged_params
        
        # Fallback: Try to read from param_files (legacy approach)
        param_files = dataset_config.get('param_files', {})
        if param_files and engine in param_files:
            param_file_path = param_files[engine]
            # Construct full path relative to workload directory
            workload = dataset_config.get('workload', 'vectorsearch')
            full_path = self.workspace_dir / 'workloads' / workload / param_file_path
            
            try:
                import json
                with open(full_path, 'r') as f:
                    params = json.load(f)
                    logger.info(f"Loaded params from file for {dataset_name}/{engine}: {param_file_path}")
                    return params
            except Exception as e:
                logger.error(f"Failed to read param file {full_path}: {e}")
                return {}
        
        # No params available
        logger.debug(f"No params found for {dataset_name}/{engine}")
        return {}
    
    def get_workload_params_file(self, dataset_name: str, engine: str) -> str:
        """Get the workload parameters file path for a dataset and engine (legacy)"""
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
    
    def _resolve_template_vars(self, params: Dict[str, Any], dataset_config: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively resolve template variables like {{corpus_size}} in parameters
        
        Args:
            params: Parameters dictionary that may contain template variables
            dataset_config: Dataset configuration containing variable values
            
        Returns:
            Parameters with resolved template variables
        """
        # Get template variable values from dataset config
        corpus_size = dataset_config.get('corpus_size', '1m')
        base_url = dataset_config.get('base_url', '')
        
        # Check if user provided num_vectors directly in params
        if 'num_vectors' in params or 'target_index_num_vectors' in params:
            num_vectors_raw = params.get('num_vectors', params.get('target_index_num_vectors'))
            
            # Parse num_vectors if it's a string (e.g., "1m", "10m")
            if isinstance(num_vectors_raw, str):
                num_vectors = self._parse_corpus_size(num_vectors_raw)
                logger.info(f"Parsed num_vectors '{num_vectors_raw}' to {num_vectors}")
            else:
                num_vectors = num_vectors_raw
            
            # Auto-select corpus_size from num_vectors
            corpus_size = self._select_corpus_size_from_num_vectors(num_vectors, dataset_config)
            logger.info(f"Auto-selected corpus_size '{corpus_size}' for num_vectors={num_vectors}")
        else:
            # Parse corpus_size to get num_vectors
            num_vectors = self._parse_corpus_size(corpus_size)
        
        # For cohere datasets, determine the appropriate source file
        # Use the smallest file that contains enough vectors
        source_file = self._select_source_file(corpus_size, dataset_config)
        
        # Corpus name for dynamic corpus definitions
        corpus_name = f"cohere-{corpus_size}" if 'cohere' in dataset_config.get('workload', '') else corpus_size
        
        # Get k value from params (for ground truth files)
        # Validate against supported_k_values
        k_value = params.get('query_k', params.get('k', 100))
        supported_k_values = dataset_config.get('supported_k_values', [100])
        if k_value not in supported_k_values:
            logger.warning(f"k={k_value} not in supported_k_values {supported_k_values}. Ground truth may not be available.")
        
        template_vars = {
            'corpus_size': corpus_size,
            'corpus_name': corpus_name,
            'num_vectors': num_vectors,
            'source_file': source_file,
            'base_url': base_url,
            'k': k_value
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
    
    def _parse_corpus_size(self, corpus_size) -> int:
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
    
    def _select_corpus_size_from_num_vectors(self, num_vectors: int, dataset_config: Dict[str, Any]) -> str:
        """Auto-select the closest corpus_size from supported_corpus_sizes based on num_vectors
        
        Args:
            num_vectors: Number of vectors requested
            dataset_config: Dataset configuration
            
        Returns:
            Closest corpus_size string (e.g., "10m", "50m")
        """
        supported_sizes = dataset_config.get('supported_corpus_sizes', [])
        
        if not supported_sizes:
            # Fallback: convert num_vectors to corpus_size format
            if num_vectors >= 1_000_000_000:
                return f"{num_vectors / 1_000_000_000:.0f}b"
            elif num_vectors >= 1_000_000:
                return f"{num_vectors / 1_000_000:.0f}m"
            elif num_vectors >= 1_000:
                return f"{num_vectors / 1_000:.0f}k"
            else:
                return str(num_vectors)
        
        # Find the closest supported corpus size
        closest_size = None
        min_diff = float('inf')
        
        for size_str in supported_sizes:
            size_num = self._parse_corpus_size(size_str)
            diff = abs(size_num - num_vectors)
            if diff < min_diff:
                min_diff = diff
                closest_size = size_str
        
        return closest_size if closest_size else "1m"
    
    def _select_source_file_for_count(self, requested_count: int) -> str:
        """Select the appropriate source file for a given document count
        
        Uses the smallest available file that contains enough vectors.
        
        Args:
            requested_count: Number of vectors needed
            
        Returns:
            Source file name (e.g., "documents-10m.hdf5.bz2")
        """
        # Available source files with their capacities
        available_files = [
            (1_000, "documents-1k.hdf5.bz2"),
            (100_000, "documents-100k.hdf5.bz2"),
            (1_000_000, "documents-1m.hdf5.bz2"),
            (10_000_000, "documents-10m.hdf5.bz2"),
        ]
        
        # Find the smallest file that has enough documents
        for capacity, filename in available_files:
            if capacity >= requested_count:
                logger.info(f"Selected source file '{filename}' (capacity {capacity:,}) for {requested_count:,} vectors")
                return filename
        
        # If requested count exceeds all available files, use the largest
        largest_file = available_files[-1][1]
        logger.warning(f"Requested {requested_count:,} vectors exceeds largest file, using: {largest_file}")
        return largest_file

# Made with Bob
