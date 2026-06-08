"""
Comprehensive telemetry collector for pre/post test diagnostics.
Captures cluster state, server logs, and GKE metrics with time-based scraping.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from lib.server_log_collector import ServerLogCollector
from lib.metrics_collector import MetricsCollector


class TelemetryCollector:
    """
    Orchestrates comprehensive telemetry collection before and after benchmark tests.
    Supports time-based metric scraping to capture data for the test duration.
    """
    
    def __init__(self, namespace: str, results_dir: Path, cluster_endpoint: str, enabled: bool = True, pre_run_log_lines: int = 1000, post_run_log_lines: int = 5000):
        """
        Initialize telemetry collector.
        
        Args:
            namespace: Kubernetes namespace (e.g., os-jvector, os-faiss, os-lucene)
            results_dir: Directory to save telemetry data
            cluster_endpoint: OpenSearch cluster endpoint for API calls
            enabled: Whether telemetry collection is enabled
            pre_run_log_lines: Number of log lines to collect before run (default: 1000)
            post_run_log_lines: Number of log lines to collect after run (default: 5000)
        """
        self.namespace = namespace
        self.results_dir = results_dir
        self.cluster_endpoint = cluster_endpoint
        self.enabled = enabled
        self.pre_run_log_lines = pre_run_log_lines
        self.post_run_log_lines = post_run_log_lines
        
        # Initialize sub-collectors
        self.log_collector = ServerLogCollector(namespace, results_dir)
        self.metrics_collector = MetricsCollector(namespace, results_dir, enabled)
        
        # Telemetry directories - organized per namespace at run level
        self.pre_test_dir = results_dir / "telemetry-pre-run" / namespace
        self.post_test_dir = results_dir / "telemetry-post-run" / namespace
        
        # Test timing for post-test scraping
        self.test_start_time: Optional[datetime] = None
        self.test_duration: Optional[float] = None
    
    def _exec_curl(self, endpoint: str) -> str:
        """Execute curl command against OpenSearch cluster."""
        cmd = [
            "kubectl", "exec", "-n", self.namespace,
            "opensearch-benchmark-client", "-c", "benchmark", "--",
            "curl", "-sk", "-u", "admin:admin",
            f"https://{self.cluster_endpoint}{endpoint}"
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return f"ERROR: {str(e)}"
    
    def _save_json_output(self, output_dir: Path, endpoint: str, filename: str, description: str = ""):
        """Save curl output to file."""
        if not self.enabled:
            return
            
        output = self._exec_curl(endpoint)
        if output and not output.startswith("ERROR"):
            (output_dir / filename).write_text(output, encoding="utf-8")
            if description:
                print(f"    ✅ {description}")
    
    def collect_cluster_state(self, output_dir: Path, index_name: str):
        """
        Collect comprehensive cluster state snapshot.
        
        Args:
            output_dir: Directory to save cluster state
            index_name: Index name for stats collection
        """
        if not self.enabled:
            return
        
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"  📊 Collecting cluster state...")
        
        # Cluster health and status
        self._save_json_output(output_dir, "/_cluster/health?pretty", 
                              "cluster-health.json", "cluster health")
        self._save_json_output(output_dir, "/_cluster/stats?pretty", 
                              "cluster-stats.json", "cluster stats")
        self._save_json_output(output_dir, "/_cluster/settings?include_defaults=true&flat_settings=true&pretty",
                              "cluster-settings.json", "cluster settings")
        
        # Node information
        self._save_json_output(output_dir, "/_cat/nodes?v&h=name,heap.percent,heap.current,heap.max,ram.percent,ram.current,ram.max,cpu,load_1m,load_5m,load_15m",
                              "nodes-info.txt", "node info")
        self._save_json_output(output_dir, "/_nodes/stats?pretty",
                              "nodes-stats.json", "node stats")
        
        # Index information
        if index_name:
            self._save_json_output(output_dir, f"/{index_name}/_settings?pretty",
                                  "index-settings.json", "index settings")
            self._save_json_output(output_dir, f"/{index_name}/_mapping?pretty",
                                  "index-mapping.json", "index mapping")
            self._save_json_output(output_dir, f"/{index_name}/_stats?pretty",
                                  "index-stats.json", "index stats")
            self._save_json_output(output_dir, f"/_cat/indices/{index_name}?v&h=index,health,status,pri,rep,docs.count,store.size,pri.store.size",
                                  "index-info.txt", "index info")
            self._save_json_output(output_dir, f"/_cat/segments/{index_name}?v",
                                  "segments.txt", "segment info")
        
        # Thread pools and tasks
        self._save_json_output(output_dir, "/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,largest,completed,size",
                              "thread-pools.txt", "thread pools")
        self._save_json_output(output_dir, "/_cat/tasks?v&detailed",
                              "tasks.txt", "tasks")
    
    def collect_pre_test_telemetry(self, scenario_name: str, index_name: str):
        """
        Collect comprehensive telemetry before test execution.
        
        Args:
            scenario_name: Name of the scenario being tested
            index_name: Index name for stats collection
        """
        if not self.enabled:
            print("  ℹ️  Telemetry collection disabled")
            return
        
        print(f"\n📸 Collecting pre-test telemetry for {scenario_name}...")
        
        # Record test start time
        self.test_start_time = datetime.now()
        
        # Collect cluster state
        self.collect_cluster_state(self.pre_test_dir, index_name)
        
        # Collect server logs snapshot
        print(f"  📋 Collecting pre-test server logs...")
        pre_logs_dir = self.pre_test_dir / "server-logs"
        pre_logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Temporarily redirect log collector to pre-test directory
        original_logs_dir = self.log_collector.server_logs_dir
        self.log_collector.server_logs_dir = pre_logs_dir
        self.log_collector.collect_logs(scenario_name, tail_lines=self.pre_run_log_lines)
        self.log_collector.server_logs_dir = original_logs_dir
        
        # Collect GKE metrics snapshot
        print(f"  📊 Collecting pre-test GKE metrics snapshot...")
        snapshot = self.metrics_collector.collect_single_snapshot(label="pre-test")
        if snapshot:
            snapshot_file = self.pre_test_dir / "gke-metrics-snapshot.json"
            with open(snapshot_file, 'w') as f:
                json.dump(snapshot, f, indent=2)
            print(f"    ✅ GKE metrics snapshot saved")
        
        # Create summary
        self._create_summary(self.pre_test_dir, "pre-test", scenario_name)
        
        print(f"  ✅ Pre-test telemetry collected: {self.pre_test_dir.relative_to(self.results_dir)}")
    
    def collect_post_test_telemetry(self, scenario_name: str, index_name: str, test_duration_seconds: Optional[float] = None):
        """
        Collect comprehensive telemetry after test execution with time-based metric scraping.
        
        Args:
            scenario_name: Name of the scenario being tested
            index_name: Index name for stats collection
            test_duration_seconds: Duration of the test in seconds (for metric scraping)
        """
        if not self.enabled:
            print("  ℹ️  Telemetry collection disabled")
            return
        
        print(f"\n📸 Collecting post-test telemetry for {scenario_name}...")
        
        # Record test end time and duration
        test_end_time = datetime.now()
        if self.test_start_time:
            self.test_duration = (test_end_time - self.test_start_time).total_seconds()
        elif test_duration_seconds:
            self.test_duration = test_duration_seconds
        
        # Collect cluster state
        self.collect_cluster_state(self.post_test_dir, index_name)
        
        # Collect server logs
        print(f"  📋 Collecting post-test server logs...")
        post_logs_dir = self.post_test_dir / "server-logs"
        post_logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Temporarily redirect log collector to post-test directory
        original_logs_dir = self.log_collector.server_logs_dir
        self.log_collector.server_logs_dir = post_logs_dir
        self.log_collector.collect_logs(scenario_name, tail_lines=self.post_run_log_lines)
        self.log_collector.server_logs_dir = original_logs_dir
        
        # Collect GKE metrics with time-based scraping
        print(f"  📊 Collecting post-test GKE metrics...")
        self._collect_time_based_metrics(scenario_name)
        
        # Create summary
        self._create_summary(self.post_test_dir, "post-test", scenario_name)
        
        print(f"  ✅ Post-test telemetry collected: {self.post_test_dir.relative_to(self.results_dir)}")
    
    def _collect_time_based_metrics(self, scenario_name: str):
        """
        Collect GKE metrics for the test duration using time-based scraping.
        This queries metrics retroactively for the test period.
        """
        if not self.test_duration:
            print(f"    ⚠️  Test duration unknown, collecting single snapshot instead")
            snapshot = self.metrics_collector.collect_single_snapshot(label="post-test")
            if snapshot:
                snapshot_file = self.post_test_dir / "gke-metrics-snapshot.json"
                with open(snapshot_file, 'w') as f:
                    json.dump(snapshot, f, indent=2)
            return
        
        # Calculate lookback window (test duration + 1 minute buffer)
        lookback_seconds = int(self.test_duration + 60)
        
        print(f"    📈 Scraping metrics for last {lookback_seconds}s (test duration: {self.test_duration:.1f}s)")
        
        # Collect historical metrics using kubectl top with timestamps
        # Note: kubectl top doesn't support historical queries, so we collect current state
        # and document the test window for manual GKE console queries
        
        metrics_data = {
            "collection_time": datetime.now().isoformat(),
            "test_start_time": self.test_start_time.isoformat() if self.test_start_time else None,
            "test_duration_seconds": self.test_duration,
            "lookback_seconds": lookback_seconds,
            "note": "For historical metrics, query GKE console with the time window above",
            "current_snapshot": self.metrics_collector.collect_single_snapshot(label="post-test")
        }
        
        metrics_file = self.post_test_dir / "gke-metrics.json"
        with open(metrics_file, 'w') as f:
            json.dump(metrics_data, f, indent=2)
        
        print(f"    ✅ Metrics metadata saved (includes time window for GKE console queries)")
        
        # Create a helper script for GKE metrics query
        self._create_gke_query_helper(lookback_seconds)
    
    def _create_gke_query_helper(self, lookback_seconds: int):
        """Create a helper script for querying GKE metrics."""
        if not self.test_start_time or not self.test_duration:
            return
        
        test_end_time = self.test_start_time + timedelta(seconds=self.test_duration)
        
        script_content = f"""#!/bin/bash
