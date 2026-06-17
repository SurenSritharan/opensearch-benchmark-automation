#!/usr/bin/env python3
"""
Data collection module for benchmark results.
Handles scanning directories and extracting metrics from test results.
Supports multiple scenarios of the same type.
"""
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from .common import ENGINES, get_scenario_type


class DataCollector:
    """Collects benchmark data from results directory."""
    
    def __init__(self, results_dir: Path, dataset_name: str):
        """
        Initialize data collector.
        
        Args:
            results_dir: Path to results directory
            dataset_name: Name of the dataset being benchmarked
        """
        self.results_dir = Path(results_dir)
        self.dataset_name = dataset_name
        self.engines = ENGINES
    
    def extract_dataset_name(self) -> str:
        """Extract dataset name from directory structure."""
        # Look for directories like msmarco-faiss, cohere-faiss, etc.
        for engine in self.engines:
            for item in self.results_dir.iterdir():
                if item.is_dir() and item.name.endswith(f"-{engine}"):
                    # Extract dataset name (everything before the last hyphen)
                    return item.name.rsplit('-', 1)[0]
        return "unknown"
    
    def discover_scenarios(self) -> Dict[str, Any]:
        """Discover all scenarios by scanning the results directory."""
        scenarios = {}
        
        for engine in self.engines:
            engine_dir = self.results_dir / f"{self.dataset_name}-{engine}"
            if not engine_dir.exists():
                continue
            
            # Scan for scenario directories
            for scenario_dir in engine_dir.iterdir():
                if not scenario_dir.is_dir() or not scenario_dir.name.startswith('scenario-'):
                    continue
                
                scenario_name = scenario_dir.name
                
                # Check if it has test_run.json (direct scenario) OR crash_error.log (failed scenario)
                test_file = scenario_dir / "test_run.json"
                crash_file = scenario_dir / "crash_error.log"
                
                if test_file.exists() or crash_file.exists():
                    if scenario_name not in scenarios:
                        scenarios[scenario_name] = {
                            'engines': [],
                            'type': get_scenario_type(scenario_name),
                            'has_sweeps': False
                        }
                    scenarios[scenario_name]['engines'].append(engine)
                
                # Check for sweep subdirectories
                sweep_dirs = [d for d in scenario_dir.iterdir() 
                             if d.is_dir() and d.name.startswith('sweep-')]
                if sweep_dirs:
                    if scenario_name not in scenarios:
                        scenarios[scenario_name] = {
                            'engines': [],
                            'type': get_scenario_type(scenario_name),
                            'has_sweeps': True,
                            'num_sweeps': len(sweep_dirs)
                        }
                    else:
                        scenarios[scenario_name]['has_sweeps'] = True
                        scenarios[scenario_name]['num_sweeps'] = len(sweep_dirs)
                    
                    if engine not in scenarios[scenario_name]['engines']:
                        scenarios[scenario_name]['engines'].append(engine)
        
        return scenarios
    
    def _collect_sweep_data(self, sweep_dir: Path, sweep_num: int, 
                           scenario_type: str) -> Optional[Dict[str, Any]]:
        """Collect data from a single sweep directory."""
        test_file = sweep_dir / "test_run.json"
        crash_file = sweep_dir / "crash_error.log"
        
        # Check for crash/failure first
        if crash_file.exists() and not test_file.exists():
            try:
                with open(crash_file, 'r') as f:
                    crash_content = f.read()
            except Exception as e:
                crash_content = f"Could not read crash log: {e}"
            
            # Check if results.html exists (partial data before crash)
            results_html = sweep_dir / "results.html"
            has_results_page = results_html.exists()
            results_page_path = str(results_html.relative_to(self.results_dir)) if has_results_page else None
            
            return {
                'sweep': sweep_num,
                'failed': True,
                'error': 'Benchmark crashed',
                'crash_log': crash_content,
                'crash_log_path': str(crash_file.relative_to(self.results_dir)),
                'has_results_page': has_results_page,
                'results_page_path': results_page_path
            }
        
        if test_file.exists():
            try:
                with open(test_file) as f:
                    data = json.load(f)
                    workload_params = data.get("workload-params", {})
                    
                    # Check if results exist
                    if "results" not in data or "op_metrics" not in data.get("results", {}):
                        return None
                    
                    for op in data["results"]["op_metrics"]:
                        throughput = op.get("throughput", {}).get("mean")
                        latency = op.get("latency", {})
                        p50 = latency.get("50_0") if latency else None
                        p90 = latency.get("90_0") if latency else None
                        p99 = latency.get("99_0") if latency else None
                        p100 = latency.get("100_0") if latency else None
                        
                        # Extract recall metrics if available
                        recall_at_k = None
                        recall_at_1 = None
                        if "correctness_metrics" in data["results"]:
                            for correctness_op in data["results"]["correctness_metrics"]:
                                if correctness_op.get("operation") == op.get("operation"):
                                    recall_at_k_data = correctness_op.get("recall@k", {})
                                    recall_at_1_data = correctness_op.get("recall@1", {})
                                    if recall_at_k_data:
                                        recall_at_k = recall_at_k_data.get("mean")
                                    if recall_at_1_data:
                                        recall_at_1 = recall_at_1_data.get("mean")
                                    break
                        
                        if throughput and p50:
                            sweep_data = {
                                'sweep': sweep_num,
                                'throughput': throughput,
                                'p50': p50,
                                'p90': p90,
                                'p99': p99,
                                'p100': p100,
                                'config': workload_params
                            }
                            # Add recall metrics if available
                            if recall_at_k is not None:
                                sweep_data['recall_at_k'] = recall_at_k
                            if recall_at_1 is not None:
                                sweep_data['recall_at_1'] = recall_at_1
                            
                            return sweep_data
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                return {
                    'sweep': sweep_num,
                    'failed': True,
                    'error': f'Failed to parse results: {str(e)}'
                }
        
        return None
    
    def _collect_direct_scenario_data(self, scenario_dir: Path, 
                                     scenario_type: str) -> Optional[Dict[str, Any]]:
        """Collect data from a direct scenario (no sweeps)."""
        test_file = scenario_dir / "test_run.json"
        crash_file = scenario_dir / "crash_error.log"
        
        # Check for crash/failure
        if crash_file.exists() and not test_file.exists():
            return {'failed': True, 'error': 'Benchmark crashed'}
        
        if test_file.exists():
            try:
                with open(test_file) as f:
                    data = json.load(f)
                    
                    # Check if results exist
                    if "results" not in data or "op_metrics" not in data.get("results", {}):
                        return None
                    
                    if scenario_type == 'bulk_ingest':
                        metrics = data["results"]["op_metrics"][0]
                        return {
                            'throughput': metrics['throughput']['mean'],
                            'p50': metrics['latency']['50_0'],
                            'p99': metrics['latency']['99_0'],
                            'p100': metrics['latency']['100_0']
                        }
                    
                    elif scenario_type == 'search':
                        metrics = data["results"]["op_metrics"][0]
                        throughput = metrics.get("throughput", {}).get("mean")
                        latency = metrics.get("latency", {})
                        
                        if throughput and latency:
                            search_data = {
                                'throughput': throughput,
                                'p50': latency.get("50_0"),
                                'p90': latency.get("90_0"),
                                'p99': latency.get("99_0"),
                                'p100': latency.get("100_0"),
                                'config': data.get("workload-params", {})
                            }
                            
                            # Extract recall metrics if available
                            if "correctness_metrics" in data["results"]:
                                for correctness_op in data["results"]["correctness_metrics"]:
                                    if correctness_op.get("operation") == metrics.get("operation"):
                                        recall_at_k_data = correctness_op.get("recall@k", {})
                                        recall_at_1_data = correctness_op.get("recall@1", {})
                                        if recall_at_k_data and recall_at_k_data.get("mean") is not None:
                                            search_data['recall_at_k'] = recall_at_k_data.get("mean")
                                        if recall_at_1_data and recall_at_1_data.get("mean") is not None:
                                            search_data['recall_at_1'] = recall_at_1_data.get("mean")
                                        break
                            
                            return search_data
                    
                    elif scenario_type == 'force_merge':
                        # Check if op_metrics has force-merge operation
                        for op in data["results"]["op_metrics"]:
                            if "force-merge" in op.get("operation", "").lower():
                                return {'service_time': op.get('service_time', 0)}
                        
                        # If not in op_metrics, collect comprehensive metrics from results
                        results = data["results"]
                        return {
                            'total_time': results.get('total_time', 0) / 1000,  # Convert ms to seconds
                            'merge_time': results.get('merge_time', 0) / 1000,
                            'merge_count': results.get('merge_count', 0),
                            'merge_throttle_time': results.get('merge_throttle_time', 0) / 1000,
                            'refresh_time': results.get('refresh_time', 0) / 1000,
                            'refresh_count': results.get('refresh_count', 0),
                            'flush_time': results.get('flush_time', 0) / 1000,
                            'flush_count': results.get('flush_count', 0),
                            'merge_time_per_shard': results.get('merge_time_per_shard', {}),
                            'total_time_per_shard': results.get('total_time_per_shard', {})
                        }
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                return {'failed': True, 'error': str(e)}
        
        return None
    
    def collect_data(self) -> Dict[str, Any]:
        """
        Collect all benchmark data from results directory.
        
        Returns:
            Dictionary with structure:
            {
                'engine_name': {
                    'scenarios': {
                        'scenario-1-search': {
                            'type': 'search',
                            'has_sweeps': True/False,
                            'sweeps': [...] or None,
                            'data': {...} or None
                        }
                    }
                }
            }
        """
        all_data = {}
        scenarios = self.discover_scenarios()
        
        for engine in self.engines:
            all_data[engine] = {'scenarios': {}}
            engine_dir = self.results_dir / f"{self.dataset_name}-{engine}"
            
            if not engine_dir.exists():
                continue
            
            # Collect data for each discovered scenario
            for scenario_name, scenario_info in scenarios.items():
                if engine not in scenario_info['engines']:
                    continue
                
                scenario_type = scenario_info['type']
                scenario_dir = engine_dir / scenario_name
                
                # Initialize scenario entry
                all_data[engine]['scenarios'][scenario_name] = {
                    'type': scenario_type,
                    'has_sweeps': scenario_info.get('has_sweeps', False),
                    'sweeps': None,
                    'data': None
                }
                
                # Handle scenarios with sweeps
                if scenario_info.get('has_sweeps', False):
                    sweeps = []
                    for sweep_dir in sorted(scenario_dir.iterdir()):
                        if not sweep_dir.is_dir() or not sweep_dir.name.startswith('sweep-'):
                            continue
                        
                        sweep_num = int(sweep_dir.name.split('-')[1])
                        sweep_data = self._collect_sweep_data(sweep_dir, sweep_num, scenario_type)
                        
                        if sweep_data:
                            sweeps.append(sweep_data)
                    
                    all_data[engine]['scenarios'][scenario_name]['sweeps'] = sweeps
                
                # Handle direct scenarios (no sweeps)
                else:
                    scenario_data = self._collect_direct_scenario_data(scenario_dir, scenario_type)
                    if scenario_data:
                        all_data[engine]['scenarios'][scenario_name]['data'] = scenario_data
        
        return all_data

# Made with Bob
