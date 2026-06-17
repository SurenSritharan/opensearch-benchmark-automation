#!/usr/bin/env python3
"""
Dashboard generation package for benchmark results.
"""
from .common import (
    ENGINES,
    ENGINE_COLORS,
    get_scenario_type,
    format_number,
    format_throughput,
    format_latency,
    format_percentage,
    get_winner_class,
    escape_html,
    get_engine_display_name,
    determine_winner
)
from .data_collector import DataCollector
from .main_dashboard import MainDashboardGenerator
from .scenario_comparison import ScenarioComparisonGenerator
from .orchestrator import DashboardOrchestrator, generate_dashboards

__all__ = [
    # Common utilities
    'ENGINES',
    'ENGINE_COLORS',
    'get_scenario_type',
    'format_number',
    'format_throughput',
    'format_latency',
    'format_percentage',
    'get_winner_class',
    'escape_html',
    'get_engine_display_name',
    'determine_winner',
    # Main classes
    'DataCollector',
    'MainDashboardGenerator',
    'ScenarioComparisonGenerator',
    'DashboardOrchestrator',
    # Convenience function
    'generate_dashboards'
]

# Made with Bob
