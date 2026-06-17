#!/usr/bin/env python3
"""
Generic scenario comparison dashboard generator.
Supports any scenario type with flexible metric display and charting.
"""
import json
from typing import Dict, Any, List, Optional, Tuple
from .html_templates import (
    get_html_header, get_html_footer, get_page_header, get_breadcrumb,
    get_engine_badge, get_metric_value
)
from .common import ENGINES, ENGINE_COLORS, escape_html


class ScenarioComparisonGenerator:
    """Generates comparison dashboards for any scenario type."""
    
    def __init__(self, dataset_name: str, timestamp: str, scenario_name: str,
                 scenario_type: str, results_dir: Optional[Any] = None):
        """
        Initialize scenario comparison generator.
        
        Args:
            dataset_name: Name of the dataset
            timestamp: Timestamp string for the dashboard
            scenario_name: Full scenario name (e.g., 'scenario-1-search')
            scenario_type: Type of scenario (search, bulk_ingest, force_merge, etc.)
            results_dir: Optional results directory path for generating links
        """
        self.dataset_name = dataset_name
        self.timestamp = timestamp
        self.scenario_name = scenario_name
        self.scenario_type = scenario_type
        self.results_dir = results_dir
        self.engines = ENGINES
        
        # Scenario-specific configuration
        self.config = self._get_scenario_config(scenario_type)
    
    def _get_scenario_config(self, scenario_type: str) -> Dict[str, Any]:
        """
        Get configuration for a specific scenario type.
        
        Returns:
            Configuration dictionary with display settings and metrics
        """
        configs = {
            'search': {
                'title': 'Search Performance Comparison',
                'icon': '🔍',
                'metrics': [
                    {'key': 'throughput', 'label': 'Throughput', 'unit': 'ops/s', 'higher_is_better': True},
                    {'key': 'p50', 'label': 'P50 Latency', 'unit': 'ms', 'higher_is_better': False},
                    {'key': 'p90', 'label': 'P90 Latency', 'unit': 'ms', 'higher_is_better': False},
                    {'key': 'p99', 'label': 'P99 Latency', 'unit': 'ms', 'higher_is_better': False},
                    {'key': 'p100', 'label': 'P100 Latency', 'unit': 'ms', 'higher_is_better': False},
                    {'key': 'recall_at_k', 'label': 'Recall@K', 'unit': '%', 'higher_is_better': True, 'optional': True},
                    {'key': 'recall_at_1', 'label': 'Recall@1', 'unit': '%', 'higher_is_better': True, 'optional': True},
                ],
                'charts': ['throughput', 'latency', 'recall']
            },
            'bulk_ingest': {
                'title': 'Bulk Ingest Performance Comparison',
                'icon': '📥',
                'metrics': [
                    {'key': 'throughput', 'label': 'Throughput', 'unit': 'docs/s', 'higher_is_better': True},
                    {'key': 'p50', 'label': 'P50 Latency', 'unit': 'ms', 'higher_is_better': False},
                    {'key': 'p99', 'label': 'P99 Latency', 'unit': 'ms', 'higher_is_better': False},
                    {'key': 'p100', 'label': 'P100 Latency', 'unit': 'ms', 'higher_is_better': False},
                ],
                'charts': ['throughput', 'latency']
            },
            'force_merge': {
                'title': 'Force Merge Performance Comparison',
                'icon': '🔄',
                'metrics': [
                    {'key': 'total_time', 'label': 'Total Time', 'unit': 's', 'higher_is_better': False},
                    {'key': 'merge_time', 'label': 'Merge Time', 'unit': 's', 'higher_is_better': False, 'optional': True},
                    {'key': 'merge_count', 'label': 'Merge Count', 'unit': '', 'higher_is_better': False, 'optional': True},
                ],
                'charts': ['time_comparison']
            }
        }
        
        # Return config or default
        return configs.get(scenario_type, {
            'title': f'{scenario_type.replace("_", " ").title()} Comparison',
            'icon': '📊',
            'metrics': [
                {'key': 'throughput', 'label': 'Throughput', 'unit': 'ops/s', 'higher_is_better': True},
            ],
            'charts': []
        })
    
    def generate(self, all_data: Dict[str, Any]) -> str:
        """
        Generate comparison dashboard HTML for the scenario.
        
        Args:
            all_data: Dictionary containing all benchmark data
        
        Returns:
            Complete HTML string for scenario comparison
        """
        # Extract scenario data
        scenario_data = self._extract_scenario_data(all_data)
        
        if not scenario_data:
            return ""
        
        # Determine winner
        winner = self._determine_winner(scenario_data)
        
        # Build HTML
        html = get_html_header(
            f"{self.config['title']} - {self.dataset_name.upper()}",
            include_chartjs=True
        )
        
        html += get_breadcrumb([
            ('Home', 'index.html'),
            (self.config['title'], None)
        ])
        
        # Format scenario name for display (e.g., "scenario-1-search" -> "Scenario 1")
        scenario_display = self.scenario_name.replace('-', ' ').title().replace('Scenario ', 'Scenario ')
        
        html += get_page_header(
            f"{self.config['icon']} {scenario_display}: {self.config['title']}",
            f"Detailed comparison for {self.dataset_name}",
            self.timestamp
        )
        
        # Summary section
        html += self._generate_summary_section(scenario_data, winner)
        
        # Detailed comparison table
        html += self._generate_comparison_table(scenario_data, winner)
        
        # Charts - aggregated comparison
        if self.config.get('charts'):
            html += self._generate_charts(scenario_data)
        
        # Sweep-by-sweep progression charts (if applicable)
        if self._has_sweeps(scenario_data):
            html += self._generate_sweep_progression_charts(scenario_data)
        
        # Sweep details table (if applicable)
        if self._has_sweeps(scenario_data):
            html += self._generate_sweep_details(scenario_data)
        
        html += get_html_footer()
        return html
    
    def _extract_scenario_data(self, all_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract data for specific scenario from all_data."""
        scenario_data = []
        
        for engine in self.engines:
            engine_data = all_data.get(engine, {})
            scenarios = engine_data.get('scenarios', {})
            
            # Check if this engine has this specific scenario
            if self.scenario_name not in scenarios:
                continue
            
            scenario = scenarios[self.scenario_name]
            
            # Handle scenarios with sweeps
            if scenario.get('has_sweeps', False) and scenario.get('sweeps'):
                sweeps = scenario['sweeps']
                
                # Calculate aggregated metrics from successful sweeps
                successful_sweeps = [s for s in sweeps if not s.get('failed', False)]
                
                if successful_sweeps:
                    aggregated = self._aggregate_sweep_data(successful_sweeps)
                    scenario_data.append({
                        'engine': engine,
                        'data': aggregated,
                        'has_sweeps': True,
                        'sweeps': sweeps,
                        'scenario_name': self.scenario_name
                    })
                elif sweeps:  # All failed but we still want to show them
                    scenario_data.append({
                        'engine': engine,
                        'data': {},
                        'has_sweeps': True,
                        'sweeps': sweeps,
                        'scenario_name': self.scenario_name,
                        'all_failed': True
                    })
            
            # Handle direct scenarios (no sweeps)
            elif scenario.get('data'):
                scenario_data.append({
                    'engine': engine,
                    'data': scenario['data'],
                    'has_sweeps': False,
                    'scenario_name': self.scenario_name
                })
        
        return scenario_data
    
    def _aggregate_sweep_data(self, sweeps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate metrics from multiple sweeps."""
        aggregated = {}
        
        for metric in self.config['metrics']:
            key = metric['key']
            values = [s[key] for s in sweeps if key in s and s[key] is not None]
            
            if values:
                aggregated[key] = sum(values) / len(values)
        
        return aggregated
    
    def _determine_winner(self, scenario_data: List[Dict[str, Any]]) -> Optional[str]:
        """Determine the winner based on primary metric."""
        if not scenario_data:
            return None
        
        # Use first metric as primary
        primary_metric = self.config['metrics'][0]
        key = primary_metric['key']
        higher_is_better = primary_metric.get('higher_is_better', True)
        
        # Filter valid data
        valid_data = [
            s for s in scenario_data 
            if not s['data'].get('failed', False) and key in s['data']
        ]
        
        if not valid_data:
            return None
        
        if higher_is_better:
            winner = max(valid_data, key=lambda x: x['data'][key])
        else:
            winner = min(valid_data, key=lambda x: x['data'][key])
        
        return winner['engine']
    
    def _has_sweeps(self, scenario_data: List[Dict[str, Any]]) -> bool:
        """Check if any engine has sweep data."""
        return any(s.get('has_sweeps', False) for s in scenario_data)
    
    def _generate_summary_section(self, scenario_data: List[Dict[str, Any]], 
                                  winner: Optional[str]) -> str:
        """Generate summary cards section."""
        html = '<div class="section"><h2 class="section-title">📊 Summary</h2>'
        html += '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-top: 20px;">'
        
        for engine_info in scenario_data:
            engine = engine_info['engine']
            data = engine_info['data']
            is_winner = (engine == winner)
            
            winner_badge = ' 🏆' if is_winner else ''
            
            html += f'''
            <div style="background: rgba(24, 144, 255, 0.05); border: 1px solid rgba(24, 144, 255, 0.2); 
                        border-radius: 12px; padding: 20px;">
                <div style="margin-bottom: 12px;">{get_engine_badge(engine)}{winner_badge}</div>'''
            
            # Display primary metrics
            for metric in self.config['metrics'][:3]:  # Show top 3 metrics
                key = metric['key']
                if key in data:
                    value = data[key]
                    html += f'''
                <div style="margin: 8px 0;">
                    <div style="font-size: 12px; color: rgba(255,255,255,0.6);">{metric['label']}</div>
                    <div style="font-size: 20px; font-weight: 600;">{value:,.2f} {metric['unit']}</div>
                </div>'''
            
            html += '</div>'
        
        html += '</div></div>'
        return html
    
    def _generate_comparison_table(self, scenario_data: List[Dict[str, Any]], 
                                   winner: Optional[str]) -> str:
        """Generate detailed comparison table."""
        html = '''
        <div class="section">
            <h2 class="section-title">📋 Detailed Metrics</h2>
            <table class="metrics-table">
                <thead>
                    <tr>
                        <th>Engine</th>'''
        
        # Add metric columns
        for metric in self.config['metrics']:
            if not metric.get('optional', False):
                html += f'<th>{metric["label"]}</th>'
        
        html += '<th>Status</th></tr></thead><tbody>'
        
        # Add data rows
        for engine_info in scenario_data:
            engine = engine_info['engine']
            data = engine_info['data']
            is_winner = (engine == winner)
            
            if data.get('failed', False):
                html += f'''
                <tr>
                    <td>{get_engine_badge(engine)}</td>
                    <td colspan="{len([m for m in self.config['metrics'] if not m.get('optional', False)])}">
                        <span style="color: #ff4d4f;">Failed: {escape_html(str(data.get('error', 'Unknown error')))}</span>
                    </td>
                    <td><span style="color: #ff4d4f;">❌ Failed</span></td>
                </tr>'''
            else:
                html += f'<tr><td>{get_engine_badge(engine)}</td>'
                
                for metric in self.config['metrics']:
                    if metric.get('optional', False):
                        continue
                    
                    key = metric['key']
                    if key in data:
                        value = data[key]
                        is_best = is_winner and metric == self.config['metrics'][0]
                        html += f'<td>{get_metric_value(value, metric["unit"], is_best)}</td>'
                    else:
                        html += '<td>N/A</td>'
                
                html += '<td><span style="color: #52c41a;">✅ Success</span></td></tr>'
        
        html += '</tbody></table></div>'
        return html
    
    def _generate_charts(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate charts based on scenario configuration."""
        html = '<div class="section"><h2 class="section-title">📈 Performance Charts</h2>'
        
        # Generate charts based on configuration
        for chart_type in self.config.get('charts', []):
            if chart_type == 'throughput':
                html += self._generate_throughput_chart(scenario_data)
            elif chart_type == 'latency':
                html += self._generate_latency_chart(scenario_data)
            elif chart_type == 'recall':
                html += self._generate_recall_chart(scenario_data)
            elif chart_type == 'time_comparison':
                html += self._generate_time_comparison_chart(scenario_data)
        
        html += '</div>'
        return html
    
    def _generate_throughput_chart(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate throughput comparison chart."""
        labels = [s['engine'].upper() for s in scenario_data if not s['data'].get('failed', False)]
        values = [s['data'].get('throughput', 0) for s in scenario_data if not s['data'].get('failed', False)]
        colors = [ENGINE_COLORS[s['engine']]['border'] for s in scenario_data if not s['data'].get('failed', False)]
        
        return f'''
        <div class="chart-container">
            <div class="chart-title">Throughput Comparison</div>
            <canvas id="throughputChart"></canvas>
        </div>
        <script>
        new Chart(document.getElementById('throughputChart'), {{
            type: 'bar',
            data: {{
                labels: {labels},
                datasets: [{{
                    label: 'Throughput',
                    data: {values},
                    backgroundColor: {colors}
                }}]
            }},
            options: {{
                responsive: true,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{ y: {{ beginAtZero: true }} }}
            }}
        }});
        </script>'''
    
    def _generate_latency_chart(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate latency comparison chart."""
        labels = [s['engine'].upper() for s in scenario_data if not s['data'].get('failed', False)]
        
        datasets = []
        for percentile in ['p50', 'p90', 'p99']:
            values = [s['data'].get(percentile, 0) for s in scenario_data if not s['data'].get('failed', False)]
            datasets.append({
                'label': percentile.upper(),
                'data': values
            })
        
        return f'''
        <div class="chart-container">
            <div class="chart-title">Latency Comparison</div>
            <canvas id="latencyChart"></canvas>
        </div>
        <script>
        new Chart(document.getElementById('latencyChart'), {{
            type: 'bar',
            data: {{
                labels: {labels},
                datasets: {datasets}
            }},
            options: {{
                responsive: true,
                scales: {{ y: {{ beginAtZero: true }} }}
            }}
        }});
        </script>'''
    
    def _generate_recall_chart(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate recall metrics chart."""
        # Implementation similar to latency chart
        return ""
    
    def _generate_sweep_progression_charts(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate sweep-by-sweep progression charts showing metrics across sweeps."""
        html = '<div class="section"><h2 class="section-title">📊 Sweep Progression Analysis</h2>'
        html += '<p style="color: rgba(255,255,255,0.7); margin-bottom: 20px;">Track how each engine performs across different parameter sweeps</p>'
        
        # Check if we have sweep data
        has_sweep_data = any(
            s.get('has_sweeps', False) and s.get('sweeps') 
            for s in scenario_data
        )
        
        if not has_sweep_data:
            return ""
        
        # Generate charts based on scenario type
        if self.scenario_type == 'search':
            html += self._generate_sweep_throughput_chart(scenario_data)
            html += self._generate_sweep_latency_chart(scenario_data)
            html += self._generate_sweep_recall_chart(scenario_data)
        elif self.scenario_type == 'bulk_ingest':
            html += self._generate_sweep_throughput_chart(scenario_data)
            html += self._generate_sweep_latency_chart(scenario_data)
        
        html += '</div>'
        return html
    
    def _generate_sweep_throughput_chart(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate line chart showing throughput across sweeps for each engine."""
        # Prepare data for each engine
        datasets = []
        
        for engine_info in scenario_data:
            if not engine_info.get('has_sweeps', False):
                continue
            
            engine = engine_info['engine']
            sweeps = engine_info.get('sweeps', [])
            
            # Extract throughput data from successful sweeps
            sweep_numbers = []
            throughput_values = []
            
            for sweep in sweeps:
                if not sweep.get('failed', False) and 'throughput' in sweep:
                    sweep_numbers.append(sweep.get('sweep', 0))
                    throughput_values.append(sweep['throughput'])
            
            if throughput_values:
                color = ENGINE_COLORS[engine]['border']
                bg_color = ENGINE_COLORS[engine]['bg']
                
                # Build dataset as a dictionary
                dataset = {
                    'label': engine.upper(),
                    'data': throughput_values,
                    'borderColor': color,
                    'backgroundColor': bg_color,
                    'borderWidth': 3,
                    'tension': 0.4,
                    'fill': True
                }
                datasets.append(dataset)
        
        if not datasets:
            return ""
        
        # Use sweep numbers from first engine as labels
        max_sweeps = max(len(d['data']) for d in datasets)
        labels = [f"Sweep {i+1}" for i in range(max_sweeps)]
        
        datasets_js = json.dumps(datasets)
        
        return f'''
        <div class="chart-container">
            <div class="chart-title">Throughput by Sweep</div>
            <canvas id="sweepThroughputChart"></canvas>
        </div>
        <script>
        new Chart(document.getElementById('sweepThroughputChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(labels)},
                datasets: {datasets_js}
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ display: true, labels: {{ color: 'rgba(255, 255, 255, 0.8)', font: {{ weight: 'bold' }} }} }},
                    tooltip: {{ backgroundColor: 'rgba(10, 25, 41, 0.95)', padding: 12 }}
                }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: 'rgba(24, 144, 255, 0.1)' }}, ticks: {{ color: 'rgba(255, 255, 255, 0.7)' }} }},
                    x: {{ grid: {{ display: false }}, ticks: {{ color: 'rgba(255, 255, 255, 0.7)', font: {{ weight: 'bold' }} }} }}
                }}
            }}
        }});
        </script>'''
    
    def _generate_sweep_latency_chart(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate line chart showing latency percentiles across sweeps."""
        # Prepare datasets for each engine and percentile
        all_datasets = []
        
        for engine_info in scenario_data:
            if not engine_info.get('has_sweeps', False):
                continue
            
            engine = engine_info['engine']
            sweeps = engine_info.get('sweeps', [])
            color = ENGINE_COLORS[engine]['border']
            
            # Create datasets for P50, P90, P99
            for percentile in ['p50', 'p90', 'p99']:
                sweep_numbers = []
                latency_values = []
                
                for sweep in sweeps:
                    if not sweep.get('failed', False) and percentile in sweep:
                        sweep_numbers.append(sweep.get('sweep', 0))
                        latency_values.append(sweep[percentile])
                
                if latency_values:
                    # Vary line style for different percentiles
                    border_dash = [] if percentile == 'p50' else [5, 5] if percentile == 'p90' else [2, 2]
                    bg_color = ENGINE_COLORS[engine]['bg']
                    
                    # Build dataset as a dictionary
                    dataset = {
                        'label': f'{engine.upper()} {percentile.upper()}',
                        'data': latency_values,
                        'borderColor': color,
                        'backgroundColor': bg_color,
                        'borderWidth': 3,
                        'borderDash': border_dash,
                        'tension': 0.4,
                        'fill': True
                    }
                    all_datasets.append(dataset)
        
        if not all_datasets:
            return ""
        
        # Use sweep numbers as labels
        max_sweeps = max(len(d['data']) for d in all_datasets)
        labels = [f"Sweep {i+1}" for i in range(max_sweeps)]
        
        datasets_js = json.dumps(all_datasets)
        
        return f'''
        <div class="chart-container">
            <div class="chart-title">Latency by Sweep (P50, P90, P99)</div>
            <canvas id="sweepLatencyChart"></canvas>
        </div>
        <script>
        new Chart(document.getElementById('sweepLatencyChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(labels)},
                datasets: {datasets_js}
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ display: true, labels: {{ color: 'rgba(255, 255, 255, 0.8)', font: {{ weight: 'bold' }} }} }},
                    tooltip: {{ backgroundColor: 'rgba(10, 25, 41, 0.95)', padding: 12 }}
                }},
                scales: {{
                    y: {{ beginAtZero: true, grid: {{ color: 'rgba(24, 144, 255, 0.1)' }}, ticks: {{ color: 'rgba(255, 255, 255, 0.7)' }} }},
                    x: {{ grid: {{ display: false }}, ticks: {{ color: 'rgba(255, 255, 255, 0.7)', font: {{ weight: 'bold' }} }} }}
                }}
            }}
        }});
        </script>'''
    
    def _generate_sweep_recall_chart(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate line chart showing recall metrics across sweeps."""
        # Prepare data for each engine
        datasets = []
        
        for engine_info in scenario_data:
            if not engine_info.get('has_sweeps', False):
                continue
            
            engine = engine_info['engine']
            sweeps = engine_info.get('sweeps', [])
            
            # Try both recall_at_k and recall_at_1
            for recall_key in ['recall_at_k', 'recall_at_1']:
                sweep_numbers = []
                recall_values = []
                
                for sweep in sweeps:
                    if not sweep.get('failed', False) and recall_key in sweep:
                        sweep_numbers.append(sweep.get('sweep', 0))
                        recall_values.append(sweep[recall_key])
                
                if recall_values:
                    color = ENGINE_COLORS[engine]['border']
                    bg_color = ENGINE_COLORS[engine]['bg']
                    label_suffix = '@K' if recall_key == 'recall_at_k' else '@1'
                    
                    # Build dataset as a dictionary
                    dataset = {
                        'label': f'{engine.upper()} Recall{label_suffix}',
                        'data': recall_values,
                        'borderColor': color,
                        'backgroundColor': bg_color,
                        'borderWidth': 3,
                        'tension': 0.4,
                        'fill': True
                    }
                    datasets.append(dataset)
                    break  # Only use one recall metric per engine
        
        if not datasets:
            return ""
        
        # Use sweep numbers as labels
        max_sweeps = max(len(d['data']) for d in datasets)
        labels = [f"Sweep {i+1}" for i in range(max_sweeps)]
        
        datasets_js = json.dumps(datasets)
        
        return f'''
        <div class="chart-container">
            <div class="chart-title">Recall by Sweep</div>
            <canvas id="sweepRecallChart"></canvas>
        </div>
        <script>
        new Chart(document.getElementById('sweepRecallChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(labels)},
                datasets: {datasets_js}
            }},
            options: {{
                responsive: true,
                plugins: {{
                    legend: {{ display: true, labels: {{ color: 'rgba(255, 255, 255, 0.8)', font: {{ weight: 'bold' }} }} }},
                    tooltip: {{ backgroundColor: 'rgba(10, 25, 41, 0.95)', padding: 12 }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        max: 1.0,
                        grid: {{ color: 'rgba(24, 144, 255, 0.1)' }},
                        ticks: {{
                            color: 'rgba(255, 255, 255, 0.7)',
                            callback: function(value) {{
                                return (value * 100).toFixed(0) + '%';
                            }}
                        }}
                    }},
                    x: {{ grid: {{ display: false }}, ticks: {{ color: 'rgba(255, 255, 255, 0.7)', font: {{ weight: 'bold' }} }} }}
                }}
            }}
        }});
        </script>'''
    
    def _generate_time_comparison_chart(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate time comparison chart for force merge."""
        # Implementation for time-based metrics
        return ""
    
    def _generate_sweep_details(self, scenario_data: List[Dict[str, Any]]) -> str:
        """Generate sweep-by-sweep details section with links to results.html."""
        html = '<div class="section"><h2 class="section-title">🔄 Sweep Details</h2>'
        
        for engine_info in scenario_data:
            if not engine_info.get('has_sweeps', False):
                continue
            
            engine = engine_info['engine']
            sweeps = engine_info.get('sweeps', [])
            scenario_name = engine_info.get('scenario_name')
            
            # Skip if no scenario name (shouldn't happen, but be safe)
            if not scenario_name:
                continue
            
            html += f'<h3>{get_engine_badge(engine)}</h3>'
            html += '<table class="metrics-table"><thead><tr><th>Sweep</th>'
            
            for metric in self.config['metrics'][:4]:  # Show first 4 metrics
                html += f'<th>{metric["label"]}</th>'
            
            html += '<th>Status</th><th>Details</th></tr></thead><tbody>'
            
            for sweep in sweeps:
                sweep_num = sweep.get('sweep', '?')
                
                # Build link to results.html
                if len(sweeps) == 1 and sweep_num == 1:
                    # Direct scenario (no sweep subdirectory)
                    results_link = f"{self.dataset_name}-{engine}/{scenario_name}/results.html"
                else:
                    # Sweep subdirectory
                    results_link = f"{self.dataset_name}-{engine}/{scenario_name}/sweep-{sweep_num}/results.html"
                
                if sweep.get('failed', False):
                    # Check if partial results exist
                    has_results = sweep.get('has_results_page', False)
                    results_path = sweep.get('results_page_path', '')
                    
                    html += f'<tr style="background: rgba(255, 77, 79, 0.1);"><td>Sweep {sweep_num}</td>'
                    html += f'<td colspan="{len(self.config["metrics"][:4])}"><span style="color: #ff4d4f;">Failed: {escape_html(str(sweep.get("error", "Unknown error")))}</span></td>'
                    html += '<td><span style="color: #ff4d4f;">❌</span></td>'
                    
                    # Add link to partial results if available
                    if has_results and results_path:
                        html += f'<td><a href="{results_path}" style="color: #1890ff; text-decoration: none;">📊 View Partial Metrics →</a></td>'
                    else:
                        html += '<td><span style="color: #ff4d4f;">View Error Log →</span></td>'
                else:
                    html += f'<tr onclick="window.location.href=\'{results_link}\'" style="cursor: pointer;"><td>Sweep {sweep_num}</td>'
                    
                    for metric in self.config['metrics'][:4]:
                        key = metric['key']
                        if key in sweep:
                            html += f'<td>{sweep[key]:,.2f} {metric["unit"]}</td>'
                        else:
                            html += '<td>N/A</td>'
                    html += '<td><span style="color: #52c41a;">✅</span></td>'
                    html += '<td><span style="color: #1890ff;">View Results →</span></td>'
                
                html += '</tr>'
            
            html += '</tbody></table>'
        
        html += '</div>'
        return html

# Made with Bob
