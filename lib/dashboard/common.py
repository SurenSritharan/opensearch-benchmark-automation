#!/usr/bin/env python3
"""
Common utilities and constants for dashboard generation.
"""
from typing import Dict, Any, List, Optional


# Engine names
ENGINES = ["faiss", "jvector", "lucene"]

# Engine colors for charts
ENGINE_COLORS = {
    'faiss': {'border': 'rgba(24, 144, 255, 1)', 'bg': 'rgba(24, 144, 255, 0.2)'},
    'jvector': {'border': 'rgba(82, 196, 26, 1)', 'bg': 'rgba(82, 196, 26, 0.1)'},
    'lucene': {'border': 'rgba(235, 47, 150, 1)', 'bg': 'rgba(235, 47, 150, 0.2)'}
}


def get_scenario_type(scenario_name: str) -> str:
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


def format_number(value: Optional[float], decimals: int = 2) -> str:
    """Format a number with specified decimal places."""
    if value is None:
        return "N/A"
    return f"{value:.{decimals}f}"


def format_throughput(value: Optional[float]) -> str:
    """Format throughput value."""
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def format_latency(value: Optional[float]) -> str:
    """Format latency value in milliseconds."""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def format_percentage(value: Optional[float]) -> str:
    """Format percentage value."""
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def get_winner_class(is_winner: bool) -> str:
    """Get CSS class for winner highlighting."""
    return "winner" if is_winner else ""


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ""
    text = text.replace('&', '&')
    text = text.replace('<', '<')
    text = text.replace('>', '>')
    text = text.replace('"', '"')
    text = text.replace("'", '&#x27;')
    return text


def get_engine_display_name(engine: str) -> str:
    """Get display name for engine."""
    display_names = {
        'faiss': 'FAISS',
        'jvector': 'JVector',
        'lucene': 'Lucene'
    }
    return display_names.get(engine, engine.upper())


def determine_winner(engines_data: List[Dict[str, Any]], metric_key: str, 
                    higher_is_better: bool = True) -> Optional[str]:
    """
    Determine the winner among engines based on a metric.
    
    Args:
        engines_data: List of engine data dictionaries
        metric_key: Key to compare in the data
        higher_is_better: If True, higher values win; if False, lower values win
    
    Returns:
        Name of the winning engine or None
    """
    if not engines_data:
        return None
    
    # Filter out failed engines
    valid_engines = [e for e in engines_data 
                    if not e.get('failed', False) and metric_key in e]
    
    if not valid_engines:
        return None
    
    if higher_is_better:
        winner = max(valid_engines, key=lambda x: x[metric_key])
    else:
        winner = min(valid_engines, key=lambda x: x[metric_key])
    
    return winner.get('name', winner.get('engine'))

# Made with Bob