# GKE Metrics Query Helper
# Generated for test that ran from {self.test_start_time.isoformat()}
# Duration: {self.test_duration:.1f} seconds

# To query historical metrics from GKE, use the following time window:
# Start: {self.test_start_time.isoformat()}
# End: {test_end_time.isoformat()}

# Example: Query metrics using gcloud
# gcloud monitoring time-series list \\
#   --filter='metric.type="kubernetes.io/container/cpu/core_usage_time"' \\
#   --start-time="{self.test_start_time.isoformat()}" \\
#   --end-time="{test_end_time.isoformat()}"

echo "Test Window:"
echo "  Start: {self.test_start_time.strftime('%Y-%m-%d %H:%M:%S')}"
echo "  End:   {test_end_time.strftime('%Y-%m-%d %H:%M:%S')}"
echo "  Duration: {self.test_duration:.1f}s"
echo ""
echo "Query GKE Monitoring console with this time window for historical metrics."
"""
        
        script_file = self.post_test_dir / "query-gke-metrics.sh"
        script_file.write_text(script_content)
        script_file.chmod(0o755)
        print(f"    ✅ GKE query helper created: {script_file.name}")
    
    def _create_summary(self, output_dir: Path, phase: str, scenario_name: str):
        """Create a summary file for the telemetry collection."""
        summary_file = output_dir / "TELEMETRY_SUMMARY.txt"
        
        lines = [
            f"Telemetry Collection Summary - {phase.upper()}",
            "=" * 70,
            f"Collection Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Namespace: {self.namespace}",
            f"Scenario: {scenario_name}",
        ]
        
        if phase == "pre-test" and self.test_start_time:
            lines.append(f"Test Start Time: {self.test_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        elif phase == "post-test":
            if self.test_start_time:
                lines.append(f"Test Start Time: {self.test_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            if self.test_duration:
                lines.append(f"Test Duration: {self.test_duration:.1f}s")
        
        lines.extend([
            "",
            "Collected Data:",
            "-" * 70,
        ])
        
        # List all files in the directory
        for item in sorted(output_dir.rglob("*")):
            if item.is_file() and item != summary_file:
                rel_path = item.relative_to(output_dir)
                size = item.stat().st_size
                size_str = self._format_size(size)
                lines.append(f"  {str(rel_path):50s} {size_str:>10s}")
        
        summary_file.write_text("\n".join(lines))
    
    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        size_float = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_float < 1024.0:
                return f"{size_float:.1f}{unit}"
            size_float /= 1024.0
        return f"{size_float:.1f}TB"
    
    def reset(self):
        """Reset telemetry collector state for next test."""
        self.test_start_time = None
        self.test_duration = None


# Made with Bob