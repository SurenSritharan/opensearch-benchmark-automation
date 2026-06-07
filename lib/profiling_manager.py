import time
import concurrent.futures
from pathlib import Path
from typing import Optional
from lib.kubectl_helper import KubectlHelper

class ProfilingManager:
    """Coordinates parallel, low-overhead sampling profilers across distributed GKE nodes."""
    
    def __init__(self, config_manager):
        self.enabled = config_manager.profiling_enabled
        self.results_root = config_manager.results_root
        self.pod_label_selector = config_manager.pod_label_selector
        self.kubectl = KubectlHelper()

    def _get_cluster_nodes(self, namespace: str) -> list:
        """Discovers all operational OpenSearch data/compute nodes in the target namespace."""
        pods = self.kubectl.get_pods(namespace, label_selector=self.pod_label_selector)
        return [pod["metadata"]["name"] for pod in pods]

    def _exec_on_node(self, namespace: str, pod_name: str, cmd: list) -> str:
        """Executes a diagnostic command directly inside the 'opensearch' container."""
        return self.kubectl.exec_command(namespace, pod_name, "opensearch", cmd)

    def _check_profiler_installed(self, namespace: str, pod_name: str) -> bool:
        """
        Checks if async-profiler is installed on a node.
        Returns True if installed, False otherwise.
        """
        check_cmd = ["test", "-f", "/opt/async-profiler/bin/asprof"]
        result = self._exec_on_node(namespace, pod_name, check_cmd)
        
        # If test returns empty, file exists
        return not result or not result.strip()

    def _get_opensearch_pid(self, namespace: str, pod_name: str) -> str:
        """
        Identifies the target OpenSearch JVM PID without relying on pgrep/procps binaries.
        Falls back to '1' if discovery fails.
        """
        # Search /proc for the OpenSearch bootstrap execution string
        pid_cmd = [
            "bash", "-c", 
            "for pid in /proc/[0-9]*; do "
            "  if [ -f \"$pid/cmdline\" ] && grep -q \"org.opensearch.bootstrap.OpenSearch\" \"$pid/cmdline\" 2>/dev/null; then "
            "    basename \"$pid\"; "
            "    exit 0; "
            "  fi; "
            "done; "
            "echo '1'"
        ]
        pid = self._exec_on_node(namespace, pod_name, pid_cmd).strip()
        return pid if pid else "1"

    def _reset_stale_profiler(self, namespace: str, pod_name: str, pid: str) -> None:
        """Force-stops any dangling, legacy profiling sessions before initiating a fresh test."""
        stop_cmd = ["/opt/async-profiler/bin/asprof", "stop", pid]
        self._exec_on_node(namespace, pod_name, stop_cmd)

    def profile_steady_state(self, namespace: str, scenario_path: str, duration: int = 45, engine_results_dir: Optional[Path] = None) -> list[str]:
        """
        Spawns parallel profiling worker threads across the distributed cluster.
        Returns buffered output messages to display after benchmark completion.
        
        Args:
            namespace: Kubernetes namespace
            scenario_path: Scenario path (may include sweep subdirectory)
            duration: Profiling duration in seconds
            engine_results_dir: Optional engine-specific results directory. If provided,
                              overrides self.results_root for this profiling session.
        """
        if not self.enabled:
            print("ℹ️  Profiling is disabled via configuration flags. Skipping...")
            return []
            
        nodes = self._get_cluster_nodes(namespace)
        if not nodes:
            print(f"⚠️  No OpenSearch nodes found matching component labels in namespace {namespace}!")
            return []

        # Check if async-profiler is installed on all nodes (silently)
        missing_profiler = []
        for node_name in nodes:
            if not self._check_profiler_installed(namespace, node_name):
                missing_profiler.append(node_name)
        
        if missing_profiler:
            output_buffer = []
            output_buffer.append(f"\n⚠️  async-profiler not found on {len(missing_profiler)} node(s):")
            for node in missing_profiler:
                output_buffer.append(f"  - {node}")
            output_buffer.append("\n⏭️  Profiling was skipped for this run.\n")
            return output_buffer

        # Resolve PIDs and clear out lingering background sessions sequentially before launching thread pools
        node_targets = {}
        for idx, node_name in enumerate(nodes):
            target_pid = self._get_opensearch_pid(namespace, node_name)
            self._reset_stale_profiler(namespace, node_name, target_pid)
            node_targets[node_name] = (idx, target_pid)

        # Map out execution threads concurrently so nodes profile the exact same time window
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
            futures = {}
            for node_name, (idx, pid) in node_targets.items():
                # Isolate target HTML output path inside the node container
                safe_scenario_name = scenario_path.replace('/', '_')
                remote_path = f"/tmp/cpu_flame_{safe_scenario_name}-node-{idx}.html"
                
                # Low-overhead sampling command targeting verified PID
                cmd = ["timeout", str(duration + 10), "/opt/async-profiler/bin/asprof", "-d", str(duration), "-f", remote_path, pid]
                
                futures[executor.submit(self._exec_on_node, namespace, node_name, cmd)] = (node_name, idx, remote_path)

            # Wait for the duration of the profile snapshot window to elapse across all nodes
            concurrent.futures.wait(futures)
            
            # Buffer output from collection phase to display after benchmark completes
            output_buffer = []
            output_buffer.append("\n📊 Profiling Results:")
            output_buffer.append("  📥 Collecting profiling diagnostic HTML maps...")

            # Check profiling results and capture any errors
            profiling_errors = []
            for future, (node_name, idx, remote_path) in futures.items():
                try:
                    profiler_output = future.result()
                    
                    # Check if profiler reported any errors
                    if profiler_output and ("error" in profiler_output.lower() or "failed" in profiler_output.lower()):
                        profiling_errors.append(f"    ⚠️  Node {idx} ({node_name}): Profiler error - {profiler_output.strip()}")
                        continue
                    
                    # Verify the file exists before trying to read it
                    check_cmd = ["test", "-f", remote_path]
                    check_result = self._exec_on_node(namespace, node_name, check_cmd)
                    
                    # If test command returns non-empty output, file doesn't exist
                    if check_result and check_result.strip():
                        profiling_errors.append(f"    ⚠️  Node {idx} ({node_name}): Profile file not created at {remote_path}")
                        profiling_errors.append(f"        Profiler output: {profiler_output.strip() if profiler_output else '(no output)'}")
                        continue
                    
                    # Download and harvest flame graph artifacts to local directory tree
                    raw_html = self._exec_on_node(namespace, node_name, ["cat", remote_path])
                    
                    # Check if cat command failed
                    if raw_html.startswith("cat:") or "No such file" in raw_html:
                        profiling_errors.append(f"    ⚠️  Node {idx} ({node_name}): Failed to read profile - {raw_html.strip()}")
                        continue
                    
                    # Use engine-specific results directory if provided, otherwise use default
                    results_base = engine_results_dir if engine_results_dir else self.results_root
                    out_dir = results_base / scenario_path / "profiles"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    
                    local_file = out_dir / f"cpu_flame_graph_node-{idx}.html"
                    local_file.write_text(raw_html, encoding="utf-8")
                    output_buffer.append(f"    ➔ Saved local artifact: {local_file.relative_to(self.results_root.parent)}")
                    
                    # Housekeeping: Clean up the /tmp footprint inside the pod container
                    self._exec_on_node(namespace, node_name, ["rm", "-f", remote_path])
                    
                except Exception as e:
                    profiling_errors.append(f"    ⚠️  Node {idx} ({node_name}): Exception - {str(e)}")
            
            # Report any errors encountered
            if profiling_errors:
                output_buffer.append("\n  ⚠️  Profiling encountered errors:")
                output_buffer.extend(profiling_errors)
                output_buffer.append("\n  💡 Troubleshooting tips:")
                output_buffer.append("     1. Verify async-profiler is installed at /opt/async-profiler/bin/asprof in OpenSearch pods")
                output_buffer.append("     2. Verify process identifiers map cleanly via 'pgrep' tools")
                output_buffer.append("     3. Ensure sysctl adjustments (kernel.perf_event_paranoid=1) are properly loaded via initContainers")
                output_buffer.append("     4. Check target data node disk watermarks if allocations drop silently")
            else:
                output_buffer.append("✅ Profiling grid run finalized cleanly.")
                
            return output_buffer