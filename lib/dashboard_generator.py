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
                
                # Check if it has test_run.json (direct scenario)
                test_file = scenario_dir / "test_run.json"
                if test_file.exists():
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
                        if test_file.exists():
                            with open(test_file) as f:
                                data = json.load(f)
                                workload_params = data.get("workload-params", {})
                                
                                for op in data["results"]["op_metrics"]:
                                    throughput = op.get("throughput", {}).get("mean")
                                    latency = op.get("latency", {})
                                    p50 = latency.get("50_0") if latency else None
                                    p90 = latency.get("90_0") if latency else None
                                    p99 = latency.get("99_0") if latency else None
                                    p100 = latency.get("100_0") if latency else None
                                    
                                    if throughput and p50:
                                        sweep_num = int(sweep_dir.name.split('-')[1])
                                        all_data[engine][f'{scenario_type}_sweeps'].append({
                                            'sweep': sweep_num,
                                            'throughput': throughput,
                                            'p50': p50,
                                            'p90': p90,
                                            'p99': p99,
                                            'p100': p100,
                                            'config': workload_params
                                        })
                
                # Handle direct scenarios (no sweeps)
                else:
                    test_file = scenario_dir / "test_run.json"
                    if test_file.exists():
                        with open(test_file) as f:
                            data = json.load(f)
                            
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
                                        'p99': latency.get("99_0"),
                                        'p100': latency.get("100_0"),
                                        'config': data.get("workload-params", {})
                                    }
                            
                            elif scenario_type == 'force_merge':
                                for op in data["results"]["op_metrics"]:
                                    if "force-merge" in op.get("operation", "").lower():
                                        all_data[engine]['force_merge'] = {
                                            'service_time': op.get('service_time', 0)
                                        }
                                        break
        
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
        
        # Determine winners for available scenarios
        bulk_winner = None
        if bulk_ingest_available:
            bulk_winner = max([e for e in self.engines if 'bulk_ingest' in all_data.get(e, {})],
                            key=lambda e: all_data[e]['bulk_ingest']['throughput'], default=None)
        
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
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 12px;
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
        
        # Search Section (only if data exists)
        if search_available:
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
                        <div class="sweep-value">{sweep['throughput']:.1f} <span style="font-size: 12px; color: rgba(255, 255, 255, 0.5);">ops/s</span></div>
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
        
        # Force Merge Section (only if data exists)
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
                        <th>Service Time</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
'''
            
            for engine in self.engines:
                if 'force_merge' in all_data.get(engine, {}):
                    d = all_data[engine]['force_merge']
                    html += f'''
                    <tr onclick="window.location.href='{self.dataset_name}-{engine}/scenario-3-force-merge/results.html'">
                        <td><span class="engine-badge {engine}">{engine.upper()}</span></td>
                        <td><span class="metric-value">{d['service_time']:.2f}</span><span class="metric-unit">s</span></td>
                        <td><span style="color: #1890ff;">View Results →</span></td>
                    </tr>
'''
            
            html += '''
                </tbody>
            </table>
        </div>
'''
        
        html += '''
    </div>
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
                    avg_throughput = sum(s['throughput'] for s in sweeps) / len(sweeps)
                    avg_p50 = sum(s['p50'] for s in sweeps) / len(sweeps)
                    avg_p99 = sum(s['p99'] for s in sweeps) / len(sweeps)
                    search_engines.append({
                        'name': engine,
                        'throughput': avg_throughput,
                        'p50': avg_p50,
                        'p99': avg_p99,
                        'sweeps': sweeps,
                        'scenario_name': scenario_name
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
            font-size: 38px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
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
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 40px 0 20px 0;
            padding-left: 10px;
            border-left: 4px solid #1890ff;
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
        
        <div class="section-title">📊 Performance Summary</div>
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
        
        <div class="section-title">🔬 Detailed Sweep Analysis</div>
'''
        
        # Add sweep tables for each engine
        for engine_data in search_engines:
            engine = engine_data['name']
            sweeps = engine_data.get('sweeps', [])
            
            if not sweeps:
                continue
            
            # Find best sweep
            best_sweep = max(sweeps, key=lambda s: s['throughput'])
            
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
                is_best = sweep['sweep'] == best_sweep['sweep']
                value_class = 'best' if is_best else 'value'
                sweep_num = sweep['sweep']
                
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
                
                # Pad data if needed
                throughput_data = [str(s['throughput']) for s in sorted(sweeps, key=lambda s: s['sweep'])]
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
                
                p50_data = [str(s['p50']) for s in sorted(sweeps, key=lambda s: s['sweep'])]
                p99_data = [str(s['p99']) for s in sorted(sweeps, key=lambda s: s['sweep'])]
                p100_data = [str(s.get('p100', s['p99'])) for s in sorted(sweeps, key=lambda s: s['sweep'])]
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
                
                p90_data = [str(s.get('p90', s['p99'])) for s in sorted(sweeps, key=lambda s: s['sweep'])]
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
                
                p99_data = [str(s['p99']) for s in sorted(sweeps, key=lambda s: s['sweep'])]
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
                
                p100_data = [str(s.get('p100', s['p99'])) for s in sorted(sweeps, key=lambda s: s['sweep'])]
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
        
        # TODO: Generate bulk-ingest-comparison.html
        
        print("=" * 80)
        print(f"🎉 Dashboards generated in: {self.results_dir}")
        print(f"   Open: {main_file}")
        if search_available:
            print(f"   Search: {self.results_dir / 'search-comparison.html'}")
        print("=" * 80)

# Made with Bob
