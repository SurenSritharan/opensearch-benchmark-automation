#!/usr/bin/env python3
"""
Dashboard generator for benchmark results.
Creates comprehensive comparison dashboards after benchmark execution.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional


class DashboardGenerator:
    """Generates HTML dashboards for benchmark results comparison."""
    
    def __init__(self, results_dir: Path):
        self.results_dir = Path(results_dir)
        self.engines = ["faiss", "jvector", "lucene"]
        self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.dataset_name = self._extract_dataset_name()
    
    def _extract_dataset_name(self) -> str:
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
                            'type': self._get_scenario_type(scenario_name),
                            'has_sweeps': False
                        }
                    scenarios[scenario_name]['engines'].append(engine)
                
                # Check for sweep subdirectories
                sweep_dirs = [d for d in scenario_dir.iterdir() if d.is_dir() and d.name.startswith('sweep-')]
                if sweep_dirs:
                    if scenario_name not in scenarios:
                        scenarios[scenario_name] = {
                            'engines': [],
                            'type': self._get_scenario_type(scenario_name),
                            'has_sweeps': True,
                            'num_sweeps': len(sweep_dirs)
                        }
                    else:
                        scenarios[scenario_name]['has_sweeps'] = True
                        scenarios[scenario_name]['num_sweeps'] = len(sweep_dirs)
                    
                    if engine not in scenarios[scenario_name]['engines']:
                        scenarios[scenario_name]['engines'].append(engine)
        
        return scenarios
    
    def _get_scenario_type(self, scenario_name: str) -> str:
        """Determine scenario type from directory name."""
        name_lower = scenario_name.lower()
        if 'index' in name_lower or 'create' in name_lower:
            return 'index'
        elif 'ingest' in name_lower or 'bulk' in name_lower:
            return 'bulk_ingest'
        elif 'merge' in name_lower:
            return 'force_merge'
        elif 'search' in name_lower:
            return 'search'
        else:
            return 'unknown'
    
    def collect_data(self) -> Dict[str, Any]:
        """Collect all benchmark data from results directory."""
        all_data = {}
        scenarios = self.discover_scenarios()
        
        for engine in self.engines:
            all_data[engine] = {}
            engine_dir = self.results_dir / f"{self.dataset_name}-{engine}"
            
            if not engine_dir.exists():
                continue
            
            # Collect data for each discovered scenario
            for scenario_name, scenario_info in scenarios.items():
                if engine not in scenario_info['engines']:
                    continue
                
                scenario_type = scenario_info['type']
                scenario_dir = engine_dir / scenario_name
                
                # Handle scenarios with sweeps
                if scenario_info.get('has_sweeps', False):
                    if scenario_type not in all_data[engine]:
                        all_data[engine][f'{scenario_type}_sweeps'] = []
                        all_data[engine][f'{scenario_type}_scenario_name'] = scenario_name  # Track scenario name
                    
                    for sweep_dir in sorted(scenario_dir.iterdir()):
                        if not sweep_dir.is_dir() or not sweep_dir.name.startswith('sweep-'):
                            continue
                        
                        test_file = sweep_dir / "test_run.json"
                        crash_file = sweep_dir / "crash_error.log"
                        sweep_num = int(sweep_dir.name.split('-')[1])
                        
                        # Check for crash/failure first
                        if crash_file.exists() and not test_file.exists():
                            # Read crash log content
                            try:
                                with open(crash_file, 'r') as f:
                                    crash_content = f.read()
                            except Exception as e:
                                crash_content = f"Could not read crash log: {e}"
                            
                            # Check if results.html exists (partial data before crash)
                            results_html = sweep_dir / "results.html"
                            has_results_page = results_html.exists()
                            results_page_path = str(results_html.relative_to(self.results_dir)) if has_results_page else None
                            
                            all_data[engine][f'{scenario_type}_sweeps'].append({
                                'sweep': sweep_num,
                                'failed': True,
                                'error': 'Benchmark crashed',
                                'crash_log': crash_content,
                                'crash_log_path': str(crash_file.relative_to(self.results_dir)),
                                'has_results_page': has_results_page,
                                'results_page_path': results_page_path
                            })
                            continue
                        
                        if test_file.exists():
                            try:
                                with open(test_file) as f:
                                    data = json.load(f)
                                    workload_params = data.get("workload-params", {})
                                    
                                    # Check if results exist
                                    if "results" not in data or "op_metrics" not in data.get("results", {}):
                                        continue
                                    
                                    for op in data["results"]["op_metrics"]:
                                        throughput = op.get("throughput", {}).get("mean")
                                        latency = op.get("latency", {})
                                        p50 = latency.get("50_0") if latency else None
                                        p90 = latency.get("90_0") if latency else None
                                        p99 = latency.get("99_0") if latency else None
                                        p100 = latency.get("100_0") if latency else None
                                        
                                        if throughput and p50:
                                            all_data[engine][f'{scenario_type}_sweeps'].append({
                                                'sweep': sweep_num,
                                                'throughput': throughput,
                                                'p50': p50,
                                                'p90': p90,
                                                'p99': p99,
                                                'p100': p100,
                                                'config': workload_params
                                            })
                            except (json.JSONDecodeError, KeyError, IndexError) as e:
                                # Mark as failed if we can't parse results
                                all_data[engine][f'{scenario_type}_sweeps'].append({
                                    'sweep': sweep_num,
                                    'failed': True,
                                    'error': f'Failed to parse results: {str(e)}'
                                })
                                print(f"Warning: Failed to parse {test_file}: {e}")
                                continue
                
                # Handle direct scenarios (no sweeps)
                else:
                    test_file = scenario_dir / "test_run.json"
                    crash_file = scenario_dir / "crash_error.log"
                    
                    # Check for crash/failure
                    if crash_file.exists() and not test_file.exists():
                        # Benchmark crashed - mark as failed
                        if scenario_type == 'bulk_ingest':
                            all_data[engine]['bulk_ingest'] = {'failed': True, 'error': 'Benchmark crashed'}
                        elif scenario_type == 'search':
                            all_data[engine]['search'] = {'failed': True, 'error': 'Benchmark crashed'}
                        elif scenario_type == 'force_merge':
                            all_data[engine]['force_merge'] = {'failed': True, 'error': 'Benchmark crashed'}
                        continue
                    
                    if test_file.exists():
                        try:
                            with open(test_file) as f:
                                data = json.load(f)
                                
                                # Check if results exist
                                if "results" not in data or "op_metrics" not in data.get("results", {}):
                                    continue
                                
                                if scenario_type == 'bulk_ingest':
                                    metrics = data["results"]["op_metrics"][0]
                                    all_data[engine]['bulk_ingest'] = {
                                        'throughput': metrics['throughput']['mean'],
                                        'p50': metrics['latency']['50_0'],
                                        'p99': metrics['latency']['99_0'],
                                        'p100': metrics['latency']['100_0']
                                    }
                                
                                elif scenario_type == 'search':
                                    # Handle search scenarios without sweeps
                                    metrics = data["results"]["op_metrics"][0]
                                    throughput = metrics.get("throughput", {}).get("mean")
                                    latency = metrics.get("latency", {})
                                    
                                    if throughput and latency:
                                        all_data[engine]['search'] = {
                                            'throughput': throughput,
                                            'p50': latency.get("50_0"),
                                            'p90': latency.get("90_0"),
                                            'p99': latency.get("99_0"),
                                            'p100': latency.get("100_0"),
                                            'config': data.get("workload-params", {})
                                        }
                                
                                elif scenario_type == 'force_merge':
                                    # Check if op_metrics has force-merge operation
                                    found_in_ops = False
                                    for op in data["results"]["op_metrics"]:
                                        if "force-merge" in op.get("operation", "").lower():
                                            all_data[engine]['force_merge'] = {
                                                'service_time': op.get('service_time', 0)
                                            }
                                            found_in_ops = True
                                            break
                                    
                                    # If not in op_metrics, collect comprehensive metrics from results
                                    if not found_in_ops:
                                        results = data["results"]
                                        all_data[engine]['force_merge'] = {
                                            'total_time': results.get('total_time', 0) / 1000,  # Convert ms to seconds
                                            'merge_time': results.get('merge_time', 0) / 1000,
                                            'merge_count': results.get('merge_count', 0),
                                            'merge_throttle_time': results.get('merge_throttle_time', 0) / 1000,
                                            'refresh_time': results.get('refresh_time', 0) / 1000,
                                            'refresh_count': results.get('refresh_count', 0),
                                            'flush_time': results.get('flush_time', 0) / 1000,
                                            'flush_count': results.get('flush_count', 0),
                                            # Per-shard metrics
                                            'merge_time_per_shard': results.get('merge_time_per_shard', {}),
                                            'total_time_per_shard': results.get('total_time_per_shard', {})
                                        }
                        except (json.JSONDecodeError, KeyError, IndexError) as e:
                            # Mark as failed if we can't parse results
                            if scenario_type == 'bulk_ingest':
                                all_data[engine]['bulk_ingest'] = {'failed': True, 'error': str(e)}
                            elif scenario_type == 'search':
                                all_data[engine]['search'] = {'failed': True, 'error': str(e)}
                            elif scenario_type == 'force_merge':
                                all_data[engine]['force_merge'] = {'failed': True, 'error': str(e)}
                            print(f"Warning: Failed to parse {test_file}: {e}")
                            continue
        
        return all_data
    
    def generate_main_dashboard(self, all_data: Dict[str, Any]) -> str:
        """Generate main index.html dashboard."""
        # Detect which scenarios have data
        bulk_ingest_available = any('bulk_ingest' in all_data.get(e, {}) for e in self.engines)
        search_available = any(
            len(all_data.get(e, {}).get('search_sweeps', [])) > 0 or 'search' in all_data.get(e, {})
            for e in self.engines
        )
        force_merge_available = any('force_merge' in all_data.get(e, {}) for e in self.engines)
        
        # Determine winners for available scenarios (exclude failed benchmarks)
        bulk_winner = None
        if bulk_ingest_available:
            successful_bulk = [e for e in self.engines
                             if 'bulk_ingest' in all_data.get(e, {})
                             and not all_data[e]['bulk_ingest'].get('failed', False)]
            if successful_bulk:
                bulk_winner = max(successful_bulk,
                                key=lambda e: all_data[e]['bulk_ingest']['throughput'], default=None)
        
        # Force merge winner: fastest total time (lowest is best)
        force_merge_winner = None
        if force_merge_available:
            successful_merge = [e for e in self.engines
                              if 'force_merge' in all_data.get(e, {})
                              and not all_data[e]['force_merge'].get('failed', False)
                              and 'total_time' in all_data[e]['force_merge']]
            if successful_merge:
                force_merge_winner = min(successful_merge,
                                       key=lambda e: all_data[e]['force_merge']['total_time'], default=None)
        
        search_winner = None
        if search_available:
            # Check for direct search results first
            search_engines = [e for e in self.engines if 'search' in all_data.get(e, {})]
            if search_engines:
                search_winner = max(search_engines,
                                  key=lambda e: all_data[e]['search']['throughput'], default=None)
        
        html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.dataset_name.upper()} Benchmark Results - Engine Comparison</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0a1929 0%, #1a2332 50%, #0f1419 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }}
        .container {{ max-width: 1800px; margin: 0 auto; }}
        .header {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.1) 0%, rgba(114, 46, 209, 0.1) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 40px;
            margin-bottom: 30px;
            backdrop-filter: blur(10px);
        }}
        .header h1 {{
            font-size: 42px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 50%, #eb2f96 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 12px;
        }}
        .header .subtitle {{ font-size: 18px; color: rgba(255, 255, 255, 0.7); margin-bottom: 15px; }}
        .header .timestamp {{ font-size: 14px; color: rgba(255, 255, 255, 0.5); }}
        .section {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.05) 0%, rgba(114, 46, 209, 0.05) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            cursor: pointer;
            transition: all 0.3s ease;
        }}
        .section:hover {{
            transform: translateY(-4px);
            box-shadow: 0 12px 40px rgba(24, 144, 255, 0.25);
            border-color: rgba(24, 144, 255, 0.4);
        }}
        .section-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 25px;
        }}
        .section-title {{
            font-size: 28px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .section-title span {{
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .section-icon {{ font-size: 32px; }}
        .view-comparison {{
            padding: 10px 20px;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            border: none;
            border-radius: 10px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .view-comparison:hover {{
            transform: scale(1.05);
            box-shadow: 0 4px 16px rgba(24, 144, 255, 0.4);
        }}
        .metrics-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 10px;
        }}
        .metrics-table thead th {{
            text-align: left;
            padding: 12px 16px;
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: rgba(255, 255, 255, 0.5);
            border-bottom: 1px solid rgba(24, 144, 255, 0.2);
        }}
        .metrics-table tbody tr {{
            background: rgba(24, 144, 255, 0.05);
            transition: all 0.2s ease;
            cursor: pointer;
        }}
        .metrics-table tbody tr:hover {{
            background: rgba(24, 144, 255, 0.12);
            transform: translateX(5px);
        }}
        .metrics-table tbody td {{
            padding: 16px;
            border-top: 1px solid rgba(24, 144, 255, 0.1);
            border-bottom: 1px solid rgba(24, 144, 255, 0.1);
        }}
        .metrics-table tbody td:first-child {{
            border-left: 1px solid rgba(24, 144, 255, 0.1);
            border-radius: 10px 0 0 10px;
        }}
        .metrics-table tbody td:last-child {{
            border-right: 1px solid rgba(24, 144, 255, 0.1);
            border-radius: 0 10px 10px 0;
        }}
        .engine-badge {{
            display: inline-block;
            padding: 6px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .engine-badge.faiss {{ background: linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%); color: white; }}
        .engine-badge.jvector {{ background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%); color: white; }}
        .engine-badge.lucene {{ background: linear-gradient(135deg, #1890ff 0%, #40a9ff 100%); color: white; }}
        .metric-value {{
            font-size: 18px;
            font-weight: 600;
            color: rgba(255, 255, 255, 0.9);
        }}
        .metric-value.winner {{
            color: #52c41a;
            font-weight: 700;
        }}
        .metric-value.winner::after {{
            content: ' 🏆';
            font-size: 14px;
        }}
        .metric-unit {{
            font-size: 12px;
            color: rgba(255, 255, 255, 0.5);
            margin-left: 4px;
        }}
        .sweep-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-top: 15px;
        }}
        .sweep-card {{
            background: rgba(24, 144, 255, 0.08);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 12px;
            padding: 16px;
            transition: all 0.2s ease;
            cursor: pointer;
        }}
        .sweep-card:hover {{
            background: rgba(24, 144, 255, 0.15);
            transform: translateY(-2px);
        }}
        .sweep-label {{
            font-size: 12px;
            font-weight: 600;
            color: rgba(255, 255, 255, 0.6);
            margin-bottom: 8px;
        }}
        .sweep-value {{
            font-size: 20px;
            font-weight: 700;
            color: rgba(255, 255, 255, 0.9);
        }}
        .status-badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 10px;
        }}
        .status-badge.complete {{
            background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%);
            color: white;
        }}
        .status-badge.incomplete {{
            background: linear-gradient(135deg, #ff9800 0%, #ffc107 100%);
            color: white;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 {self.dataset_name.upper()} Benchmark Results</h1>
            <div class="subtitle">Comprehensive Performance Analysis - FAISS vs JVector vs Lucene</div>
            <div class="timestamp">Generated: {self.timestamp}</div>
        </div>
'''
        
        # Bulk Ingest Section (only if data exists)
        if bulk_ingest_available:
            html += '''
        <div class="section" onclick="window.location.href='bulk-ingest-comparison.html'">
            <div class="section-header">
                <div class="section-title">
                    <span class="section-icon">📥</span>
                    <span>Bulk Ingest Performance</span>
                    <span class="status-badge complete">✓ Complete</span>
                </div>
                <button class="view-comparison" onclick="event.stopPropagation(); window.location.href='bulk-ingest-comparison.html'">
                    View Detailed Comparison →
                </button>
            </div>
            <table class="metrics-table">
                <thead>
                    <tr>
                        <th>Engine</th>
                        <th>Throughput</th>
                        <th>P50 Latency</th>
                        <th>P99 Latency</th>
                        <th>P100 Latency</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
'''
            
            for engine in self.engines:
                if 'bulk_ingest' in all_data.get(engine, {}):
                    d = all_data[engine]['bulk_ingest']
                    
                    # Check if benchmark failed
                    if d.get('failed'):
                        html += f'''
                    <tr onclick="event.stopPropagation(); window.location.href='{self.dataset_name}-{engine}/scenario-2-bulk-ingest/results.html'">
                        <td><span class="engine-badge {engine}">{engine.upper()}</span></td>
                        <td colspan="4"><span style="color: #ff4d4f; font-weight: 600;">❌ Benchmark Failed</span> <span style="color: rgba(255, 255, 255, 0.5); font-size: 12px;">({d.get('error', 'Unknown error')})</span></td>
                        <td><span style="color: #ff4d4f;">View Logs →</span></td>
                    </tr>
'''
                    else:
                        is_winner = engine == bulk_winner
                        winner_class = 'winner' if is_winner else ''
                        
                        html += f'''
                    <tr onclick="event.stopPropagation(); window.location.href='{self.dataset_name}-{engine}/scenario-2-bulk-ingest/results.html'">
                        <td><span class="engine-badge {engine}">{engine.upper()}</span></td>
                        <td><span class="metric-value {winner_class}">{d['throughput']:.2f}</span><span class="metric-unit">docs/s</span></td>
                        <td><span class="metric-value">{d['p50']:.2f}</span><span class="metric-unit">ms</span></td>
                        <td><span class="metric-value">{d['p99']:.2f}</span><span class="metric-unit">ms</span></td>
                        <td><span class="metric-value">{d['p100']:.2f}</span><span class="metric-unit">ms</span></td>
                        <td><span style="color: #1890ff;">View Results →</span></td>
                    </tr>
'''
            
            html += '''
                </tbody>
            </table>
        </div>
'''
        
        # Force Merge Section (only if data exists) - Runs after Bulk Ingest
        if force_merge_available:
            html += '''
        <div class="section">
            <div class="section-header">
                <div class="section-title">
                    <span class="section-icon">🔄</span>
                    <span>Force Merge Performance</span>
                    <span class="status-badge complete">✓ Complete</span>
                </div>
            </div>
            <table class="metrics-table">
                <thead>
                    <tr>
                        <th>Engine</th>
                        <th>Total Time</th>
                        <th>Merge Time</th>
                        <th>Merge Count</th>
                        <th>Throttle Time</th>
                        <th>Refresh Time</th>
                        <th>Flush Time</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
'''
            
            for engine in self.engines:
                if 'force_merge' in all_data.get(engine, {}):
                    d = all_data[engine]['force_merge']
                    if d.get('failed', False):
                        html += f'''
                    <tr style="background: rgba(255, 77, 79, 0.1);">
                        <td><span class="engine-badge {engine}">{engine.upper()}</span></td>
                        <td colspan="7"><span style="color: #ff4d4f;">❌ FAILED: {d.get('error', 'Unknown error')}</span></td>
                    </tr>
'''
                    else:
                        # Handle both old format (service_time) and new format (comprehensive metrics)
                        if 'total_time' in d:
                            is_winner = engine == force_merge_winner
                            winner_class = 'winner' if is_winner else ''
                            
                            html += f'''
                    <tr onclick="window.location.href='{self.dataset_name}-{engine}/scenario-3-force-merge/results.html'">
                        <td><span class="engine-badge {engine}">{engine.upper()}</span></td>
                        <td><span class="metric-value {winner_class}">{d['total_time']:.2f}</span><span class="metric-unit">s</span></td>
                        <td><span class="metric-value">{d['merge_time']:.2f}</span><span class="metric-unit">s</span></td>
                        <td><span class="metric-value">{d['merge_count']}</span></td>
                        <td><span class="metric-value">{d['merge_throttle_time']:.2f}</span><span class="metric-unit">s</span></td>
                        <td><span class="metric-value">{d['refresh_time']:.2f}</span><span class="metric-unit">s</span> <span style="color: rgba(255,255,255,0.5); font-size: 11px;">({d['refresh_count']} ops)</span></td>
                        <td><span class="metric-value">{d['flush_time']:.2f}</span><span class="metric-unit">s</span> <span style="color: rgba(255,255,255,0.5); font-size: 11px;">({d['flush_count']} ops)</span></td>
                        <td><span style="color: #1890ff;">View Results →</span></td>
                    </tr>
'''
                        else:
                            # Old format with just service_time
                            html += f'''
                    <tr onclick="window.location.href='{self.dataset_name}-{engine}/scenario-3-force-merge/results.html'">
                        <td><span class="engine-badge {engine}">{engine.upper()}</span></td>
                        <td colspan="6"><span class="metric-value">{d.get('service_time', 0):.2f}</span><span class="metric-unit">s</span></td>
                        <td><span style="color: #1890ff;">View Results →</span></td>
                    </tr>
'''
            
            html += '''
                </tbody>
            </table>
        </div>
'''
        
        # Search Section (only if data exists) - Runs after Force Merge
        if search_available:
            # Determine winners for each sweep number (highest throughput)
            sweep_winners = {}  # sweep_num -> engine
            for engine in self.engines:
                sweeps = all_data.get(engine, {}).get('search_sweeps', [])
                for sweep in sweeps:
                    if not sweep.get('failed', False) and 'throughput' in sweep:
                        sweep_num = sweep['sweep']
                        if sweep_num not in sweep_winners or sweep['throughput'] > sweep_winners[sweep_num][1]:
                            sweep_winners[sweep_num] = (engine, sweep['throughput'])
            
            html += '''
        <div class="section" onclick="window.location.href='search-comparison.html'">
            <div class="section-header">
                <div class="section-title">
                    <span class="section-icon">🔍</span>
                    <span>Search Performance (Parameter Sweeps)</span>
                    <span class="status-badge complete">✓ Complete</span>
                </div>
                <button class="view-comparison" onclick="event.stopPropagation(); window.location.href='search-comparison.html'">
                    View Detailed Comparison →
                </button>
            </div>
'''
            
            for engine in self.engines:
                sweeps = all_data.get(engine, {}).get('search_sweeps', [])
                scenario_name = all_data.get(engine, {}).get('search_scenario_name', 'scenario-4-search')
                if sweeps:
                    html += f'''
            <div style="margin-bottom: 20px;">
                <div style="margin-bottom: 12px;">
                    <span class="engine-badge {engine}">{engine.upper()}</span>
                    <span style="color: rgba(255, 255, 255, 0.6); margin-left: 10px; font-size: 14px;">
                        {len(sweeps)} parameter sweep(s)
                    </span>
                </div>
                <div class="sweep-grid">
'''
                    
                    for sweep in sweeps:
                        # Check if sweep failed
                        if sweep.get('failed', False):
                            # Escape crash log for JavaScript
                            crash_log = sweep.get('crash_log', 'No crash log available')
                            crash_log_escaped = crash_log.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
                            
                            # Check if results page exists (partial data)
                            has_results = sweep.get('has_results_page', False)
                            results_path = sweep.get('results_page_path', '')
                            
                            html += f'''
                    <div class="sweep-card" onclick="event.stopPropagation(); showCrashLog('{engine}', {sweep['sweep']}, '{crash_log_escaped}')" style="background: rgba(255, 77, 79, 0.1); border-color: rgba(255, 77, 79, 0.3); cursor: pointer;">
                        <div class="sweep-label" style="color: #ff4d4f;">❌ Sweep {sweep['sweep']}</div>
                        <div class="sweep-value" style="color: #ff4d4f; font-size: 13px;">FAILED</div>
                        <div style="font-size: 11px; color: #ff7875; margin-top: 4px;">
                            {sweep.get('error', 'Unknown error')}
                        </div>
                        <div style="font-size: 10px; color: rgba(255, 255, 255, 0.4); margin-top: 4px;">
                            Click to view crash log
                        </div>
'''
                            
                            # Add link to results page if it exists
                            if has_results:
                                html += f'''
                        <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255, 77, 79, 0.3);">
                            <a href="{results_path}" onclick="event.stopPropagation();" style="color: #1890ff; text-decoration: none; font-size: 11px; display: flex; align-items: center; gap: 4px;">
                                📊 View Partial Metrics →
                            </a>
                        </div>
'''
                            
                            html += '''
                    </div>
'''
                        else:
                            # Check if this sweep is the winner
                            sweep_num = sweep['sweep']
                            is_winner = sweep_num in sweep_winners and sweep_winners[sweep_num][0] == engine
                            winner_indicator = ' 🏆' if is_winner else ''
                            winner_style = 'color: #52c41a; font-weight: 700;' if is_winner else ''
                            
                            # Format sweep configuration for display
                            config_str = ""
                            if 'config' in sweep and sweep['config']:
                                config_items = []
                                for key, value in sweep['config'].items():
                                    # Include common search parameters
                                    if key in ['k', 'ef_search', 'num_candidates', 'rescore', 'query_k', 'query_count', 'query_clients']:
                                        config_items.append(f"{key}={value}")
                                if config_items:
                                    config_str = f"<div style='font-size: 10px; color: rgba(255, 255, 255, 0.4); margin-top: 2px;'>{', '.join(config_items)}</div>"
                            
                            html += f'''
                    <div class="sweep-card" onclick="event.stopPropagation(); window.location.href='{self.dataset_name}-{engine}/{scenario_name}/sweep-{sweep['sweep']}/results.html'">
                        <div class="sweep-label">Sweep {sweep['sweep']}</div>
                        <div class="sweep-value" style="{winner_style}">{sweep['throughput']:.1f}{winner_indicator} <span style="font-size: 12px; color: rgba(255, 255, 255, 0.5);">ops/s</span></div>
                        <div style="font-size: 11px; color: rgba(255, 255, 255, 0.5); margin-top: 4px;">
                            P50: {sweep['p50']:.1f}ms | P99: {sweep['p99']:.1f}ms
                        </div>
                        {config_str}
                    </div>
'''
                    
                    html += '''
                </div>
            </div>
'''
            
            html += '''
        </div>
'''
        
        html += '''
    </div>
    
    <!-- Crash Log Modal -->
    <div id="crashLogModal" style="
        display: none;
        position: fixed;
        z-index: 1000;
        left: 0;
        top: 0;
        width: 100%;
        height: 100%;
        overflow: auto;
        background-color: rgba(0,0,0,0.7);
    ">
        <div style="
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            margin: 5% auto;
            padding: 0;
            border: 1px solid rgba(24, 144, 255, 0.3);
            border-radius: 12px;
            width: 80%;
            max-width: 1000px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
        ">
            <div style="
                background: rgba(24, 144, 255, 0.1);
                padding: 20px;
                border-bottom: 1px solid rgba(24, 144, 255, 0.3);
                border-radius: 12px 12px 0 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
            ">
                <h2 id="crashLogTitle" style="margin: 0; color: #ff4d4f; font-size: 20px;">Crash Log</h2>
                <span id="closeModal" style="
                    color: rgba(255, 255, 255, 0.6);
                    font-size: 28px;
                    font-weight: bold;
                    cursor: pointer;
                    transition: color 0.3s;
                ">&times;</span>
            </div>
            <div style="padding: 20px;">
                <pre id="crashLogContent" style="
                    background: rgba(0, 0, 0, 0.3);
                    color: #ff7875;
                    padding: 20px;
                    border-radius: 8px;
                    overflow-x: auto;
                    max-height: 500px;
                    font-family: 'Courier New', monospace;
                    font-size: 12px;
                    line-height: 1.5;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                "></pre>
            </div>
        </div>
    </div>
    
    <script>
        // Function to show crash log modal
        function showCrashLog(engine, sweepNum, crashLog) {
            const modal = document.getElementById('crashLogModal');
            const title = document.getElementById('crashLogTitle');
            const content = document.getElementById('crashLogContent');
            
            title.textContent = `${engine.toUpperCase()} - Sweep ${sweepNum} - Crash Log`;
            content.textContent = crashLog;
            
            modal.style.display = 'block';
        }
        
        // Close modal when clicking X or outside
        document.getElementById('closeModal').onclick = function() {
            document.getElementById('crashLogModal').style.display = 'none';
        }
        
        window.onclick = function(event) {
            const modal = document.getElementById('crashLogModal');
            if (event.target == modal) {
                modal.style.display = 'none';
            }
        }
    </script>
</body>
</html>'''
        
        return html
    
    def generate_search_comparison(self, all_data: Dict[str, Any]) -> str:
        """Generate search-comparison.html with detailed search metrics."""
        # Normalize all search data to sweep format for consistent display
        search_engines = []
        for engine in self.engines:
            if 'search' in all_data.get(engine, {}):
                # Treat single search as "Sweep 1"
                search_data = all_data[engine]['search']
                scenario_name = all_data[engine].get('search_scenario_name', 'scenario-1-search')
                sweeps = [{
                    'sweep': 1,
                    'throughput': search_data['throughput'],
                    'p50': search_data['p50'],
                    'p99': search_data['p99'],
                    'p100': search_data.get('p100', 0),
                    'config': search_data.get('config', {})
                }]
                search_engines.append({
                    'name': engine,
                    'throughput': search_data['throughput'],
                    'p50': search_data['p50'],
                    'p99': search_data['p99'],
                    'sweeps': sweeps,
                    'scenario_name': scenario_name
                })
            elif 'search_sweeps' in all_data.get(engine, {}):
                sweeps = all_data[engine]['search_sweeps']
                scenario_name = all_data[engine].get('search_scenario_name', 'scenario-1-search')
                if sweeps:
                    # Separate successful and failed sweeps
                    successful_sweeps = [s for s in sweeps if not s.get('failed', False)]
                    failed_sweeps = [s for s in sweeps if s.get('failed', False)]
                    
                    # If we have at least one successful sweep, calculate averages
                    if successful_sweeps:
                        avg_throughput = sum(s['throughput'] for s in successful_sweeps) / len(successful_sweeps)
                        avg_p50 = sum(s['p50'] for s in successful_sweeps) / len(successful_sweeps)
                        avg_p99 = sum(s['p99'] for s in successful_sweeps) / len(successful_sweeps)
                        search_engines.append({
                            'name': engine,
                            'throughput': avg_throughput,
                            'p50': avg_p50,
                            'p99': avg_p99,
                            'sweeps': sweeps,  # Include all sweeps (successful and failed)
                            'scenario_name': scenario_name,
                            'has_failures': len(failed_sweeps) > 0
                        })
                    elif failed_sweeps:
                        # All sweeps failed - still add to show the failures
                        search_engines.append({
                            'name': engine,
                            'throughput': 0,
                            'p50': 0,
                            'p99': 0,
                            'sweeps': sweeps,
                            'scenario_name': scenario_name,
                            'all_failed': True
                        })
        
        if not search_engines:
            return ""
        
        winner = max(search_engines, key=lambda x: x['throughput'])
        
        html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Search Performance Comparison - {self.dataset_name.upper()}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0a1929 0%, #1a2332 50%, #0f1419 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }}
        .container {{ max-width: 1800px; margin: 0 auto; }}
        
        .breadcrumb {{
            margin-bottom: 20px;
            padding: 12px 20px;
            background: rgba(24, 144, 255, 0.05);
            border-radius: 10px;
            font-size: 14px;
        }}
        .breadcrumb a {{
            color: #1890ff;
            text-decoration: none;
            transition: color 0.2s;
        }}
        .breadcrumb a:hover {{ color: #40a9ff; }}
        .breadcrumb span {{ color: rgba(255, 255, 255, 0.5); margin: 0 8px; }}
        
        .header {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.1) 0%, rgba(114, 46, 209, 0.1) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 40px;
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 42px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 50%, #eb2f96 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 12px;
        }}
        .header .subtitle {{ font-size: 16px; color: rgba(255, 255, 255, 0.7); }}
        
        .summary-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .summary-card {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.08) 0%, rgba(114, 46, 209, 0.08) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 16px;
            padding: 24px;
            position: relative;
        }}
        .summary-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #1890ff, #722ed1, #eb2f96);
        }}
        .summary-card.winner::after {{
            content: '🏆 BEST';
            position: absolute;
            top: 12px;
            right: 12px;
            background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%);
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 700;
        }}
        .engine-badge {{
            display: inline-block;
            padding: 6px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            margin-bottom: 12px;
        }}
        .engine-badge.faiss {{ background: linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%); color: white; }}
        .engine-badge.jvector {{ background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%); color: white; }}
        .engine-badge.lucene {{ background: linear-gradient(135deg, #1890ff 0%, #40a9ff 100%); color: white; }}
        
        .metric-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid rgba(24, 144, 255, 0.1);
        }}
        .metric-row:last-child {{ border-bottom: none; }}
        .metric-label {{
            font-size: 13px;
            color: rgba(255, 255, 255, 0.6);
            font-weight: 600;
        }}
        .metric-value {{
            font-size: 20px;
            font-weight: 700;
            color: rgba(255, 255, 255, 0.9);
        }}
        
        .section-title {{
            font-size: 28px;
            font-weight: 700;
            margin: 40px 0 20px 0;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .section-title span {{
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        
        .chart-container {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.05) 0%, rgba(114, 46, 209, 0.05) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
        }}
        .chart-title {{
            font-size: 20px;
            font-weight: 700;
            color: rgba(255, 255, 255, 0.9);
            margin-bottom: 20px;
        }}
        
        .sweep-comparison {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.05) 0%, rgba(114, 46, 209, 0.05) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
        }}
        
        .sweep-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 8px;
        }}
        .sweep-table thead th {{
            text-align: left;
            padding: 12px 16px;
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: rgba(255, 255, 255, 0.5);
            border-bottom: 1px solid rgba(24, 144, 255, 0.2);
        }}
        .sweep-table tbody tr {{
            background: rgba(24, 144, 255, 0.05);
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .sweep-table tbody tr:hover {{
            background: rgba(24, 144, 255, 0.12);
            transform: translateX(3px);
        }}
        .sweep-table tbody td {{
            padding: 14px 16px;
            border-top: 1px solid rgba(24, 144, 255, 0.1);
            border-bottom: 1px solid rgba(24, 144, 255, 0.1);
        }}
        .sweep-table tbody td:first-child {{
            border-left: 1px solid rgba(24, 144, 255, 0.1);
            border-radius: 8px 0 0 8px;
        }}
        .sweep-table tbody td:last-child {{
            border-right: 1px solid rgba(24, 144, 255, 0.1);
            border-radius: 0 8px 8px 0;
        }}
        .sweep-table .value {{
            font-weight: 600;
            color: rgba(255, 255, 255, 0.9);
        }}
        .sweep-table .best {{
            color: #52c41a;
            font-weight: 700;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="breadcrumb">
            <a href="index.html">🏠 Dashboard</a>
            <span>/</span>
            <span>Search Performance Comparison</span>
        </div>
        
        <div class="header">
            <h1>🔍 Search Performance Comparison</h1>
            <div class="subtitle">Cross-engine analysis of search throughput and latency</div>
        </div>
        
        <div class="section-title"><span>📊 Performance Summary</span></div>
        <div class="summary-cards">
'''
        
        for engine_data in search_engines:
            engine = engine_data['name']
            is_winner = engine == winner['name']
            winner_class = ' winner' if is_winner else ''
            
            html += f'''
            <div class="summary-card{winner_class}">
                <span class="engine-badge {engine}">{engine.upper()}</span>
                <div class="metric-row">
                    <span class="metric-label">Throughput</span>
                    <span class="metric-value">{engine_data['throughput']:.1f} ops/s</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">P50 Latency</span>
                    <span class="metric-value">{engine_data['p50']:.2f} ms</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">P99 Latency</span>
                    <span class="metric-value">{engine_data['p99']:.2f} ms</span>
                </div>
            </div>
'''
        
        html += '''
        </div>
        
        <div class="chart-container">
            <div class="chart-title">Throughput Comparison</div>
            <canvas id="throughputChart" style="max-height: 350px;"></canvas>
        </div>
        
        <div class="chart-container">
            <div class="chart-title">Latency Comparison (P50 vs P99)</div>
            <canvas id="latencyChart" style="max-height: 350px;"></canvas>
        </div>
        
        <div class="section-title"><span>🔬 Detailed Sweep Analysis</span></div>
'''
        
        # Add sweep tables for each engine
        for engine_data in search_engines:
            engine = engine_data['name']
            sweeps = engine_data.get('sweeps', [])
            
            if not sweeps:
                continue
            
            # Find best sweep (only from successful sweeps)
            successful_sweeps = [s for s in sweeps if not s.get('failed', False)]
            best_sweep = max(successful_sweeps, key=lambda s: s['throughput']) if successful_sweeps else None
            
            html += f'''
        <div class="sweep-comparison">
            <div style="margin-bottom: 20px;">
                <span class="engine-badge {engine}">{engine.upper()}</span>
                <span style="color: rgba(255, 255, 255, 0.6); margin-left: 10px; font-size: 14px;">
                    {len(sweeps)} parameter sweep{'s' if len(sweeps) > 1 else ''}
                </span>
            </div>
            <table class="sweep-table">
                <thead>
                    <tr>
                        <th>Sweep</th>
                        <th>Throughput</th>
                        <th>P50 Latency</th>
                        <th>P99 Latency</th>
                        <th>P100 Latency</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
'''
            
            # Get the scenario name for this engine
            scenario_name = engine_data.get('scenario_name', 'scenario-1-search')
            
            for sweep in sorted(sweeps, key=lambda s: s['sweep']):
                sweep_num = sweep['sweep']
                
                # Check if this sweep failed
                if sweep.get('failed', False):
                    # Escape crash log for JavaScript
                    crash_log = sweep.get('crash_log', 'No crash log available')
                    crash_log_escaped = crash_log.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r')
                    
                    # Check if results page exists
                    has_results = sweep.get('has_results_page', False)
                    results_path = sweep.get('results_page_path', '')
                    
                    # Build the "View Error Log" or "View Partial Metrics" link
                    if has_results:
                        details_link = f'<a href="{results_path}" onclick="event.stopPropagation();" style="color: #1890ff; text-decoration: none;">📊 View Partial Metrics →</a>'
                    else:
                        details_link = '<span style="color: #ff4d4f;">View Error Log →</span>'
                    
                    html += f'''
                    <tr onclick="showCrashLog('{engine}', {sweep_num}, '{crash_log_escaped}')" style="cursor: pointer; background: rgba(255, 77, 79, 0.1);">
                        <td><span class="value" style="color: #ff4d4f;">❌ Sweep {sweep_num}</span></td>
                        <td colspan="4"><span style="color: #ff4d4f;">FAILED: {sweep.get('error', 'Unknown error')}</span></td>
                        <td>{details_link}</td>
                    </tr>
'''
                else:
                    is_best = best_sweep and sweep['sweep'] == best_sweep['sweep']
                    value_class = 'best' if is_best else 'value'
                    
                    # Build link to individual sweep results
                    # If there's only one sweep (treated as Sweep 1 from direct search), link directly to scenario results
                    if len(sweeps) == 1 and sweep_num == 1:
                        sweep_link = f"{self.dataset_name}-{engine}/{scenario_name}/results.html"
                    else:
                        sweep_link = f"{self.dataset_name}-{engine}/{scenario_name}/sweep-{sweep_num}/results.html"
                    
                    html += f'''
                    <tr onclick="window.location.href='{sweep_link}'">
                        <td><span class="{value_class}">Sweep {sweep_num}</span></td>
                        <td><span class="{value_class}">{sweep['throughput']:.1f} ops/s</span></td>
                        <td><span class="value">{sweep['p50']:.2f} ms</span></td>
                        <td><span class="value">{sweep['p99']:.2f} ms</span></td>
                        <td><span class="value">{sweep.get('p100', 0):.2f} ms</span></td>
                        <td><span style="color: #1890ff;">View Results →</span></td>
                    </tr>
'''
            
            html += '''
                </tbody>
            </table>
        </div>
'''
        
        # Add sweep comparison charts if there are multiple sweeps
        max_sweeps = max(len(e.get('sweeps', [])) for e in search_engines)
        if max_sweeps > 1:
            html += '''
        <div class="chart-container">
            <div class="chart-title">Throughput Across All Sweeps</div>
            <canvas id="sweepThroughputChart" style="max-height: 400px;"></canvas>
        </div>
        
        <div class="chart-container">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                <div class="chart-title" style="margin: 0;">Latency Across All Sweeps</div>
                <select id="latencyMetricSelector" style="
                    background: rgba(24, 144, 255, 0.1);
                    border: 1px solid rgba(24, 144, 255, 0.3);
                    color: rgba(255, 255, 255, 0.9);
                    padding: 8px 12px;
                    border-radius: 6px;
                    font-size: 14px;
                    cursor: pointer;
                    outline: none;
                ">
                    <option value="p50">P50 Latency</option>
                    <option value="p90" selected>P90 Latency</option>
                    <option value="p99">P99 Latency</option>
                    <option value="p100">P100 Latency</option>
                </select>
            </div>
            <canvas id="sweepLatencyChart" style="max-height: 400px;"></canvas>
        </div>
'''
        
        html += '''
    </div>
    
    <script>
        // Throughput Chart
        const throughputCtx = document.getElementById('throughputChart').getContext('2d');
        new Chart(throughputCtx, {
            type: 'bar',
            data: {
                labels: ['''
        
        html += ', '.join(f"'{e['name'].upper()}'" for e in search_engines)
        html += '''],
                datasets: [{
                    label: 'Throughput (ops/s)',
                    data: ['''
        
        html += ', '.join(f"{e['throughput']:.1f}" for e in search_engines)
        html += '''],
                    backgroundColor: [
                        'rgba(255, 77, 79, 0.8)',
                        'rgba(82, 196, 26, 0.8)',
                        'rgba(24, 144, 255, 0.8)'
                    ],
                    borderColor: [
                        'rgba(255, 77, 79, 1)',
                        'rgba(82, 196, 26, 1)',
                        'rgba(24, 144, 255, 1)'
                    ],
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(24, 144, 255, 0.5)',
                        borderWidth: 1
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(24, 144, 255, 0.1)' },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    }
                }
            }
        });
        
        // Latency Chart
        const latencyCtx = document.getElementById('latencyChart').getContext('2d');
        new Chart(latencyCtx, {
            type: 'bar',
            data: {
                labels: ['''
        
        html += ', '.join(f"'{e['name'].upper()}'" for e in search_engines)
        html += '''],
                datasets: [
                    {
                        label: 'P50 Latency (ms)',
                        data: ['''
        
        html += ', '.join(f"{e['p50']:.2f}" for e in search_engines)
        html += '''],
                        backgroundColor: 'rgba(24, 144, 255, 0.6)',
                        borderColor: 'rgba(24, 144, 255, 1)',
                        borderWidth: 2
                    },
                    {
                        label: 'P99 Latency (ms)',
                        data: ['''
        
        html += ', '.join(f"{e['p99']:.2f}" for e in search_engines)
        html += '''],
                        backgroundColor: 'rgba(114, 46, 209, 0.6)',
                        borderColor: 'rgba(114, 46, 209, 1)',
                        borderWidth: 2
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        labels: { color: 'rgba(255, 255, 255, 0.9)' }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(24, 144, 255, 0.5)',
                        borderWidth: 1
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(24, 144, 255, 0.1)' },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    }
                }
            }
        });
'''
        
        # Add sweep comparison charts if there are multiple sweeps
        max_sweeps = max(len(e.get('sweeps', [])) for e in search_engines)
        if max_sweeps > 1:
            # Prepare sweep labels
            sweep_labels = [f"'Sweep {i+1}'" for i in range(max_sweeps)]
            
            # Prepare data for each engine
            engine_colors = {
                'faiss': ('rgba(255, 77, 79, 1)', 'rgba(255, 77, 79, 0.1)'),
                'jvector': ('rgba(82, 196, 26, 1)', 'rgba(82, 196, 26, 0.1)'),
                'lucene': ('rgba(24, 144, 255, 1)', 'rgba(24, 144, 255, 0.1)')
            }
            
            html += '''
        
        // Sweep Throughput Chart
        new Chart(document.getElementById('sweepThroughputChart'), {
            type: 'line',
            data: {
                labels: [''' + ', '.join(sweep_labels) + '''],
                datasets: [
'''
            
            for engine_data in search_engines:
                engine = engine_data['name']
                sweeps = engine_data.get('sweeps', [])
                if not sweeps:
                    continue
                
                # Filter out failed sweeps for chart data, use null for failed sweeps
                throughput_data = []
                for s in sorted(sweeps, key=lambda s: s['sweep']):
                    if s.get('failed', False):
                        throughput_data.append('null')
                    else:
                        throughput_data.append(str(s['throughput']))
                
                border_color, bg_color = engine_colors.get(engine, ('rgba(255, 255, 255, 1)', 'rgba(255, 255, 255, 0.1)'))
                
                html += f'''
                    {{
                        label: '{engine.upper()}',
                        data: [{', '.join(throughput_data)}],
                        borderColor: '{border_color}',
                        backgroundColor: '{bg_color}',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: true
                    }},
'''
            
            html += '''
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: true, labels: { color: 'rgba(255, 255, 255, 0.8)', font: { weight: 'bold' } } },
                    tooltip: { backgroundColor: 'rgba(10, 25, 41, 0.95)', padding: 12 }
                },
                scales: {
                    y: { beginAtZero: true, grid: { color: 'rgba(24, 144, 255, 0.1)' }, ticks: { color: 'rgba(255, 255, 255, 0.7)' } },
                    x: { grid: { display: false }, ticks: { color: 'rgba(255, 255, 255, 0.7)', font: { weight: 'bold' } } }
                }
            }
        });
        
        // Sweep Latency Chart - Store all metric data
        const latencyData = {
            labels: [''' + ', '.join(sweep_labels) + '''],
            datasets: {
                p50: [
'''
            
            for engine_data in search_engines:
                engine = engine_data['name']
                sweeps = engine_data.get('sweeps', [])
                if not sweeps:
                    continue
                
                # Filter out failed sweeps for chart data
                p50_data = []
                p99_data = []
                p100_data = []
                for s in sorted(sweeps, key=lambda s: s['sweep']):
                    if s.get('failed', False):
                        p50_data.append('null')
                        p99_data.append('null')
                        p100_data.append('null')
                    else:
                        p50_data.append(str(s['p50']))
                        p99_data.append(str(s['p99']))
                        p100_data.append(str(s.get('p100', s['p99'])))
                
                border_color, bg_color = engine_colors.get(engine, ('rgba(255, 255, 255, 1)', 'rgba(255, 255, 255, 0.1)'))
                
                html += f'''
                    {{
                        label: '{engine.upper()}',
                        data: [{', '.join(p50_data)}],
                        borderColor: '{border_color}',
                        backgroundColor: '{bg_color}',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: true
                    }},
'''
            
            html += '''
                ],
                p90: [
'''
            
            for engine_data in search_engines:
                engine = engine_data['name']
                sweeps = engine_data.get('sweeps', [])
                if not sweeps:
                    continue
                
                # Filter out failed sweeps for chart data
                p90_data = []
                for s in sorted(sweeps, key=lambda s: s['sweep']):
                    if s.get('failed', False):
                        p90_data.append('null')
                    else:
                        p90_data.append(str(s.get('p90', s['p99'])))
                
                border_color, bg_color = engine_colors.get(engine, ('rgba(255, 255, 255, 1)', 'rgba(255, 255, 255, 0.1)'))
                
                html += f'''
                    {{
                        label: '{engine.upper()}',
                        data: [{', '.join(p90_data)}],
                        borderColor: '{border_color}',
                        backgroundColor: '{bg_color}',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: true
                    }},
'''
            
            html += '''
                ],
                p99: [
'''
            
            for engine_data in search_engines:
                engine = engine_data['name']
                sweeps = engine_data.get('sweeps', [])
                if not sweeps:
                    continue
                
                # Filter out failed sweeps for chart data
                p99_data = []
                for s in sorted(sweeps, key=lambda s: s['sweep']):
                    if s.get('failed', False):
                        p99_data.append('null')
                    else:
                        p99_data.append(str(s['p99']))
                
                border_color, bg_color = engine_colors.get(engine, ('rgba(255, 255, 255, 1)', 'rgba(255, 255, 255, 0.1)'))
                
                html += f'''
                    {{
                        label: '{engine.upper()}',
                        data: [{', '.join(p99_data)}],
                        borderColor: '{border_color}',
                        backgroundColor: '{bg_color}',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: true
                    }},
'''
            
            html += '''
                ],
                p100: [
'''
            
            for engine_data in search_engines:
                engine = engine_data['name']
                sweeps = engine_data.get('sweeps', [])
                if not sweeps:
                    continue
                
                # Filter out failed sweeps for chart data
                p100_data = []
                for s in sorted(sweeps, key=lambda s: s['sweep']):
                    if s.get('failed', False):
                        p100_data.append('null')
                    else:
                        p100_data.append(str(s.get('p100', s['p99'])))
                
                border_color, bg_color = engine_colors.get(engine, ('rgba(255, 255, 255, 1)', 'rgba(255, 255, 255, 0.1)'))
                
                html += f'''
                    {{
                        label: '{engine.upper()}',
                        data: [{', '.join(p100_data)}],
                        borderColor: '{border_color}',
                        backgroundColor: '{bg_color}',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: true
                    }},
'''
            
            html += '''
                ]
            }
        };
        
        const sweepLatencyChart = new Chart(document.getElementById('sweepLatencyChart'), {
            type: 'line',
            data: {
                labels: latencyData.labels,
                datasets: latencyData.datasets.p90
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: true, labels: { color: 'rgba(255, 255, 255, 0.8)', font: { weight: 'bold' } } },
                    tooltip: { backgroundColor: 'rgba(10, 25, 41, 0.95)', padding: 12 }
                },
                scales: {
                    y: { beginAtZero: true, grid: { color: 'rgba(24, 144, 255, 0.1)' }, ticks: { color: 'rgba(255, 255, 255, 0.7)' } },
                    x: { grid: { display: false }, ticks: { color: 'rgba(255, 255, 255, 0.7)', font: { weight: 'bold' } } }
                }
            }
        });
        
        // Add event listener for metric selector
        document.getElementById('latencyMetricSelector').addEventListener('change', function(e) {
            const metric = e.target.value;
            sweepLatencyChart.data.datasets = latencyData.datasets[metric];
            sweepLatencyChart.update();
        });
'''
        
        html += '''
        
        // Function to show crash log modal
        function showCrashLog(engine, sweepNum, crashLog) {
            const modal = document.getElementById('crashLogModal');
            const title = document.getElementById('crashLogTitle');
            const content = document.getElementById('crashLogContent');
            
            title.textContent = `${engine.toUpperCase()} - Sweep ${sweepNum} - Crash Log`;
            content.textContent = crashLog;
            
            modal.style.display = 'block';
        }
        
        // Close modal when clicking X or outside
        document.getElementById('closeModal').onclick = function() {
            document.getElementById('crashLogModal').style.display = 'none';
        }
        
        window.onclick = function(event) {
            const modal = document.getElementById('crashLogModal');
            if (event.target == modal) {
                modal.style.display = 'none';
            }
        }
    </script>
    
    <!-- Crash Log Modal -->
    <div id="crashLogModal" style="
        display: none;
        position: fixed;
        z-index: 1000;
        left: 0;
        top: 0;
        width: 100%;
        height: 100%;
        overflow: auto;
        background-color: rgba(0,0,0,0.7);
    ">
        <div style="
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            margin: 5% auto;
            padding: 0;
            border: 1px solid rgba(24, 144, 255, 0.3);
            border-radius: 12px;
            width: 80%;
            max-width: 1000px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
        ">
            <div style="
                background: rgba(24, 144, 255, 0.1);
                padding: 20px;
                border-bottom: 1px solid rgba(24, 144, 255, 0.3);
                border-radius: 12px 12px 0 0;
                display: flex;
                justify-content: space-between;
                align-items: center;
            ">
                <h2 id="crashLogTitle" style="margin: 0; color: #ff4d4f; font-size: 20px;">Crash Log</h2>
                <span id="closeModal" style="
                    color: rgba(255, 255, 255, 0.6);
                    font-size: 28px;
                    font-weight: bold;
                    cursor: pointer;
                    transition: color 0.3s;
                ">&times;</span>
            </div>
            <div style="padding: 20px;">
                <pre id="crashLogContent" style="
                    background: rgba(0, 0, 0, 0.3);
                    color: #ff7875;
                    padding: 20px;
                    border-radius: 8px;
                    overflow-x: auto;
                    max-height: 500px;
                    font-family: 'Courier New', monospace;
                    font-size: 12px;
                    line-height: 1.5;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                "></pre>
            </div>
        </div>
    </div>
</body>
</html>'''
        
        return html
    def generate_bulk_ingest_comparison(self, all_data: Dict[str, Any]) -> str:
        """Generate bulk-ingest-comparison.html with detailed bulk ingest metrics."""
        # Collect bulk ingest data
        bulk_engines = []
        for engine in self.engines:
            if 'bulk_ingest' in all_data.get(engine, {}):
                bulk_data = all_data[engine]['bulk_ingest']
                bulk_engines.append({
                    'name': engine,
                    'failed': bulk_data.get('failed', False),
                    'error': bulk_data.get('error', ''),
                    'throughput': bulk_data.get('throughput', 0),
                    'p50': bulk_data.get('p50', 0),
                    'p99': bulk_data.get('p99', 0),
                    'p100': bulk_data.get('p100', bulk_data.get('p99', 0))
                })
        
        if not bulk_engines:
            return ""
        
        # Find winner among successful benchmarks
        successful_engines = [e for e in bulk_engines if not e['failed']]
        winner = max(successful_engines, key=lambda x: x['throughput']) if successful_engines else None
        
        html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bulk Ingest Comparison - {self.dataset_name.upper()}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0a1929 0%, #1a2332 50%, #0f1419 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .breadcrumb {{
            color: rgba(255, 255, 255, 0.6);
            margin-bottom: 20px;
            font-size: 14px;
        }}
        .breadcrumb a {{
            color: #1890ff;
            text-decoration: none;
        }}
        .breadcrumb a:hover {{
            text-decoration: underline;
        }}
        .breadcrumb span {{
            margin: 0 8px;
        }}
        .header {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.1) 0%, rgba(114, 46, 209, 0.1) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 16px;
            padding: 32px;
            margin-bottom: 32px;
        }}
        .header h1 {{
            font-size: 42px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 50%, #eb2f96 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 12px;
        }}
        .subtitle {{
            color: rgba(255, 255, 255, 0.7);
            font-size: 16px;
        }}
        .section-title {{
            font-size: 28px;
            font-weight: 700;
            margin: 40px 0 20px 0;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .section-title span {{
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .summary-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}
        .summary-card {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.08) 0%, rgba(114, 46, 209, 0.08) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 12px;
            padding: 24px;
            position: relative;
        }}
        .summary-card.winner {{
            border: 2px solid #52c41a;
            box-shadow: 0 0 20px rgba(82, 196, 26, 0.3);
        }}
        .summary-card.winner::before {{
            content: '🏆 Winner';
            position: absolute;
            top: 12px;
            right: 12px;
            background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%);
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        .engine-badge {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 16px;
        }}
        .engine-badge.faiss {{ background: linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%); color: white; }}
        .engine-badge.jvector {{ background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%); color: white; }}
        .engine-badge.lucene {{ background: linear-gradient(135deg, #1890ff 0%, #40a9ff 100%); color: white; }}
        .metric-row {{
            display: flex;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid rgba(24, 144, 255, 0.1);
        }}
        .metric-row:last-child {{
            border-bottom: none;
        }}
        .metric-label {{
            color: rgba(255, 255, 255, 0.6);
            font-size: 14px;
        }}
        .metric-value {{
            color: rgba(255, 255, 255, 0.9);
            font-weight: 600;
            font-size: 16px;
        }}
        .chart-container {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.05) 0%, rgba(114, 46, 209, 0.05) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 32px;
        }}
        .chart-title {{
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 20px;
            color: rgba(255, 255, 255, 0.9);
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="breadcrumb">
            <a href="index.html">🏠 Dashboard</a>
            <span>/</span>
            <span>Bulk Ingest Comparison</span>
        </div>
        
        <div class="header">
            <h1>📥 {self.dataset_name.upper()} Bulk Ingest Comparison</h1>
            <div class="subtitle">Cross-engine analysis of bulk ingestion performance</div>
        </div>
        
        <div class="section-title"><span>📊 Performance Summary</span></div>
        <div class="summary-cards">
'''
        
        for engine_data in bulk_engines:
            engine = engine_data['name']
            
            # Check if this benchmark failed
            if engine_data['failed']:
                html += f'''
            <div class="summary-card" style="border-color: #ff4d4f;">
                <span class="engine-badge {engine}">{engine.upper()}</span>
                <div style="padding: 20px; text-align: center;">
                    <div style="font-size: 48px; margin-bottom: 12px;">❌</div>
                    <div style="color: #ff4d4f; font-weight: 600; font-size: 18px; margin-bottom: 8px;">Benchmark Failed</div>
                    <div style="color: rgba(255, 255, 255, 0.5); font-size: 14px;">{engine_data['error']}</div>
                </div>
            </div>
'''
            else:
                is_winner = winner and engine == winner['name']
                winner_class = ' winner' if is_winner else ''
                
                html += f'''
            <div class="summary-card{winner_class}">
                <span class="engine-badge {engine}">{engine.upper()}</span>
                <div class="metric-row">
                    <span class="metric-label">Throughput</span>
                    <span class="metric-value">{engine_data['throughput']:.1f} docs/s</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">P50 Latency</span>
                    <span class="metric-value">{engine_data['p50']:.2f} ms</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">P99 Latency</span>
                    <span class="metric-value">{engine_data['p99']:.2f} ms</span>
                </div>
                <div class="metric-row">
                    <span class="metric-label">P100 Latency</span>
                    <span class="metric-value">{engine_data['p100']:.2f} ms</span>
                </div>
            </div>
'''
        
        html += '''
        </div>
        
        <div class="chart-container">
            <div class="chart-title">Throughput Comparison</div>
            <canvas id="throughputChart" style="max-height: 350px;"></canvas>
        </div>
        
        <div class="chart-container">
            <div class="chart-title">Latency Comparison (P50, P99, P100)</div>
            <canvas id="latencyChart" style="max-height: 350px;"></canvas>
        </div>
    </div>
    
    <script>
        // Throughput Chart
        const throughputCtx = document.getElementById('throughputChart').getContext('2d');
        new Chart(throughputCtx, {
            type: 'bar',
            data: {
                labels: ['''
        
        html += ', '.join(f"'{e['name'].upper()}'" for e in bulk_engines)
        html += '''],
                datasets: [{
                    label: 'Throughput (docs/s)',
                    data: ['''
        
        html += ', '.join(f"{e['throughput']:.1f}" for e in bulk_engines)
        html += '''],
                    backgroundColor: [
                        'rgba(255, 77, 79, 0.8)',
                        'rgba(82, 196, 26, 0.8)',
                        'rgba(24, 144, 255, 0.8)'
                    ],
                    borderColor: [
                        'rgba(255, 77, 79, 1)',
                        'rgba(82, 196, 26, 1)',
                        'rgba(24, 144, 255, 1)'
                    ],
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(24, 144, 255, 0.5)',
                        borderWidth: 1
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(24, 144, 255, 0.1)' },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    }
                }
            }
        });
        
        // Latency Chart
        const latencyCtx = document.getElementById('latencyChart').getContext('2d');
        new Chart(latencyCtx, {
            type: 'bar',
            data: {
                labels: ['''
        
        html += ', '.join(f"'{e['name'].upper()}'" for e in bulk_engines)
        html += '''],
                datasets: [
                    {
                        label: 'P50 Latency (ms)',
                        data: ['''
        
        html += ', '.join(f"{e['p50']:.2f}" for e in bulk_engines)
        html += '''],
                        backgroundColor: 'rgba(24, 144, 255, 0.6)',
                        borderColor: 'rgba(24, 144, 255, 1)',
                        borderWidth: 2
                    },
                    {
                        label: 'P99 Latency (ms)',
                        data: ['''
        
        html += ', '.join(f"{e['p99']:.2f}" for e in bulk_engines)
        html += '''],
                        backgroundColor: 'rgba(114, 46, 209, 0.6)',
                        borderColor: 'rgba(114, 46, 209, 1)',
                        borderWidth: 2
                    },
                    {
                        label: 'P100 Latency (ms)',
                        data: ['''
        
        html += ', '.join(f"{e['p100']:.2f}" for e in bulk_engines)
        html += '''],
                        backgroundColor: 'rgba(235, 47, 150, 0.6)',
                        borderColor: 'rgba(235, 47, 150, 1)',
                        borderWidth: 2
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                plugins: {
                    legend: {
                        labels: { color: 'rgba(255, 255, 255, 0.9)' }
                    },
                    tooltip: {
                        backgroundColor: 'rgba(0, 0, 0, 0.8)',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        borderColor: 'rgba(24, 144, 255, 0.5)',
                        borderWidth: 1
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        grid: { color: 'rgba(24, 144, 255, 0.1)' },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: 'rgba(255, 255, 255, 0.7)' }
                    }
                }
            }
        });
    </script>
</body>
</html>'''
        
        return html
    
    
    def generate_all_dashboards(self):
        """Generate all dashboard files."""
        print("\n" + "=" * 80)
        print("📊 Generating Comparison Dashboards")
        print("=" * 80)
        
        # Collect data
        print("Collecting benchmark data...")
        all_data = self.collect_data()
        
        # Generate main dashboard
        print("Generating main dashboard (index.html)...")
        main_html = self.generate_main_dashboard(all_data)
        main_file = self.results_dir / "index.html"
        with open(main_file, 'w') as f:
            f.write(main_html)
        print(f"✅ Created: {main_file}")
        
        # Generate search comparison if search data exists
        search_available = any(
            'search' in all_data.get(e, {}) or 'search_sweeps' in all_data.get(e, {})
            for e in self.engines
        )
        if search_available:
            print("Generating search comparison (search-comparison.html)...")
            search_html = self.generate_search_comparison(all_data)
            if search_html:
                search_file = self.results_dir / "search-comparison.html"
                with open(search_file, 'w') as f:
                    f.write(search_html)
                print(f"✅ Created: {search_file}")
        
        # Generate bulk ingest comparison if bulk ingest data exists
        bulk_available = any(
            'bulk_ingest' in all_data.get(e, {})
            for e in self.engines
        )
        if bulk_available:
            print("Generating bulk ingest comparison (bulk-ingest-comparison.html)...")
            bulk_html = self.generate_bulk_ingest_comparison(all_data)
            if bulk_html:
                bulk_file = self.results_dir / "bulk-ingest-comparison.html"
                with open(bulk_file, 'w') as f:
                    f.write(bulk_html)
                print(f"✅ Created: {bulk_file}")
        
        print("=" * 80)
        print(f"🎉 Dashboards generated in: {self.results_dir}")
        print(f"   Open: {main_file}")
        if search_available:
            print(f"   Search: {self.results_dir / 'search-comparison.html'}")
        if bulk_available:
            print(f"   Bulk Ingest: {self.results_dir / 'bulk-ingest-comparison.html'}")
        print("=" * 80)

# Made with Bob
