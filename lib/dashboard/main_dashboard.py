#!/usr/bin/env python3
"""
Main dashboard generator - creates the index.html overview page.
Now supports multiple scenarios of the same type.
"""
from typing import Dict, Any, Optional, List
from .html_templates import (
    get_html_header, get_html_footer, get_page_header,
    get_engine_badge, get_metric_value
)
from .common import ENGINES, determine_winner


class MainDashboardGenerator:
    """Generates the main index.html dashboard."""
    
    def __init__(self, dataset_name: str, timestamp: str):
        """
        Initialize main dashboard generator.
        
        Args:
            dataset_name: Name of the dataset
            timestamp: Timestamp string for the dashboard
        """
        self.dataset_name = dataset_name
        self.timestamp = timestamp
        self.engines = ENGINES
    
    def generate(self, all_data: Dict[str, Any]) -> str:
        """
        Generate main dashboard HTML.
        
        Args:
            all_data: Dictionary containing all benchmark data with new structure
        
        Returns:
            Complete HTML string for main dashboard
        """
        # Collect all scenarios grouped by type
        scenarios_by_type = self._group_scenarios_by_type(all_data)
        
        # Build HTML
        html = get_html_header(
            f"{self.dataset_name.upper()} Benchmark Results - Engine Comparison",
            include_chartjs=False
        )
        
        html += get_page_header(
            f"🚀 {self.dataset_name.upper()} Benchmark Results",
            "Comprehensive comparison of vector search engines",
            self.timestamp
        )
        
        # Generate sections for each scenario type
        for scenario_type, scenarios in scenarios_by_type.items():
            html += self._generate_scenario_type_section(scenario_type, scenarios, all_data)
        
        html += get_html_footer()
        return html
    
    def _group_scenarios_by_type(self, all_data: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        Group all scenarios by their type.
        
        Returns:
            Dictionary mapping scenario_type -> list of scenario_names
        """
        scenarios_by_type = {}
        
        for engine_data in all_data.values():
            for scenario_name, scenario_info in engine_data.get('scenarios', {}).items():
                scenario_type = scenario_info['type']
                if scenario_type not in scenarios_by_type:
                    scenarios_by_type[scenario_type] = []
                if scenario_name not in scenarios_by_type[scenario_type]:
                    scenarios_by_type[scenario_type].append(scenario_name)
        
        # Sort scenario names within each type
        for scenario_type in scenarios_by_type:
            scenarios_by_type[scenario_type].sort()
        
        return scenarios_by_type
    
    def _generate_scenario_type_section(self, scenario_type: str, 
                                       scenario_names: List[str], 
                                       all_data: Dict[str, Any]) -> str:
        """Generate section for a scenario type showing all scenarios of that type."""
        # Map scenario types to display info
        type_info = {
            'search': {'icon': '🔍', 'title': 'Search Performance'},
            'bulk_ingest': {'icon': '📥', 'title': 'Bulk Ingest Performance'},
            'force_merge': {'icon': '🔄', 'title': 'Force Merge Performance'}
        }
        
        info = type_info.get(scenario_type, {'icon': '📊', 'title': scenario_type.replace('_', ' ').title()})
        
        html = f'''
<div class="section">
    <div class="section-header">
        <div class="section-title">
            <span class="section-icon">{info['icon']}</span>
            <span>{info['title']}</span>
        </div>
    </div>
    <div style="margin-top: 20px;">
'''
        
        # List each scenario
        for scenario_name in scenario_names:
            html += self._generate_scenario_card(scenario_name, scenario_type, all_data)
        
        html += '''
    </div>
</div>'''
        
        return html
    
    def _generate_scenario_card(self, scenario_name: str, scenario_type: str, 
                                all_data: Dict[str, Any]) -> str:
        """Generate a card for a single scenario."""
        # Format scenario name for display
        scenario_display = scenario_name.replace('-', ' ').title()
        
        # Count engines that have this scenario
        engines_with_data = []
        for engine in self.engines:
            scenarios = all_data.get(engine, {}).get('scenarios', {})
            if scenario_name in scenarios:
                scenario_data = scenarios[scenario_name]
                # Check if it has actual data (not just failed)
                if scenario_data.get('sweeps') or scenario_data.get('data'):
                    engines_with_data.append(engine)
        
        engines_count = len(engines_with_data)
        comparison_link = f"{scenario_name}-comparison.html"
        
        html = f'''
        <div class="scenario-card" onclick="window.location.href='{comparison_link}'" 
             style="background: rgba(24, 144, 255, 0.05); border: 1px solid rgba(24, 144, 255, 0.2); 
                    border-radius: 12px; padding: 20px; margin-bottom: 15px; cursor: pointer; 
                    transition: all 0.2s ease;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0 0 8px 0; font-size: 18px;">{scenario_display}</h3>
                    <div style="color: rgba(255, 255, 255, 0.6); font-size: 14px;">
                        {engines_count} engine(s) • Click to view detailed comparison
                    </div>
                </div>
                <div style="display: flex; gap: 8px;">
'''
        
        # Show engine badges
        for engine in engines_with_data:
            html += f'                    {get_engine_badge(engine)}\n'
        
        html += '''
                </div>
            </div>
        </div>
'''
        
        return html

# Made with Bob
