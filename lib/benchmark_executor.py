import re
import json
import sys
import subprocess
from pathlib import Path
from typing import Tuple, List, Optional
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config


class BenchmarkExecutor:
    """Manages the lifecycle of OpenSearch Benchmark runs and diagnostic cluster utilities."""
    
    BENCHMARK_HOME = "/datasets/opensearch-benchmark"

    def __init__(self, engine: str, namespace: str, results_dir: Path, config):
        self.engine = engine
        self.namespace = namespace
        self.results_dir = results_dir
        self.pod_name = "opensearch-benchmark-client"
        self.config = config
        
        # Extract cluster settings from config
        self.cluster_endpoint = config.cluster_endpoint
        self.client_options = config.client_options
        
        # Load local kube config for API endpoints that don't need shell overhead
        k8s_config.load_kube_config()
        self.k8s_core = k8s_client.CoreV1Api()

    def _exec_subprocess(self, container: str, cmd: list) -> str:
        """Runs a direct, non-blocking kubectl exec command via native subprocesses."""
        full_cmd = [
            "kubectl", "exec", "-n", self.namespace, "-c", container,
            self.pod_name, "--"
        ] + cmd
        try:
            result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "TIMEOUT"
        except Exception as e:
            return f"ERROR: {str(e)}"

    def check_pod_status(self) -> str:
        """Returns the current execution lifecycle phase of the benchmark runner pod."""
        cmd = ["kubectl", "get", "pod", self.pod_name, "-n", self.namespace, "-o", "jsonpath={.status.phase}"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            return res.stdout.strip() if res.stdout else "NotFound"
        except Exception:
            return "NotFound"

    def clear_remote_logs(self):
        """Truncates the remote OSB logging payload to zero bytes before a new run starts."""
        self._exec_subprocess(
            container="benchmark", 
            cmd=["truncate", "-s", "0", f"{self.BENCHMARK_HOME}/.osb/logs/benchmark.log"]
        )

    def download_artifacts(self, console_output: str, scenario_name: str):
        """Extracts JSON summaries and background telemetry files safely via subprocess hooks."""
        target_dir = self.results_dir / scenario_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # Regex match UUIDs for the target test execution run
        uuid_pattern = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
        match = re.search(uuid_pattern, console_output)
        
        if match:
            run_id = match.group(0)
            remote_json_path = f"{self.BENCHMARK_HOME}/.osb/benchmarks/test-runs/{run_id}/test_run.json"
            stdout = self._exec_subprocess("benchmark", ["cat", remote_json_path])
            if stdout and not stdout.startswith("ERROR") and not stdout.startswith("cat:"):
                (target_dir / "test_run.json").write_text(stdout, encoding="utf-8")

        # Download the active system log footprint
        log_stdout = self._exec_subprocess("benchmark", ["cat", f"{self.BENCHMARK_HOME}/.osb/logs/benchmark.log"])
        if log_stdout and not log_stdout.startswith("ERROR") and not log_stdout.startswith("cat:"):
            (target_dir / "benchmark.log").write_text(log_stdout, encoding="utf-8")

    def check_index_exists(self, index_name: str) -> str:
        """Queries the internal OpenSearch service cluster port to assert index presence."""
        cmd = [
            "curl", "-sk", "-u", "admin:admin", "-o", "/dev/null",
            "-w", "%{http_code}", f"https://{self.cluster_endpoint}/{index_name}"
        ]
        return self._exec_subprocess(container="benchmark", cmd=cmd)

    def get_index_mapping(self, index_name: str) -> str:
        """Retrieves active schema engine mapping properties for deep metadata verification."""
        cmd = ["curl", "-sk", "-u", "admin:admin", f"https://{self.cluster_endpoint}/{index_name}/_mapping"]
        res = self._exec_subprocess(container="benchmark", cmd=cmd)
        return res if res and not res.startswith("ERROR") else "failed"
    
    def validate_vector_field_type(self, index_name: str, field_name: str = "vector") -> tuple[bool, str]:
        """Validates that the specified field is of type knn_vector."""
        import json
        
        # First check if index exists
        status_code = self.check_index_exists(index_name)
        if status_code != "200":
            return False, f"Index '{index_name}' does not exist (HTTP {status_code})"
        
        mapping_json = self.get_index_mapping(index_name)
        
        if mapping_json == "failed":
            return False, "Failed to retrieve index mapping"
        
        try:
            mapping = json.loads(mapping_json)
            # Navigate through the mapping structure
            properties = mapping.get(index_name, {}).get("mappings", {}).get("properties", {})
            field_info = properties.get(field_name, {})
            field_type = field_info.get("type", "")
            
            if not field_type:
                return False, f"Field '{field_name}' does not exist in index mapping"
            
            if field_type != "knn_vector":
                return False, f"Field '{field_name}' has type '{field_type}' but requires 'knn_vector'"
            
            return True, "Valid knn_vector field"
        except Exception as e:
            return False, f"Error parsing mapping: {str(e)}"


    def get_opensearch_pods(self) -> List[str]:
        """Discovers the cluster nodes dynamically using label structures and pattern match paths."""
        print(f"  🔍 Looking for OpenSearch data instances inside namespace: {self.namespace}")
        
        selectors = ["app=opensearch-cluster", "app=opensearch"]
        for selector in selectors:
            pods = self.k8s_core.list_namespaced_pod(self.namespace, label_selector=selector)
            if pods.items:
                found_names = [p.metadata.name for p in pods.items]
                print(f"  ✅ Found OpenSearch nodes via pattern match: {found_names}")
                return found_names

        # Fallback regex sweep across any non-benchmark processing pod structures
        try:
            all_pods = self.k8s_core.list_namespaced_pod(self.namespace)
            patterns = [r'opensearch-cluster-\d+', r'opensearch-data-\d+', r'^opensearch-[^b][a-z-]*-\d+']
            matched_names = []
            
            for pod in all_pods.items:
                name = pod.metadata.name
                if any(re.search(pat, name) for pat in patterns):
                    matched_names.append(name)
            
            if matched_names:
                print(f"  ✅ Found OpenSearch nodes via fallback regex patterns: {matched_names}")
                return matched_names
        except Exception:
            pass

        print(f"  ⚠️  No OpenSearch pods found. Check cluster status in namespace {self.namespace}!")
        return []

    def run_osb_command(self, scenario_name: str, workload_path: str, test_procedure: str, workload_params: Optional[str] = None, extra_args: Optional[list] = None) -> Tuple[bool, str]:
        """Runs the complete OpenSearch Benchmark workload wrapper process."""
        # Clean remote logs before starting a fresh run path
        self.clear_remote_logs()

        # Build command array
        cmd = [
            "kubectl", "exec", "-it", "-n", self.namespace, "-c", "benchmark",
            self.pod_name, "--", "opensearch-benchmark", "run",
            "--pipeline=benchmark-only",
        ]
        
        # Use --workload for official workloads (no path), --workload-path for custom
        if workload_path.startswith("/"):
            cmd.append(f"--workload-path={workload_path}")
        else:
            cmd.append(f"--workload={workload_path}")
        
        cmd.extend([
            f"--test-procedure={test_procedure}",
            f"--target-hosts={self.cluster_endpoint}",
            f"--client-options={self.client_options}",
            "--kill-running-processes"
        ])
        
        # Add workload-params if provided
        if workload_params:
            cmd.append(f"--workload-params={workload_params}")
        
        # Add any additional arguments
        if extra_args:
            cmd.extend(extra_args)
        
        # Log the OSB command being executed
        osb_cmd_start = cmd.index("opensearch-benchmark")
        osb_command = " ".join(cmd[osb_cmd_start:])
        print(f"\n  🔧 OSB Command:")
        print(f"     {osb_command}\n")
        
        try:
            # Stream output in real-time while capturing it
            # Use unbuffered mode to get output exactly as it appears
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0  # Unbuffered for real-time output
            )
            
            output_bytes = []
            
            # Read byte by byte to preserve all formatting including \r
            while True:
                byte = process.stdout.read(1)
                if not byte:
                    break
                output_bytes.append(byte)
                # Write directly to stdout to preserve formatting
                sys.stdout.buffer.write(byte)
                sys.stdout.buffer.flush()
            
            process.wait()
            result_stdout = b''.join(output_bytes).decode('utf-8', errors='replace')
            
            
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, cmd, result_stdout)
            
            # Post-run telemetry gathering pipeline
            self.download_artifacts(result_stdout, scenario_name)
            
            # Flush console logs locally
            output_file = self.results_dir / scenario_name / "console.log"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(result_stdout, encoding="utf-8")
            
            return True, result_stdout
            
        except subprocess.CalledProcessError as e:
            error_log = f"ERROR:\nReturn Code: {e.returncode}\n\nOUTPUT:\n{e.stdout if e.stdout else '(no output captured)'}"
            print(f"\n❌ BENCHMARK FAILED with return code {e.returncode}\n")
            err_file = self.results_dir / scenario_name / "crash_error.log"
            err_file.parent.mkdir(parents=True, exist_ok=True)
            err_file.write_text(error_log, encoding="utf-8")
            return False, error_log
        except Exception as e:
            error_log = f"UNEXPECTED ERROR:\n{str(e)}"
            print(f"\n❌ UNEXPECTED ERROR: {str(e)}\n")
            err_file = self.results_dir / scenario_name / "crash_error.log"
            err_file.parent.mkdir(parents=True, exist_ok=True)
            err_file.write_text(error_log, encoding="utf-8")

    def collect_telemetry(self):
        """Collects comprehensive cluster telemetry and saves to cluster-telemetry-state directory."""
        telemetry_dir = self.results_dir / "cluster-telemetry-state"
        telemetry_dir.mkdir(parents=True, exist_ok=True)
        
        print("📊 Collecting comprehensive cluster telemetry...")
        
        index_name = f"{self.engine}_index"
        
        # Helper function to execute curl and save output
        def save_curl_output(endpoint: str, filename: str, description: str = ""):
            cmd = ["curl", "-sk", "-u", "admin:admin", f"https://{self.cluster_endpoint}{endpoint}"]
            output = self._exec_subprocess("benchmark", cmd)
            if output and not output.startswith("ERROR") and not output.startswith("curl:"):
                (telemetry_dir / filename).write_text(output, encoding="utf-8")
                if description:
                    print(f"  ✅ Collected {description}")
        
        # Cluster health and status
        save_curl_output("/_cluster/health?pretty", "cluster-health.json", "cluster health")
        save_curl_output("/_cluster/stats?pretty", "cluster-stats.json", "cluster stats")
        save_curl_output("/_cluster/settings?include_defaults=true&flat_settings=true&pretty", 
                        "cluster-settings.json", "cluster settings")
        
        # Node information
        save_curl_output("/_cat/nodes?v&h=name,heap.percent,heap.current,heap.max,ram.percent,ram.current,ram.max,cpu,load_1m,load_5m,load_15m",
                        "cluster-nodes.txt", "node stats")
        save_curl_output("/_nodes/stats?pretty", "nodes-stats.json", "detailed node stats")
        
        # Index information
        save_curl_output(f"/{index_name}/_settings?pretty", "index-settings.json", "index settings")
        save_curl_output(f"/{index_name}/_mapping?pretty", "index-mapping.json", "index mapping")
        save_curl_output(f"/{index_name}/_stats?pretty", "index-stats.json", "index stats")
        save_curl_output(f"/_cat/indices/{index_name}?v&h=index,health,status,pri,rep,docs.count,store.size,pri.store.size",
                        "index-info.txt", "index info")
        
        # Thread pool statistics
        save_curl_output("/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,largest,completed,size",
                        "thread-pools.txt", "thread pool stats")
        
        # Task management
        save_curl_output("/_cat/tasks?v&detailed", "tasks.txt", "active tasks")
        
        # Segment information
        save_curl_output(f"/_cat/segments/{index_name}?v", "segments.txt", "segment info")
        
        print(f"  ✅ Telemetry data saved to: {telemetry_dir.relative_to(self.results_dir.parent.parent)}")