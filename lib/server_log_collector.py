"""
Server log collector for OpenSearch pods on server-pool nodes.
Automatically captures logs during benchmark execution.
"""

import subprocess
import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


class ServerLogCollector:
    """Collects logs from OpenSearch pods running on server-pool nodes."""
    
    def __init__(self, namespace: str, results_dir: Path):
        """
        Initialize server log collector.
        
        Args:
            namespace: Kubernetes namespace (e.g., os-jvector, os-faiss, os-lucene)
            results_dir: Directory to save logs (top-level results directory)
        """
        self.namespace = namespace
        self.results_dir = results_dir
        # Create server-logs directory at top level, organized by namespace
        self.server_logs_dir = results_dir / "server-logs" / namespace
        self.server_logs_dir.mkdir(parents=True, exist_ok=True)
    
    def get_server_pool_pods(self) -> List[Dict[str, str]]:
        """
        Get list of OpenSearch pods running on server-pool nodes.
        
        Returns:
            List of pod dictionaries with name and node information
        """
        cmd = [
            "kubectl", "get", "pods",
            "-n", self.namespace,
            "-o", "json"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pods_data = json.loads(result.stdout)
            
            server_pool_pods = []
            for pod in pods_data.get("items", []):
                pod_name = pod["metadata"]["name"]
                node_name = pod["spec"].get("nodeName", "")
                
                # Check if pod is on server-pool node
                if node_name:
                    # Get node labels to verify it's in server-pool
                    node_cmd = [
                        "kubectl", "get", "node", node_name,
                        "-o", "jsonpath={.metadata.labels.cloud\\.google\\.com/gke-nodepool}"
                    ]
                    try:
                        node_result = subprocess.run(node_cmd, capture_output=True, text=True, check=True)
                        node_pool = node_result.stdout.strip()
                        
                        if node_pool == "server-pool":
                            server_pool_pods.append({
                                "name": pod_name,
                                "node": node_name,
                                "status": pod["status"].get("phase", "Unknown")
                            })
                    except subprocess.CalledProcessError:
                        continue
            
            return server_pool_pods
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            print(f"  ⚠️  Failed to get server-pool pods: {e}")
            return []
    
    def get_pod_containers(self, pod_name: str) -> List[str]:
        """Get list of containers in a pod."""
        cmd = [
            "kubectl", "get", "pod", pod_name,
            "-n", self.namespace,
            "-o", "jsonpath={.spec.containers[*].name}"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip().split()
        except subprocess.CalledProcessError:
            return []
    
    def get_pod_logs(self, pod_name: str, container: Optional[str] = None,
                     tail_lines: int = 5000) -> str:
        """
        Get logs from a pod.
        
        Args:
            pod_name: Name of the pod
            container: Optional container name
            tail_lines: Number of lines to tail
            
        Returns:
            Log content as string
        """
        cmd = ["kubectl", "logs", "-n", self.namespace, pod_name, "--tail", str(tail_lines)]
        
        if container:
            cmd.extend(["-c", container])
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                return f"Error getting logs: {result.stderr}"
            return result.stdout
        except Exception as e:
            return f"Exception getting logs: {str(e)}"
    
    def save_log(self, pod_name: str, content: str, container: str = ""):
        """Save log content to file."""
        filename = f"{pod_name}"
        if container:
            filename += f"-{container}"
        filename += ".log"
        
        log_file = self.server_logs_dir / filename
        
        try:
            log_file.write_text(content)
            size = len(content)
            size_str = self._format_size(size)
            print(f"    ✅ {filename} ({size_str})")
        except Exception as e:
            print(f"    ❌ Failed to save {filename}: {e}")
    
    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        size_float = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_float < 1024.0:
                return f"{size_float:.1f}{unit}"
            size_float /= 1024.0
        return f"{size_float:.1f}TB"
    
    def collect_logs(self, scenario_name: str = "", tail_lines: int = 5000):
        """
        Collect logs from all server-pool pods.
        Gracefully handles errors to avoid disrupting benchmark execution.
        
        Args:
            scenario_name: Optional scenario name for logging context
            tail_lines: Number of lines to tail from each log
        """
        try:
            print(f"\n📋 Collecting server logs from {self.namespace}...")
            
            pods = self.get_server_pool_pods()
            
            if not pods:
                print(f"  ℹ️  No server-pool pods found in {self.namespace}")
                return
            
            print(f"  Found {len(pods)} server-pool pod(s)")
            
            for pod in pods:
                pod_name = pod.get("name", "unknown")
                try:
                    print(f"  📄 {pod_name}")
                    
                    # Get containers in the pod
                    containers = self.get_pod_containers(pod_name)
                    
                    if not containers:
                        # Try to get logs without specifying container
                        logs = self.get_pod_logs(pod_name, tail_lines=tail_lines)
                        self.save_log(pod_name, logs)
                    else:
                        # Get logs from each container
                        for container in containers:
                            try:
                                logs = self.get_pod_logs(pod_name, container=container, tail_lines=tail_lines)
                                self.save_log(pod_name, logs, container=container)
                            except Exception as e:
                                print(f"    ⚠️  Failed to collect logs from container {container}: {e}")
                except Exception as e:
                    print(f"  ⚠️  Failed to collect logs from pod {pod_name}: {e}")
            
            # Create summary file
            try:
                self._create_summary(scenario_name, len(pods))
            except Exception as e:
                print(f"  ⚠️  Failed to create summary file: {e}")
                
        except Exception as e:
            print(f"  ❌ Error during log collection: {e}")
            print(f"  ℹ️  Continuing with benchmark execution...")
    
    def _create_summary(self, scenario_name: str, pod_count: int):
        """Create a summary file of collected logs."""
        summary_file = self.server_logs_dir / "SUMMARY.txt"
        
        lines = []
        lines.append("Server Log Collection Summary")
        lines.append("=" * 60)
        lines.append(f"Collection Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"Namespace: {self.namespace}")
        if scenario_name:
            lines.append(f"Scenario: {scenario_name}")
        lines.append(f"Server-pool Pods: {pod_count}")
        lines.append("")
        lines.append("Collected Logs:")
        lines.append("-" * 60)
        
        log_files = sorted(self.server_logs_dir.glob("*.log"))
        if log_files:
            for log_file in log_files:
                size = log_file.stat().st_size
                size_str = self._format_size(size)
                lines.append(f"  {log_file.name:40s} {size_str:>10s}")
        else:
            lines.append("  No logs collected")
        
        summary_file.write_text("\n".join(lines))

# Made with Bob