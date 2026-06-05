import time
import concurrent.futures
from pathlib import Path
from kubernetes import client, config
from kubernetes.stream import stream

class ProfilingManager:
    """Coordinates parallel, low-overhead sampling profilers across distributed GKE nodes."""
    
    def __init__(self, config_manager):
        self.enabled = config_manager.profiling_enabled
        self.results_root = config_manager.results_root
        self.pod_label_selector = config_manager.pod_label_selector
        
        # Initialize native Kubernetes API Client
        config.load_kube_config()
        self.k8s_core = client.CoreV1Api()

    def _get_cluster_nodes(self, namespace: str) -> list:
        """Discovers all operational OpenSearch data/compute nodes in the target namespace."""
        pods = self.k8s_core.list_namespaced_pod(
            namespace,
            label_selector=self.pod_label_selector
        )
        return [pod.metadata.name for pod in pods.items]

    def _exec_on_node(self, namespace: str, pod_name: str, cmd: list) -> str:
        """Executes a diagnostic command directly inside the 'opensearch' container."""
        try:
            return stream(
                self.k8s_core.connect_get_namespaced_pod_exec,
                pod_name, 
                namespace, 
                container="opensearch",
                command=cmd, 
                stderr=True, 
                stdout=True, 
                stdin=False, 
                tty=False
            )
        except Exception as e:
            return f"Execution failed on node {pod_name}: {str(e)}"

    def profile_steady_state(self, namespace: str, scenario_path: str, duration: int = 45) -> list[str]:
        """
        Spawns parallel profiling worker threads across the distributed cluster.
        Returns buffered output messages to display after benchmark completion.
        """
        if not self.enabled:
            print("ℹ️  Profiling is disabled via configuration flags. Skipping...")
            return []
            
        nodes = self._get_cluster_nodes(namespace)
        if not nodes:
            print(f"⚠️  No OpenSearch nodes found matching component labels in namespace {namespace}!")
            return []

        print(f"🔥 Spawning async-profiler grid across {len(nodes)} nodes ({duration}s snapshot window)...")

        # Map out execution threads concurrently so nodes profile the exact same time window
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
            futures = {}
            for i, node_name in enumerate(nodes):
                # Isolate target HTML output path inside the node container
                remote_path = f"/tmp/cpu_flame_{scenario_path.replace('/', '_')}-node-{i}.html"
                
                # Low-overhead sampling command targeting PID 1
                cmd = ["bash", "-c", f"/opt/async-profiler/bin/asprof -d {duration} -f {remote_path} 1"]
                
                futures[executor.submit(self._exec_on_node, namespace, node_name, cmd)] = (node_name, i, remote_path)

            # Wait for the duration of the profile snapshot window to elapse across all nodes
            concurrent.futures.wait(futures)
            
            # Buffer output from collection phase to display after benchmark completes
            output_buffer = []
            output_buffer.append("  📥 Collecting profiling diagnostic HTML maps...")

            # Download and harvest flame graph artifacts to local directory tree
            for future, (node_name, idx, remote_path) in futures.items():
                raw_html = self._exec_on_node(namespace, node_name, ["cat", remote_path])
                
                # Standardize output paths to match results structure requirements
                out_dir = self.results_root / f"{namespace.replace('os-', '')}-metrics" / scenario_path / "profiles"
                out_dir.mkdir(parents=True, exist_ok=True)
                
                local_file = out_dir / f"cpu_flame_graph_node-{idx}.html"
                local_file.write_text(raw_html, encoding="utf-8")
                output_buffer.append(f"    ➔ Saved local artifact: {local_file.relative_to(self.results_root.parent)}")
                
                # Housekeeping: Clean up the /tmp footprint inside the pod container
                self._exec_on_node(namespace, node_name, ["rm", remote_path])
                
            output_buffer.append("✅ Profiling grid run finalized cleanly.")
            return output_buffer