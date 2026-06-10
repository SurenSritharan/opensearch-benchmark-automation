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
    
    def get_pod_file(self, pod_name: str, file_path: str, container: str = "opensearch") -> str:
        """
        Get file content from a pod.
        
        Args:
            pod_name: Name of the pod
            file_path: Path to file inside the pod
            container: Container name (default: opensearch)
            
        Returns:
            File content as string
        """
        cmd = [
            "kubectl", "exec", "-n", self.namespace,
            pod_name, "-c", container, "--",
            "cat", file_path
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                return f"Error reading file: {result.stderr}"
            return result.stdout
        except Exception as e:
            return f"Exception reading file: {str(e)}"
    
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
    
    def collect_gc_logs(self, scenario_name: str = ""):
        """
        Collect GC log files from all server-pool pods.
        GC logs are located at /usr/share/opensearch/logs/gc.log
        
        Args:
            scenario_name: Optional scenario name for logging context
        """
        try:
            print(f"\n🗑️  Collecting GC logs from {self.namespace}...")
            
            pods = self.get_server_pool_pods()
            
            if not pods:
                print(f"  ℹ️  No server-pool pods found in {self.namespace}")
                return
            
            print(f"  Found {len(pods)} server-pool pod(s)")
            
            gc_log_path = "/usr/share/opensearch/logs/gc.log"
            
            for pod in pods:
                pod_name = pod.get("name", "unknown")
                try:
                    print(f"  📄 {pod_name}")
                    
                    # Get GC log file
                    gc_content = self.get_pod_file(pod_name, gc_log_path, container="opensearch")
                    
                    if gc_content and not gc_content.startswith("Error") and not gc_content.startswith("Exception"):
                        # Save GC log with .gc.log extension
                        filename = f"{pod_name}-gc.log"
                        log_file = self.server_logs_dir / filename
                        
                        log_file.write_text(gc_content)
                        size = len(gc_content)
                        size_str = self._format_size(size)
                        print(f"    ✅ {filename} ({size_str})")
                    else:
                        print(f"    ⚠️  GC log not found or empty")
                        
                except Exception as e:
                    print(f"  ⚠️  Failed to collect GC log from pod {pod_name}: {e}")
            
            print(f"  ✅ GC log collection complete")
                    
        except Exception as e:
            print(f"  ❌ Error during GC log collection: {e}")
            print(f"  ℹ️  Continuing with benchmark execution...")
    
    def collect_heap_dumps(self, scenario_name: str = ""):
        """
        Collect heap dump files from all server-pool pods.
        Heap dumps are generated on OOM at /usr/share/opensearch/data/
        
        Args:
            scenario_name: Optional scenario name for logging context
        """
        try:
            print(f"\n💾 Collecting heap dumps from {self.namespace}...")
            
            pods = self.get_server_pool_pods()
            
            if not pods:
                print(f"  ℹ️  No server-pool pods found in {self.namespace}")
                return
            
            print(f"  Found {len(pods)} server-pool pod(s)")
            
            heap_dump_found = False
            
            for pod in pods:
                pod_name = pod.get("name", "unknown")
                try:
                    print(f"  📄 {pod_name}")
                    
                    # List heap dump files in data directory
                    list_cmd = [
                        "kubectl", "exec", "-n", self.namespace,
                        pod_name, "-c", "opensearch", "--",
                        "sh", "-c", "ls -1 /usr/share/opensearch/data/*.hprof 2>/dev/null || true"
                    ]
                    
                    result = subprocess.run(list_cmd, capture_output=True, text=True, check=False)
                    heap_dump_files = [f.strip() for f in result.stdout.split('\n') if f.strip()]
                    
                    if heap_dump_files:
                        for heap_dump_path in heap_dump_files:
                            heap_dump_name = Path(heap_dump_path).name
                            print(f"    🔍 Found heap dump: {heap_dump_name}")
                            
                            # Get heap dump file (this may be large)
                            heap_content = self.get_pod_file(pod_name, heap_dump_path, container="opensearch")
                            
                            if heap_content and not heap_content.startswith("Error") and not heap_content.startswith("Exception"):
                                # Save heap dump
                                filename = f"{pod_name}-{heap_dump_name}"
                                dump_file = self.server_logs_dir / filename
                                
                                dump_file.write_text(heap_content, encoding='latin-1')  # Binary data
                                size = len(heap_content)
                                size_str = self._format_size(size)
                                print(f"    ✅ {filename} ({size_str})")
                                heap_dump_found = True
                            else:
                                print(f"    ⚠️  Failed to read heap dump: {heap_dump_path}")
                    else:
                        print(f"    ℹ️  No heap dumps found")
                        
                except Exception as e:
                    print(f"  ⚠️  Failed to collect heap dumps from pod {pod_name}: {e}")
            
            if heap_dump_found:
                print(f"  ✅ Heap dump collection complete")
            else:
                print(f"  ℹ️  No heap dumps found (this is normal if no OOM occurred)")
                    
        except Exception as e:
            print(f"  ❌ Error during heap dump collection: {e}")
            print(f"  ℹ️  Continuing with benchmark execution...")
    
    def collect_all_logs(self, scenario_name: str = "", tail_lines: int = 5000):
        """
        Collect server logs, GC logs, and heap dumps from all server-pool pods.
        
        Args:
            scenario_name: Optional scenario name for logging context
            tail_lines: Number of lines to tail from server logs
        """
        self.collect_logs(scenario_name=scenario_name, tail_lines=tail_lines)
        self.collect_gc_logs(scenario_name=scenario_name)
        self.collect_heap_dumps(scenario_name=scenario_name)
    
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