#!/usr/bin/env python3
"""
HTML templates and CSS styles for dashboard generation.
Contains reusable HTML components and styling.
"""
from typing import Optional


def get_common_css() -> str:
    """Get common CSS styles used across all dashboards."""
    return '''
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0a1929 0%, #1a2332 50%, #0f1419 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }
        .container { max-width: 1800px; margin: 0 auto; }
        .header {
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.1) 0%, rgba(114, 46, 209, 0.1) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 40px;
            margin-bottom: 30px;
            backdrop-filter: blur(10px);
        }
        .header h1 {
            font-size: 42px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 50%, #eb2f96 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 12px;
        }
        .header .subtitle { font-size: 18px; color: rgba(255, 255, 255, 0.7); margin-bottom: 15px; }
        .header .timestamp { font-size: 14px; color: rgba(255, 255, 255, 0.5); }
        .breadcrumb {
            color: rgba(255, 255, 255, 0.6);
            margin-bottom: 20px;
            font-size: 14px;
        }
        .breadcrumb a {
            color: #1890ff;
            text-decoration: none;
        }
        .breadcrumb a:hover {
            text-decoration: underline;
        }
        .breadcrumb span {
            margin: 0 8px;
        }
        .section {
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.05) 0%, rgba(114, 46, 209, 0.05) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            cursor: pointer;
            transition: all 0.3s ease;
        }
        .section:hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 40px rgba(24, 144, 255, 0.25);
            border-color: rgba(24, 144, 255, 0.4);
        }
        .section-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 25px;
        }
        .section-title {
            font-size: 28px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .section-title span {
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .section-icon { font-size: 32px; }
        .view-comparison {
            padding: 10px 20px;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            border: none;
            border-radius: 10px;
            color: white;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .view-comparison:hover {
            transform: scale(1.05);
            box-shadow: 0 4px 16px rgba(24, 144, 255, 0.4);
        }
        .metrics-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0 10px;
        }
        .metrics-table thead th {
            text-align: left;
            padding: 12px 16px;
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: rgba(255, 255, 255, 0.5);
            border-bottom: 1px solid rgba(24, 144, 255, 0.2);
        }
        .metrics-table tbody tr {
            background: rgba(24, 144, 255, 0.05);
            transition: all 0.2s ease;
            cursor: pointer;
        }
        .metrics-table tbody tr:hover {
            background: rgba(24, 144, 255, 0.12);
            transform: translateX(5px);
        }
        .metrics-table tbody td {
            padding: 16px;
            border-top: 1px solid rgba(24, 144, 255, 0.1);
            border-bottom: 1px solid rgba(24, 144, 255, 0.1);
        }
        .metrics-table tbody td:first-child {
            border-left: 1px solid rgba(24, 144, 255, 0.1);
            border-radius: 10px 0 0 10px;
        }
        .metrics-table tbody td:last-child {
            border-right: 1px solid rgba(24, 144, 255, 0.1);
            border-radius: 0 10px 10px 0;
        }
        .engine-badge {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
        }
        .engine-badge.faiss { background: linear-gradient(135deg, #ff4d4f 0%, #ff7875 100%); color: white; }
        .engine-badge.jvector { background: linear-gradient(135deg, #52c41a 0%, #73d13d 100%); color: white; }
        .engine-badge.lucene { background: linear-gradient(135deg, #1890ff 0%, #40a9ff 100%); color: white; }
        .metric-value {
            font-size: 18px;
            font-weight: 600;
            color: rgba(255, 255, 255, 0.9);
        }
        .metric-value.winner {
            color: #52c41a;
            font-weight: 700;
        }
        .metric-value.winner::after {
            content: ' 🏆';
            font-size: 14px;
        }
        .metric-unit {
            font-size: 12px;
            color: rgba(255, 255, 255, 0.5);
            margin-left: 4px;
        }
        .chart-container {
            background: rgba(10, 25, 41, 0.5);
            border-radius: 16px;
            padding: 24px;
            margin: 20px 0;
            border: 1px solid rgba(24, 144, 255, 0.1);
        }
        .chart-title {
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            color: rgba(255, 255, 255, 0.9);
        }
    '''


def get_html_header(title: str, include_chartjs: bool = False) -> str:
    """
    Get HTML header with title and optional Chart.js.
    
    Args:
        title: Page title
        include_chartjs: Whether to include Chart.js library
    
    Returns:
        HTML header string
    """
    chartjs_script = ''
    if include_chartjs:
        chartjs_script = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'
    
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {chartjs_script}
    <style>
{get_common_css()}
    </style>
</head>
<body>
<div class="container">'''


def get_html_footer() -> str:
    """Get HTML footer."""
    return '''
</div>
</body>
</html>'''


def get_breadcrumb(links: list) -> str:
    """
    Generate breadcrumb navigation.
    
    Args:
        links: List of tuples (text, url) for breadcrumb links
    
    Returns:
        HTML breadcrumb string
    """
    breadcrumb_html = '<div class="breadcrumb">'
    for i, (text, url) in enumerate(links):
        if i > 0:
            breadcrumb_html += '<span>›</span>'
        if url:
            breadcrumb_html += f'<a href="{url}">{text}</a>'
        else:
            breadcrumb_html += text
    breadcrumb_html += '</div>'
    return breadcrumb_html


def get_page_header(title: str, subtitle: Optional[str] = None, 
                   timestamp: Optional[str] = None) -> str:
    """
    Generate page header section.
    
    Args:
        title: Main title
        subtitle: Optional subtitle
        timestamp: Optional timestamp string
    
    Returns:
        HTML header section
    """
    subtitle_html = f'<div class="subtitle">{subtitle}</div>' if subtitle else ''
    timestamp_html = f'<div class="timestamp">Generated: {timestamp}</div>' if timestamp else ''
    
    return f'''
<div class="header">
    <h1>{title}</h1>
    {subtitle_html}
    {timestamp_html}
</div>'''


def get_engine_badge(engine: str) -> str:
    """
    Get HTML for engine badge.
    
    Args:
        engine: Engine name (faiss, jvector, lucene)
    
    Returns:
        HTML badge string
    """
    display_names = {
        'faiss': 'FAISS',
        'jvector': 'JVector',
        'lucene': 'Lucene'
    }
    display_name = display_names.get(engine, engine.upper())
    return f'<span class="engine-badge {engine}">{display_name}</span>'


def get_metric_value(value: float, unit: str = '', is_winner: bool = False) -> str:
    """
    Get formatted metric value HTML.
    
    Args:
        value: Metric value
        unit: Unit string (e.g., 'ops/s', 'ms')
        is_winner: Whether this is the winning value
    
    Returns:
        HTML metric value string
    """
    winner_class = ' winner' if is_winner else ''
    unit_html = f'<span class="metric-unit">{unit}</span>' if unit else ''
    return f'<span class="metric-value{winner_class}">{value:,.2f}{unit_html}</span>'

# Made with Bob
