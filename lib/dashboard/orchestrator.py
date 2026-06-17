#!/usr/bin/env python3
"""
Dashboard orchestrator - coordinates generation of all dashboard types.
"""
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from .data_collector import DataCollector
from .main_dashboard import MainDashboardGenerator
from .scenario_comparison import ScenarioComparisonGenerator


class DashboardOrchestrator:
    """Orchestrates generation of all dashboard types."""
    
    def __init__(self, results_dir: Path):
        """
        Initialize dashboard orchestrator.
        
        Args:
            results_dir: Path to results directory
        """
        self.results_dir = Path(results_dir)
        self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Initialize data collector
        self.collector = DataCollector(self.results_dir, "")
        self.dataset_name = self.collector.extract_dataset_name()
        self.collector.dataset_name = self.dataset_name
        
        # Collect all data once
        self.all_data: Dict[str, Any] = {}
    
    def generate_all(self):
        """Generate all dashboard files."""
        print("\n" + "=" * 80)
        print("📊 Generating Comparison Dashboards")
        print("=" * 80)
        
        # Collect data
        print("Collecting benchmark data...")
        self.all_data = self.collector.collect_data()
        
        if not self.all_data:
            print("⚠️  No data collected, exiting...")
            return
        
        # Generate main dashboard
        self._generate_main_dashboard()
        
        # Generate scenario-specific dashboards
        self._generate_scenario_dashboards()
        
        print("=" * 80)
        print(f"🎉 Dashboards generated in: {self.results_dir}")
        print(f"   Open: {self.results_dir / 'index.html'}")
        print("=" * 80)
    
    def _generate_main_dashboard(self):
        """Generate main index.html dashboard."""
        print("Generating main dashboard (index.html)...")
        
        generator = MainDashboardGenerator(self.dataset_name, self.timestamp)
        html = generator.generate(self.all_data)
        
        main_file = self.results_dir / "index.html"
        with open(main_file, 'w') as f:
            f.write(html)
        
        print(f"✅ Created: {main_file}")
    
    def _generate_scenario_dashboards(self):
        """Generate dashboards for each individual scenario found in data."""
        # Detect available scenarios
        scenarios = self._detect_scenarios()
        
        for scenario_name, scenario_type in scenarios.items():
            self._generate_scenario_dashboard(scenario_name, scenario_type)
    
    def _detect_scenarios(self) -> Dict[str, str]:
        """
        Detect all individual scenarios across all engines.
        
        Returns:
            Dictionary mapping scenario_name -> scenario_type
        """
        scenarios = {}
        
        for engine_data in self.all_data.values():
            for scenario_name, scenario_info in engine_data.get('scenarios', {}).items():
                if scenario_name not in scenarios:
                    scenarios[scenario_name] = scenario_info['type']
        
        return scenarios
    
    def _generate_scenario_dashboard(self, scenario_name: str, scenario_type: str):
        """Generate dashboard for a specific scenario."""
        # Generate filename from scenario name
        filename = f"{scenario_name}-comparison.html"
        
        print(f"Generating {scenario_name} comparison ({filename})...")
        
        generator = ScenarioComparisonGenerator(
            self.dataset_name,
            self.timestamp,
            scenario_name,
            scenario_type,
            self.results_dir
        )
        
        html = generator.generate(self.all_data)
        
        if html:
            output_file = self.results_dir / filename
            with open(output_file, 'w') as f:
                f.write(html)
            print(f"✅ Created: {output_file}")
        else:
            print(f"⚠️  No data for {scenario_name}, skipping...")


def generate_dashboards(results_dir: Path):
    """
    Convenience function to generate all dashboards.
    
    Args:
        results_dir: Path to results directory
    """
    orchestrator = DashboardOrchestrator(results_dir)
    orchestrator.generate_all()

# Made with Bob
