"""
GKE Metrics Collector - Captures CPU, Memory, and Disk I/O metrics during benchmark runs.

This module collects resource metrics from GKE nodes and pods during benchmark execution,
storing them alongside benchmark results for correlation analysis.
"""

import time
import json
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from lib.kubectl_helper import KubectlHelper


class MetricsCollector:
    """Collects and stores GKE resource metrics during benchmark execution."""
    
    def __init__(self, namespace: str, results_dir: Path, enabled: bool = True):
        """
        Initialize the metrics collector.
        
        Args:
            namespace: Kubernetes namespace to monitor
            results_dir: Directory to store metrics data
            enabled: Whether metrics collection is enabled
        """
        self.namespace = namespace
        self.results_dir = results_dir
        self.enabled = enabled
        self.kubectl = KubectlHelper()
        self.stop_event = threading.Event()
        self.metrics_data = {
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
            "samples": [],
            "summary": {}
        }
    
    def _run_command(self, cmd: List[str]) -> Optional[str]:
        """Execute a shell command and return output."""
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"  ⚠️  Command failed: {' '.join(cmd)}")
            return None
    
    def _get_node_metrics(self) -> Dict[str, Any]:
        """
        Collect metrics for all nodes in the cluster.
        
        Returns:
            Dictionary with node metrics including CPU, memory, and disk usage
        """
        node_metrics = {}
        
        # Get all nodes
        nodes_output = self._run_command(["kubectl", "get", "nodes", "-o", "json"])
        if not nodes_output:
            return node_metrics
        
        try:
            nodes_data = json.loads(nodes_output)
            nodes = nodes_data.get("items", [])
            
            # Get metrics for each node
            for node in nodes:
                node_name = node["metadata"]["name"]
                node_pool = node["metadata"].get("labels", {}).get("cloud.google.com/gke-nodepool", "unknown")
                
                # Get node resource metrics using kubectl top
                top_output = self._run_command(["kubectl", "top", "node", node_name, "--no-headers"])
                
                if top_output:
                    parts = top_output.strip().split()
                    if len(parts) >= 5:
                        node_metrics[node_name] = {
                            "node_pool": node_pool,
                            "cpu_cores": parts[1],
                            "cpu_percent": parts[2],
                            "memory_bytes": parts[3],
                            "memory_percent": parts[4]
                        }
                
                # Get disk usage from node (if accessible)
                # Note: This requires metrics-server and may not show disk I/O directly
                
        except json.JSONDecodeError as e:
            print(f"  ⚠️  Failed to parse node data: {e}")
        
        return node_metrics
    
    def _get_pod_metrics(self) -> Dict[str, Any]:
        """
        Collect metrics for all pods in the namespace.
        
        Returns:
            Dictionary with pod metrics including CPU and memory usage
        """
        pod_metrics = {}
        
        # Get pod metrics using kubectl top
        top_output = self._run_command([
            "kubectl", "top", "pods",
            "-n", self.namespace,
            "--no-headers"
        ])
        
        if not top_output:
            return pod_metrics
        
        for line in top_output.strip().split('\n'):
            if not line:
                continue
            
            parts = line.split()
            if len(parts) >= 3:
                pod_name = parts[0]
                cpu = parts[1]
                memory = parts[2]
                
                pod_metrics[pod_name] = {
                    "cpu": cpu,
                    "memory": memory
                }
        
        return pod_metrics
    
    def _get_disk_io_metrics(self) -> Dict[str, Any]:
        """
        Collect disk I/O metrics from OpenSearch data nodes.
        
        Returns:
            Dictionary with disk I/O statistics per pod
        """
        disk_metrics = {}
        
        # Get OpenSearch data pods
        pods = self.kubectl.get_pods(self.namespace, label_selector="app=opensearch-data")
        
        for pod in pods:
            pod_name = pod["metadata"]["name"]
            
            # Try to get disk stats from the pod
            # This reads from /proc/diskstats inside the container
            cmd = [
                "kubectl", "exec", "-n", self.namespace,
                pod_name, "-c", "opensearch", "--",
                "cat", "/proc/diskstats"
            ]
            
            diskstats_output = self._run_command(cmd)
            
            if diskstats_output:
                # Parse diskstats for the data volume
                # Format: major minor name reads reads_merged sectors_read time_reading writes writes_merged sectors_written time_writing ...
                disk_metrics[pod_name] = {
                    "raw_diskstats": diskstats_output.strip(),
                    "timestamp": datetime.utcnow().isoformat()
                }
        
        return disk_metrics
    
    def _get_container_stats(self) -> Dict[str, Any]:
        """
        Get detailed container statistics including I/O.
        
        Returns:
            Dictionary with container-level statistics
        """
        container_stats = {}
        
        # Get pods in namespace
        pods = self.kubectl.get_pods(self.namespace)
        
        for pod in pods:
            pod_name = pod["metadata"]["name"]
            
            # Get pod details including resource usage
            pod_output = self._run_command([
                "kubectl", "get", "pod", pod_name,
                "-n", self.namespace,
                "-o", "json"
            ])
            
            if pod_output:
                try:
                    pod_data = json.loads(pod_output)
                    container_stats[pod_name] = {
                        "status": pod_data.get("status", {}).get("phase", "Unknown"),
                        "node": pod_data.get("spec", {}).get("nodeName", "Unknown"),
                        "containers": []
                    }
                    
                    # Get container statuses
                    for container_status in pod_data.get("status", {}).get("containerStatuses", []):
                        container_stats[pod_name]["containers"].append({
                            "name": container_status.get("name"),
                            "ready": container_status.get("ready"),
                            "restart_count": container_status.get("restartCount", 0)
                        })
                        
                except json.JSONDecodeError:
                    pass
        
        return container_stats
    
    def collect_sample(self) -> Dict[str, Any]:
        """
        Collect a single metrics sample.
        
        Returns:
            Dictionary containing all metrics for this sample
        """
        if not self.enabled:
            return {}
        
        sample = {
            "timestamp": datetime.utcnow().isoformat(),
            "epoch": time.time(),
            "node_metrics": self._get_node_metrics(),
            "pod_metrics": self._get_pod_metrics(),
            "disk_io": self._get_disk_io_metrics(),
            "container_stats": self._get_container_stats()
        }
        
        return sample
    
    def start_collection(self, scenario_name: str, interval: int = 10):
        """
        Start continuous metrics collection until stopped.
        
        Args:
            scenario_name: Name of the benchmark scenario
            interval: Seconds between samples (default: 10)
        """
        if not self.enabled:
            print("  ℹ️  Metrics collection is disabled")
            return
        
        print(f"\n📊 Starting metrics collection for {scenario_name}")
        print(f"  ⏱️  Interval: {interval}s (will run until benchmark completes)")
        
        self.stop_event.clear()
        self.metrics_data["start_time"] = datetime.utcnow().isoformat()
        self.metrics_data["scenario"] = scenario_name
        self.metrics_data["namespace"] = self.namespace
        self.metrics_data["interval_seconds"] = interval
        
        start_time = time.time()
        sample_count = 0
        
        while not self.stop_event.is_set():
            sample = self.collect_sample()
            if sample:
                self.metrics_data["samples"].append(sample)
                sample_count += 1
                # print(f"  📈 Collected sample {sample_count}", end='\r')
            
            # Sleep until next interval or stop event
            self.stop_event.wait(timeout=interval)
        
        self.metrics_data["end_time"] = datetime.utcnow().isoformat()
        self.metrics_data["duration_seconds"] = time.time() - start_time
        
        print(f"\n  ✅ Collected {sample_count} metric samples")
    
    def stop_collection(self):
        """Stop the metrics collection loop."""
        if self.enabled:
            print("\n  🛑 Stopping metrics collection...")
            self.stop_event.set()
    
    def collect_single_snapshot(self, label: str = "snapshot") -> Dict[str, Any]:
        """
        Collect a single snapshot of metrics.
        
        Args:
            label: Label for this snapshot
            
        Returns:
            Dictionary containing the snapshot data
        """
        if not self.enabled:
            return {}
        
        snapshot = {
            "label": label,
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": self.collect_sample()
        }
        
        return snapshot
    
    def calculate_summary(self):
        """Calculate summary statistics from collected samples."""
        if not self.metrics_data["samples"]:
            return
        
        # Calculate averages and peaks for node metrics
        node_summaries = {}
        pod_summaries = {}
        
        for sample in self.metrics_data["samples"]:
            # Aggregate node metrics
            for node_name, metrics in sample.get("node_metrics", {}).items():
                if node_name not in node_summaries:
                    node_summaries[node_name] = {
                        "node_pool": metrics.get("node_pool"),
                        "cpu_samples": [],
                        "memory_samples": []
                    }
                
                # Extract numeric values
                cpu_percent = metrics.get("cpu_percent", "0%").rstrip('%')
                memory_percent = metrics.get("memory_percent", "0%").rstrip('%')
                
                try:
                    node_summaries[node_name]["cpu_samples"].append(float(cpu_percent))
                    node_summaries[node_name]["memory_samples"].append(float(memory_percent))
                except ValueError:
                    pass
            
            # Aggregate pod metrics
            for pod_name, metrics in sample.get("pod_metrics", {}).items():
                if pod_name not in pod_summaries:
                    pod_summaries[pod_name] = {
                        "cpu_samples": [],
                        "memory_samples": []
                    }
                
                # Store raw values for now (parsing units is complex)
                pod_summaries[pod_name]["cpu_samples"].append(metrics.get("cpu", "0"))
                pod_summaries[pod_name]["memory_samples"].append(metrics.get("memory", "0"))
        
        # Calculate statistics
        for node_name, data in node_summaries.items():
            if data["cpu_samples"]:
                data["cpu_avg"] = sum(data["cpu_samples"]) / len(data["cpu_samples"])
                data["cpu_max"] = max(data["cpu_samples"])
                data["cpu_min"] = min(data["cpu_samples"])
            
            if data["memory_samples"]:
                data["memory_avg"] = sum(data["memory_samples"]) / len(data["memory_samples"])
                data["memory_max"] = max(data["memory_samples"])
                data["memory_min"] = min(data["memory_samples"])
            
            # Remove raw samples to reduce file size
            del data["cpu_samples"]
            del data["memory_samples"]
        
        self.metrics_data["summary"] = {
            "nodes": node_summaries,
            "pods": pod_summaries,
            "total_samples": len(self.metrics_data["samples"])
        }
    
    def save_metrics(self, scenario_name: str):
        """
        Save collected metrics to a JSON file.
        
        Args:
            scenario_name: Name of the benchmark scenario
        """
        if not self.enabled or not self.metrics_data["samples"]:
            return
        
        # Calculate summary statistics
        self.calculate_summary()
        
        # Create output directory
        output_dir = self.results_dir / scenario_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save full metrics data
        metrics_file = output_dir / "gke_metrics.json"
        with open(metrics_file, 'w') as f:
            json.dump(self.metrics_data, f, indent=2)
        
        print(f"\n  💾 Metrics saved to: {metrics_file}")
        
        # Save summary only (smaller file)
        summary_file = output_dir / "gke_metrics_summary.json"
        summary_data = {
            "scenario": self.metrics_data.get("scenario"),
            "namespace": self.metrics_data.get("namespace"),
            "start_time": self.metrics_data.get("start_time"),
            "end_time": self.metrics_data.get("end_time"),
            "duration_seconds": self.metrics_data.get("duration_seconds"),
            "summary": self.metrics_data.get("summary")
        }
        
        with open(summary_file, 'w') as f:
            json.dump(summary_data, f, indent=2)
        
        print(f"  💾 Summary saved to: {summary_file}")
        
        self.generate_dashboard(scenario_name)
    
    def reset(self):
        """Reset metrics data for a new collection run."""
        self.metrics_data = {
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
            "samples": [],
            "summary": {}
        }
    
    def generate_dashboard(self, scenario_name: str):
        """
        Generate an interactive dashboard with volcano blue theme.
        
        Args:
            scenario_name: Name of the benchmark scenario
        """
        if not self.enabled or not self.metrics_data["samples"]:
            return
        
        output_dir = self.results_dir / scenario_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Try to load test results if available
        test_results = None
        test_results_file = output_dir / "test_run.json"
        if test_results_file.exists():
            try:
                with open(test_results_file, 'r') as f:
                    test_results = json.load(f)
            except Exception as e:
                print(f"  ⚠️  Could not load test results: {e}")
        
        # Prepare chart data
        timestamps = []
        node_cpu_data = {}
        node_memory_data = {}
        pod_cpu_data = {}
        pod_memory_data = {}
        
        for sample in self.metrics_data["samples"]:
            # Format timestamp
            ts = sample["timestamp"].split("T")[1].split(".")[0]  # HH:MM:SS
            timestamps.append(ts)
            
            # Collect node metrics
            for node_name, metrics in sample.get("node_metrics", {}).items():
                if node_name not in node_cpu_data:
                    node_cpu_data[node_name] = []
                    node_memory_data[node_name] = []
                
                cpu_val = float(metrics.get("cpu_percent", "0%").rstrip('%'))
                mem_val = float(metrics.get("memory_percent", "0%").rstrip('%'))
                node_cpu_data[node_name].append(cpu_val)
                node_memory_data[node_name].append(mem_val)
            
            # Collect pod metrics
            for pod_name, metrics in sample.get("pod_metrics", {}).items():
                if pod_name not in pod_cpu_data:
                    pod_cpu_data[pod_name] = []
                    pod_memory_data[pod_name] = []
                
                # Parse CPU (e.g., "123m" -> 0.123 cores)
                cpu_str = metrics.get("cpu", "0m")
                cpu_val = float(cpu_str.rstrip('m')) / 1000 if 'm' in cpu_str else float(cpu_str)
                
                # Parse memory (e.g., "1234Mi" -> 1.234 GB)
                mem_str = metrics.get("memory", "0Mi")
                if 'Mi' in mem_str:
                    mem_val = float(mem_str.rstrip('Mi')) / 1024
                elif 'Gi' in mem_str:
                    mem_val = float(mem_str.rstrip('Gi'))
                else:
                    mem_val = 0
                
                pod_cpu_data[pod_name].append(cpu_val)
                pod_memory_data[pod_name].append(mem_val)
        
        # Generate color palette (volcano blue theme)
        def get_color(index, total):
            """Generate colors from blue to orange gradient."""
            colors = [
                '#1890ff', '#2f54eb', '#722ed1', '#eb2f96',
                '#f5222d', '#fa541c', '#fa8c16', '#faad14',
                '#52c41a', '#13c2c2', '#1890ff', '#2f54eb'
            ]
            return colors[index % len(colors)]
        
        # Create HTML dashboard
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VIS Actor Dashboard - {scenario_name}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0a1929 0%, #1a2332 50%, #0f1419 100%);
            min-height: 100vh;
            color: #fff;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1600px;
            margin: 0 auto;
        }}
        
        .header {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.1) 0%, rgba(114, 46, 209, 0.1) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 40px;
            margin-bottom: 30px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(24, 144, 255, 0.15);
        }}
        
        .header h1 {{
            font-size: 42px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 50%, #eb2f96 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 12px;
            letter-spacing: -0.5px;
        }}
        
        .header .subtitle {{
            font-size: 18px;
            color: rgba(255, 255, 255, 0.7);
            font-weight: 400;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        
        .stat-card {{
            background: linear-gradient(135deg, rgba(24, 144, 255, 0.08) 0%, rgba(114, 46, 209, 0.08) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(10px);
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }}
        
        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #1890ff, #722ed1, #eb2f96);
        }}
        
        .stat-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 12px 40px rgba(24, 144, 255, 0.25);
            border-color: rgba(24, 144, 255, 0.4);
        }}
        
        .stat-label {{
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: rgba(255, 255, 255, 0.5);
            margin-bottom: 12px;
        }}
        
        .stat-value {{
            font-size: 32px;
            font-weight: 700;
            background: linear-gradient(135deg, #1890ff, #722ed1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .stat-unit {{
            font-size: 16px;
            color: rgba(255, 255, 255, 0.6);
            margin-left: 6px;
        }}
        
        .tabs {{
            background: rgba(10, 25, 41, 0.6);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 16px;
            padding: 8px;
            margin-bottom: 30px;
            display: flex;
            gap: 8px;
            backdrop-filter: blur(10px);
        }}
        
        .tab {{
            flex: 1;
            padding: 16px 28px;
            border: none;
            background: transparent;
            border-radius: 12px;
            font-size: 15px;
            font-weight: 600;
            color: rgba(255, 255, 255, 0.6);
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
        }}
        
        .tab:hover {{
            background: rgba(24, 144, 255, 0.1);
            color: rgba(255, 255, 255, 0.9);
        }}
        
        .tab.active {{
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            color: white;
            box-shadow: 0 4px 20px rgba(24, 144, 255, 0.4);
        }}
        
        .tab-content {{
            display: none;
        }}
        
        .tab-content.active {{
            display: block;
            animation: fadeIn 0.4s ease;
        }}
        
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(10px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        
        .chart-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(700px, 1fr));
            gap: 30px;
            margin-bottom: 30px;
        }}
        
        .chart-card {{
            background: linear-gradient(135deg, rgba(10, 25, 41, 0.8) 0%, rgba(15, 20, 25, 0.8) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 30px;
            backdrop-filter: blur(10px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }}
        
        .chart-title {{
            font-size: 20px;
            font-weight: 700;
            color: #fff;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        
        .chart-icon {{
            width: 36px;
            height: 36px;
            border-radius: 10px;
            background: linear-gradient(135deg, #1890ff 0%, #722ed1 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            box-shadow: 0 4px 12px rgba(24, 144, 255, 0.3);
        }}
        
        .chart-container {{
            position: relative;
            height: 400px;
        }}
        
        .summary-section {{
            background: linear-gradient(135deg, rgba(10, 25, 41, 0.8) 0%, rgba(15, 20, 25, 0.8) 100%);
            border: 1px solid rgba(24, 144, 255, 0.2);
            border-radius: 20px;
            padding: 30px;
            backdrop-filter: blur(10px);
        }}
        
        .summary-title {{
            font-size: 24px;
            font-weight: 700;
            color: #fff;
            margin-bottom: 24px;
            background: linear-gradient(135deg, #1890ff, #722ed1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
        
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 20px;
        }}
        
        .summary-item {{
            padding: 20px;
            border-radius: 14px;
            background: rgba(24, 144, 255, 0.05);
            border-left: 4px solid #1890ff;
            transition: all 0.3s ease;
        }}
        
        .summary-item:hover {{
            background: rgba(24, 144, 255, 0.1);
            transform: translateX(4px);
        }}
        
        .summary-item h4 {{
            font-size: 15px;
            font-weight: 600;
            color: rgba(255, 255, 255, 0.9);
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .summary-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            font-size: 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }}
        
        .summary-row:last-child {{
            border-bottom: none;
        }}
        
        .summary-row .label {{
            color: rgba(255, 255, 255, 0.6);
        }}
        
        .summary-row .value {{
            font-weight: 600;
            color: #1890ff;
        }}
        
        .footer {{
            text-align: center;
            color: rgba(255, 255, 255, 0.5);
            margin-top: 50px;
            padding: 30px;
        }}
        
        @media (max-width: 768px) {{
            .chart-grid {{
                grid-template-columns: 1fr;
            }}
            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>⚡ OpenSearch Performance Dashboard</h1>
            <div class="subtitle">Real-time Resource Monitoring & Analytics</div>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Scenario</div>
                <div class="stat-value" style="font-size: 22px;">{scenario_name}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Namespace</div>
                <div class="stat-value" style="font-size: 22px;">{self.namespace}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Duration</div>
                <div class="stat-value">{self.metrics_data.get('duration_seconds', 0):.1f}<span class="stat-unit">sec</span></div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Samples</div>
                <div class="stat-value">{len(self.metrics_data['samples'])}</div>
            </div>
        </div>
        
        <div class="tabs">
            <button class="tab active" onclick="switchTab('nodes')">🖥️ Node Metrics</button>
            <button class="tab" onclick="switchTab('pods')">📦 Pod Metrics</button>
            <button class="tab" onclick="switchTab('benchmark')">🎯 Benchmark Results</button>
            <button class="tab" onclick="switchTab('summary')">📊 Summary</button>
        </div>
        
        <div id="nodes-content" class="tab-content active">
            <div class="chart-grid">
                <div class="chart-card">
                    <div class="chart-title">
                        <div class="chart-icon">📈</div>
                        Node CPU Utilization
                    </div>
                    <div class="chart-container">
                        <canvas id="nodeCpuChart"></canvas>
                    </div>
                </div>
                <div class="chart-card">
                    <div class="chart-title">
                        <div class="chart-icon">💾</div>
                        Node Memory Utilization
                    </div>
                    <div class="chart-container">
                        <canvas id="nodeMemoryChart"></canvas>
                    </div>
                </div>
            </div>
        </div>
        
        <div id="pods-content" class="tab-content">
            <div class="chart-grid">
                <div class="chart-card">
                    <div class="chart-title">
                        <div class="chart-icon">⚡</div>
                        Pod CPU Usage
                    </div>
                    <div class="chart-container">
                        <canvas id="podCpuChart"></canvas>
                    </div>
                </div>
                <div class="chart-card">
                    <div class="chart-title">
                        <div class="chart-icon">🗄️</div>
                        Pod Memory Usage
                    </div>
                    <div class="chart-container">
                        <canvas id="podMemoryChart"></canvas>
                    </div>
                </div>
            </div>
        </div>
        
        <div id="benchmark-content" class="tab-content">
            <div class="summary-section">
                <div class="summary-title">🎯 Benchmark Test Results</div>
                <div class="summary-grid">
"""
        
        # Add benchmark results if available
        if test_results and test_results.get('results', {}).get('op_metrics'):
            op_metrics = test_results['results']['op_metrics']
            
            # Find search operation metrics (usually the first one)
            search_metrics = op_metrics[0] if op_metrics else {}
            
            throughput = search_metrics.get('throughput', {})
            latency = search_metrics.get('latency', {})
            service_time = search_metrics.get('service_time', {})
            client_processing = search_metrics.get('client_processing_time', {})
            
            # Search Performance
            html_content += f"""
                    <div class="summary-item">
                        <h4>⚡ Search Performance</h4>
                        <div class="summary-row">
                            <span class="label">Mean Throughput:</span>
                            <span class="value">{throughput.get('mean', 0):.2f} ops/s</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Min Throughput:</span>
                            <span class="value">{throughput.get('min', 0):.2f} ops/s</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Max Throughput:</span>
                            <span class="value">{throughput.get('max', 0):.2f} ops/s</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Error Rate:</span>
                            <span class="value">{search_metrics.get('error_rate', 0):.1%}</span>
                        </div>
                    </div>
"""
            
            # Latency Metrics
            html_content += f"""
                    <div class="summary-item">
                        <h4>⏱️ Latency Metrics</h4>
                        <div class="summary-row">
                            <span class="label">Mean Latency:</span>
                            <span class="value">{latency.get('mean', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p50 Latency:</span>
                            <span class="value">{latency.get('50_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p90 Latency:</span>
                            <span class="value">{latency.get('90_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p99 Latency:</span>
                            <span class="value">{latency.get('99_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p99.9 Latency:</span>
                            <span class="value">{latency.get('99_9', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Max Latency:</span>
                            <span class="value">{latency.get('100_0', 0):.2f} ms</span>
                        </div>
                    </div>
"""
            
            # Service Time
            html_content += f"""
                    <div class="summary-item">
                        <h4>🔄 Service Time</h4>
                        <div class="summary-row">
                            <span class="label">Mean Service Time:</span>
                            <span class="value">{service_time.get('mean', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p50 Service Time:</span>
                            <span class="value">{service_time.get('50_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p90 Service Time:</span>
                            <span class="value">{service_time.get('90_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p99 Service Time:</span>
                            <span class="value">{service_time.get('99_0', 0):.2f} ms</span>
                        </div>
                    </div>
"""
            
            # Processing Time (if available)
            processing_time = search_metrics.get('processing_time', {})
            if processing_time:
                html_content += f"""
                    <div class="summary-item">
                        <h4>⚙️ Processing Time</h4>
                        <div class="summary-row">
                            <span class="label">Mean Processing:</span>
                            <span class="value">{processing_time.get('mean', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p50 Processing:</span>
                            <span class="value">{processing_time.get('50_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p90 Processing:</span>
                            <span class="value">{processing_time.get('90_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p99 Processing:</span>
                            <span class="value">{processing_time.get('99_0', 0):.2f} ms</span>
                        </div>
                    </div>
"""
            
            # Client Processing Time
            if client_processing:
                html_content += f"""
                    <div class="summary-item">
                        <h4>💻 Client Processing</h4>
                        <div class="summary-row">
                            <span class="label">Mean Client Time:</span>
                            <span class="value">{client_processing.get('mean', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p50 Client Time:</span>
                            <span class="value">{client_processing.get('50_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p90 Client Time:</span>
                            <span class="value">{client_processing.get('90_0', 0):.2f} ms</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">p99 Client Time:</span>
                            <span class="value">{client_processing.get('99_0', 0):.2f} ms</span>
                        </div>
                    </div>
"""
            
            # Index Statistics
            results = test_results.get('results', {})
            html_content += f"""
                    <div class="summary-item">
                        <h4>📊 Index Statistics</h4>
                        <div class="summary-row">
                            <span class="label">Document Count:</span>
                            <span class="value">{results.get('docs_count', 0):,}</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Store Size:</span>
                            <span class="value">{results.get('store_size', 0) / (1024**3):.2f} GB</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Segment Count:</span>
                            <span class="value">{results.get('segment_count', 0)}</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Translog Size:</span>
                            <span class="value">{results.get('translog_size', 0)} bytes</span>
                        </div>
                    </div>
"""
            
            # Merge Operations
            html_content += f"""
                    <div class="summary-item">
                        <h4>🔧 Merge Operations</h4>
                        <div class="summary-row">
                            <span class="label">Total Merge Time:</span>
                            <span class="value">{results.get('merge_time', 0) / 1000:.2f} sec</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Merge Count:</span>
                            <span class="value">{results.get('merge_count', 0)}</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Merge Throttle Time:</span>
                            <span class="value">{results.get('merge_throttle_time', 0) / 1000:.2f} sec</span>
                        </div>
                    </div>
"""
            
            # Refresh & Flush
            html_content += f"""
                    <div class="summary-item">
                        <h4>🔄 Refresh & Flush</h4>
                        <div class="summary-row">
                            <span class="label">Refresh Time:</span>
                            <span class="value">{results.get('refresh_time', 0) / 1000:.2f} sec</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Refresh Count:</span>
                            <span class="value">{results.get('refresh_count', 0)}</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Flush Time:</span>
                            <span class="value">{results.get('flush_time', 0) / 1000:.2f} sec</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Flush Count:</span>
                            <span class="value">{results.get('flush_count', 0)}</span>
                        </div>
                    </div>
"""
        else:
            # No benchmark data available
            html_content += """
                    <div class="summary-item">
                        <h4>⚠️ No Benchmark Data Available</h4>
                        <div class="summary-row">
                            <span class="label">Status:</span>
                            <span class="value">Benchmark results not loaded</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Note:</span>
                            <span class="value">Run benchmark to see results here</span>
                        </div>
                    </div>
"""
        
        html_content += """
                </div>
            </div>
        </div>
        
        <div id="summary-content" class="tab-content">
            <div class="summary-section">
                <div class="summary-title">Performance Summary</div>
                <div class="summary-grid">
"""
        
        # Add node summaries
        for node_name, summary in self.metrics_data.get("summary", {}).get("nodes", {}).items():
            html_content += f"""
                    <div class="summary-item">
                        <h4>🖥️ {node_name}</h4>
                        <div class="summary-row">
                            <span class="label">Node Pool:</span>
                            <span class="value">{summary.get('node_pool', 'unknown')}</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Avg CPU:</span>
                            <span class="value">{summary.get('cpu_avg', 0):.1f}%</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Peak CPU:</span>
                            <span class="value">{summary.get('cpu_max', 0):.1f}%</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Avg Memory:</span>
                            <span class="value">{summary.get('memory_avg', 0):.1f}%</span>
                        </div>
                        <div class="summary-row">
                            <span class="label">Peak Memory:</span>
                            <span class="value">{summary.get('memory_max', 0):.1f}%</span>
                        </div>
                    </div>
"""
        
        html_content += """
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p style="font-size: 16px; margin-bottom: 10px; font-weight: 600;">OpenSearch Benchmark Automation</p>
            <p style="font-size: 13px;">VIS Actor-inspired dashboard powered by Chart.js</p>
        </div>
    </div>
    
    <script>
        // Chart.js dark theme configuration
        const chartOptions = {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 20,
                        font: {
                            size: 13,
                            weight: '600',
                            family: "'SF Pro Display', -apple-system, sans-serif"
                        },
                        color: 'rgba(255, 255, 255, 0.8)',
                        usePointStyle: true,
                        pointStyle: 'circle'
                    }
                },
                tooltip: {
                    backgroundColor: 'rgba(10, 25, 41, 0.95)',
                    padding: 16,
                    titleFont: {
                        size: 15,
                        weight: 'bold'
                    },
                    bodyFont: {
                        size: 14
                    },
                    borderColor: 'rgba(24, 144, 255, 0.5)',
                    borderWidth: 2,
                    cornerRadius: 8
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(24, 144, 255, 0.1)',
                        lineWidth: 1
                    },
                    ticks: {
                        font: {
                            size: 12,
                            family: "'SF Pro Display', -apple-system, sans-serif"
                        },
                        color: 'rgba(255, 255, 255, 0.7)'
                    }
                },
                x: {
                    grid: {
                        display: false
                    },
                    ticks: {
                        font: {
                            size: 11,
                            family: "'SF Pro Display', -apple-system, sans-serif"
                        },
                        color: 'rgba(255, 255, 255, 0.6)',
                        maxRotation: 45,
                        minRotation: 45
                    }
                }
            }
        };
        
        // Prepare datasets for Node CPU
        const nodeCpuDatasets = [
"""
        
        # Add node CPU datasets
        for idx, (node_name, data) in enumerate(node_cpu_data.items()):
            color = get_color(idx, len(node_cpu_data))
            html_content += f"""
            {{
                label: '{node_name}',
                data: {json.dumps(data)},
                borderColor: '{color}',
                backgroundColor: '{color}33',
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                pointRadius: 3,
                pointHoverRadius: 6
            }},
"""
        
        html_content += """
        ];
        
        // Prepare datasets for Node Memory
        const nodeMemoryDatasets = [
"""
        
        # Add node memory datasets
        for idx, (node_name, data) in enumerate(node_memory_data.items()):
            color = get_color(idx, len(node_memory_data))
            html_content += f"""
            {{
                label: '{node_name}',
                data: {json.dumps(data)},
                borderColor: '{color}',
                backgroundColor: '{color}33',
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                pointRadius: 3,
                pointHoverRadius: 6
            }},
"""
        
        html_content += """
        ];
        
        // Prepare datasets for Pod CPU
        const podCpuDatasets = [
"""
        
        # Add pod CPU datasets
        for idx, (pod_name, data) in enumerate(pod_cpu_data.items()):
            color = get_color(idx, len(pod_cpu_data))
            html_content += f"""
            {{
                label: '{pod_name}',
                data: {json.dumps(data)},
                borderColor: '{color}',
                backgroundColor: '{color}33',
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                pointRadius: 3,
                pointHoverRadius: 6
            }},
"""
        
        html_content += """
        ];
        
        // Prepare datasets for Pod Memory
        const podMemoryDatasets = [
"""
        
        # Add pod memory datasets
        for idx, (pod_name, data) in enumerate(pod_memory_data.items()):
            color = get_color(idx, len(pod_memory_data))
            html_content += f"""
            {{
                label: '{pod_name}',
                data: {json.dumps(data)},
                borderColor: '{color}',
                backgroundColor: '{color}33',
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                pointRadius: 3,
                pointHoverRadius: 6
            }},
"""
        
        html_content += f"""
        ];
        
        // Create charts using Object.assign instead of spread operators for better browser compatibility
        const nodeCpuConfig = Object.assign({{}}, chartOptions);
        nodeCpuConfig.scales = Object.assign({{}}, chartOptions.scales);
        nodeCpuConfig.scales.y = Object.assign({{}}, chartOptions.scales.y, {{
            max: 100,
            title: {{
                display: true,
                text: 'CPU Usage (%)',
                font: {{ weight: 'bold', size: 14 }},
                color: 'rgba(255, 255, 255, 0.8)'
            }}
        }});
        
        new Chart(document.getElementById('nodeCpuChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(timestamps)},
                datasets: nodeCpuDatasets
            }},
            options: nodeCpuConfig
        }});
        
        const nodeMemoryConfig = Object.assign({{}}, chartOptions);
        nodeMemoryConfig.scales = Object.assign({{}}, chartOptions.scales);
        nodeMemoryConfig.scales.y = Object.assign({{}}, chartOptions.scales.y, {{
            max: 100,
            title: {{
                display: true,
                text: 'Memory Usage (%)',
                font: {{ weight: 'bold', size: 14 }},
                color: 'rgba(255, 255, 255, 0.8)'
            }}
        }});
        
        new Chart(document.getElementById('nodeMemoryChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(timestamps)},
                datasets: nodeMemoryDatasets
            }},
            options: nodeMemoryConfig
        }});
        
        const podCpuConfig = Object.assign({{}}, chartOptions);
        podCpuConfig.scales = Object.assign({{}}, chartOptions.scales);
        podCpuConfig.scales.y = Object.assign({{}}, chartOptions.scales.y, {{
            title: {{
                display: true,
                text: 'CPU Usage (cores)',
                font: {{ weight: 'bold', size: 14 }},
                color: 'rgba(255, 255, 255, 0.8)'
            }}
        }});
        
        new Chart(document.getElementById('podCpuChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(timestamps)},
                datasets: podCpuDatasets
            }},
            options: podCpuConfig
        }});
        
        const podMemoryConfig = Object.assign({{}}, chartOptions);
        podMemoryConfig.scales = Object.assign({{}}, chartOptions.scales);
        podMemoryConfig.scales.y = Object.assign({{}}, chartOptions.scales.y, {{
            title: {{
                display: true,
                text: 'Memory Usage (GB)',
                font: {{ weight: 'bold', size: 14 }},
                color: 'rgba(255, 255, 255, 0.8)'
            }}
        }});
        
        new Chart(document.getElementById('podMemoryChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(timestamps)},
                datasets: podMemoryDatasets
            }},
            options: podMemoryConfig
        }});
        
        // Tab switching
        function switchTab(tabName) {{
            document.querySelectorAll('.tab-content').forEach(content => {{
                content.classList.remove('active');
            }});
            
            document.querySelectorAll('.tab').forEach(tab => {{
                tab.classList.remove('active');
            }});
            
            document.getElementById(tabName + '-content').classList.add('active');
            event.target.classList.add('active');
        }}
    </script>
</body>
</html>
"""
        
        # Save dashboard
        dashboard_file = output_dir / "results.html"
        with open(dashboard_file, 'w') as f:
            f.write(html_content)
        
        print(f"  🎨 Performance Dashboard saved to: {dashboard_file}")

# Made with Bob
