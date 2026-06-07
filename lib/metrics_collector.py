"""
GKE Metrics Collector - Captures CPU, Memory, and Disk I/O metrics during benchmark runs.

This module collects resource metrics from GKE nodes and pods during benchmark execution,
storing them alongside benchmark results for correlation analysis.
"""

import time
import json
import subprocess
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
    
    def start_collection(self, scenario_name: str, interval: int = 10, duration: int = 300):
        """
        Start continuous metrics collection in the background.
        
        Args:
            scenario_name: Name of the benchmark scenario
            interval: Seconds between samples (default: 10)
            duration: Total duration to collect metrics (default: 300)
        """
        if not self.enabled:
            print("  ℹ️  Metrics collection is disabled")
            return
        
        print(f"\n📊 Starting metrics collection for {scenario_name}")
        print(f"  ⏱️  Interval: {interval}s, Duration: {duration}s")
        
        self.metrics_data["start_time"] = datetime.utcnow().isoformat()
        self.metrics_data["scenario"] = scenario_name
        self.metrics_data["namespace"] = self.namespace
        self.metrics_data["interval_seconds"] = interval
        
        start_time = time.time()
        end_time = start_time + duration
        sample_count = 0
        
        while time.time() < end_time:
            sample = self.collect_sample()
            if sample:
                self.metrics_data["samples"].append(sample)
                sample_count += 1
                print(f"  📈 Collected sample {sample_count}", end='\r')
            
            # Sleep until next interval
            time.sleep(interval)
        
        self.metrics_data["end_time"] = datetime.utcnow().isoformat()
        self.metrics_data["duration_seconds"] = time.time() - start_time
        
        print(f"\n  ✅ Collected {sample_count} metric samples")
    
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
    
    def reset(self):
        """Reset metrics data for a new collection run."""
        self.metrics_data = {
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
            "samples": [],
            "summary": {}
        }

# Made with Bob
