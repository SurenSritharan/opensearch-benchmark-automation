"""Cluster provisioning manager for OpenSearch clusters."""

import subprocess
import json
import time
from pathlib import Path
from typing import Optional, Tuple
from lib.kubectl_helper import KubectlHelper


class ClusterProvisioner:
    """Manages OpenSearch cluster provisioning and health checks."""
    
    def __init__(self, project_root: Optional[Path] = None):
        """
        Initialize the cluster provisioner.
        
        Args:
            project_root: Path to project root directory (auto-detected if not provided)
        """
        if project_root is None:
            # Auto-detect project root (parent of lib directory)
            self.project_root = Path(__file__).parent.parent
        else:
            self.project_root = Path(project_root)
        
        self.deploy_script = self.project_root / "gke-manifest" / "deploy-namespace-cluster.sh"
        self.destroy_script = self.project_root / "gke-manifest" / "destroy-namespace-cluster.sh"
    
    @staticmethod
    def check_namespace_exists(namespace: str) -> bool:
        """
        Check if a Kubernetes namespace exists.
        
        Args:
            namespace: Kubernetes namespace name
            
        Returns:
            True if namespace exists, False otherwise
        """
        cmd = ["kubectl", "get", "namespace", namespace]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError:
            return False
    
    @staticmethod
    def check_cluster_health(namespace: str, verbose: bool = False) -> Tuple[bool, int]:
        """
        Check if OpenSearch cluster is healthy and running in a namespace.
        Pods must be both Running AND Ready to be considered healthy.
        
        Args:
            namespace: Kubernetes namespace name
            verbose: If True, print detailed pod status information
            
        Returns:
            Tuple of (is_healthy, ready_pod_count)
        """
        if not ClusterProvisioner.check_namespace_exists(namespace):
            return False, 0
        
        # Get all pods in the namespace
        cmd = [
            "kubectl", "get", "pods",
            "-n", namespace,
            "-o", "json"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pods_data = json.loads(result.stdout)
            pods = pods_data.get("items", [])
            
            if not pods:
                return False, 0
            
            # Count pods that are both Running and Ready
            ready_pods = 0
            running_not_ready = 0
            other_status = 0
            
            for pod in pods:
                pod_name = pod.get("metadata", {}).get("name", "unknown")
                phase = pod.get("status", {}).get("phase", "")
                conditions = pod.get("status", {}).get("conditions", [])
                
                # Check if pod is Ready
                is_ready = False
                for condition in conditions:
                    if condition.get("type") == "Ready" and condition.get("status") == "True":
                        is_ready = True
                        break
                
                if phase == "Running" and is_ready:
                    ready_pods += 1
                    if verbose:
                        print(f"     ✅ {pod_name}: Running and Ready")
                elif phase == "Running" and not is_ready:
                    running_not_ready += 1
                    if verbose:
                        print(f"     ⏳ {pod_name}: Running but not Ready")
                else:
                    other_status += 1
                    if verbose:
                        print(f"     ⚠️  {pod_name}: {phase}")
            
            if verbose and (running_not_ready > 0 or other_status > 0):
                print(f"     Status: {ready_pods} ready, {running_not_ready} starting, {other_status} other")
            
            return ready_pods > 0, ready_pods
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return False, 0
    
    def provision_namespace(self, namespace: str) -> bool:
        """
        Provision a specific OpenSearch cluster namespace using deploy-namespace-cluster.sh.
        
        Args:
            namespace: Kubernetes namespace to provision (e.g., "os-jvector")
            
        Returns:
            True if provisioning succeeded, False otherwise
        """
        if not self.deploy_script.exists():
            print(f"\n❌ Deploy script not found: {self.deploy_script}")
            print("   Please check that the script exists in gke-manifest/\n")
            return False
        
        try:
            print(f"\n🚀 Provisioning cluster: {namespace}")
            print("   Using script: deploy-namespace-cluster.sh\n")
            
            # Run the deploy script with --force flag to skip confirmation
            result = subprocess.run(
                [str(self.deploy_script), namespace, "--force"],
                cwd=str(self.deploy_script.parent),
                check=True,
                text=True
            )
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"\n❌ Failed to provision cluster {namespace}")
            print("   Please check the error messages above.\n")
            return False
    
    def deprovision_namespace(self, namespace: str) -> bool:
        """
        Deprovision a specific OpenSearch cluster namespace using destroy-namespace-cluster.sh.
        
        Args:
            namespace: Kubernetes namespace to deprovision (e.g., "os-jvector")
            
        Returns:
            True if deprovisioning succeeded, False otherwise
        """
        # Check if namespace exists
        if not self.check_namespace_exists(namespace):
            print(f"\n  ℹ️  Namespace {namespace} does not exist, nothing to deprovision\n")
            return True
        
        if not self.destroy_script.exists():
            print(f"\n❌ Destroy script not found: {self.destroy_script}")
            print("   Please check that the script exists in gke-manifest/\n")
            return False
        
        try:
            print(f"\n🗑️  Deprovisioning cluster: {namespace}")
            print("   Using script: destroy-namespace-cluster.sh\n")
            
            # Run the destroy script with --force flag to skip confirmation
            result = subprocess.run(
                [str(self.destroy_script), namespace, "--force"],
                cwd=str(self.destroy_script.parent),
                check=True,
                text=True
            )
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"\n❌ Failed to deprovision cluster {namespace}")
            print("   Please check the error messages above.\n")
            return False
    
    def _try_scale_existing_resources(self, namespace: str) -> bool:
        """
        Attempt to scale up existing deployments/statefulsets if they exist but have 0 replicas.
        This handles cases where resources exist but were scaled down.
        
        Args:
            namespace: Kubernetes namespace to check
            
        Returns:
            True if resources were found and scaled, False otherwise
        """
        if not self.check_namespace_exists(namespace):
            return False
        
        print(f"  🔍 Checking for existing scaled-down resources in {namespace}...")
        
        deployments_info, statefulsets_info = KubectlHelper.get_replica_info(namespace)
        
        # Debug: Show what we found
        total_resources = len(deployments_info) + len(statefulsets_info)
        if total_resources == 0:
            print(f"     No deployments or statefulsets found in namespace")
            return False
        
        print(f"     Found {len(deployments_info)} deployment(s) and {len(statefulsets_info)} statefulset(s)")
        
        scaled_resources = []
        
        # Check deployments with 0 replicas
        for deployment in deployments_info:
            print(f"     Deployment '{deployment['name']}': {deployment['current_replicas']}/{deployment['desired_replicas']} replicas")
            if deployment["desired_replicas"] == 0:
                print(f"       → Scaling up to 1 replica...")
                if KubectlHelper.scale_deployment(namespace, deployment["name"], 1):
                    scaled_resources.append(f"deployment/{deployment['name']}")
        
        # Check statefulsets with 0 replicas
        for statefulset in statefulsets_info:
            print(f"     StatefulSet '{statefulset['name']}': {statefulset['current_replicas']}/{statefulset['desired_replicas']} replicas")
            if statefulset["desired_replicas"] == 0:
                # Determine appropriate replica count based on statefulset name
                replicas = 1
                if "data" in statefulset["name"].lower():
                    replicas = 3  # Data nodes typically run with 3 replicas
                
                print(f"       → Scaling up to {replicas} replica(s)...")
                if KubectlHelper.scale_statefulset(namespace, statefulset["name"], replicas):
                    scaled_resources.append(f"statefulset/{statefulset['name']}")
        
        if scaled_resources:
            print(f"  ✅ Scaled up resources: {', '.join(scaled_resources)}")
            return True
        
        print(f"     No scaled-down resources found (all have desired replicas > 0 or failed to scale)")
        return False
    
    def ensure_cluster_ready(self, namespace: str, auto_provision: bool = False) -> bool:
        """
        Ensure a specific cluster is provisioned and ready.
        First checks if resources exist but are scaled down, then prompts for provisioning if needed.
        
        Args:
            namespace: Kubernetes namespace to check (e.g., "os-jvector")
            auto_provision: If True, provision automatically without prompting
            
        Returns:
            True if cluster is ready, False otherwise
        """
        print(f"\n🔍 Checking cluster status: {namespace}")
        
        is_healthy, pod_count = self.check_cluster_health(namespace)
        
        if is_healthy:
            print(f"  ✅ {namespace}: Running ({pod_count} pods)\n")
            # Wait for benchmark client to be fully initialized
            if not self._wait_for_benchmark_client_ready(namespace):
                return False
            return True
        
        # Cluster not healthy - check if we can scale existing resources
        print(f"  ⚠️  {namespace}: No running pods detected")
        
        if self._try_scale_existing_resources(namespace):
            # Resources were scaled up, wait for them to start with progress updates
            print(f"  ⏳ Waiting for scaled resources to become ready (timeout: 6 minutes)...")
            
            max_wait_time = 360  # 6 minutes
            check_interval = 15  # Check every 15 seconds
            elapsed_time = 0
            
            while elapsed_time < max_wait_time:
                time.sleep(check_interval)
                elapsed_time += check_interval
                
                # Show detailed status every check
                is_healthy, pod_count = self.check_cluster_health(namespace, verbose=True)
                if is_healthy:
                    print(f"  ✅ {namespace}: Ready after scaling ({pod_count} pods) - took {elapsed_time}s\n")
                    # Wait for benchmark client to be fully initialized
                    if not self._wait_for_benchmark_client_ready(namespace):
                        return False
                    return True
                else:
                    print(f"     Waiting... ({elapsed_time}s / {max_wait_time}s)")
            
            # Timeout reached
            print(f"  ⚠️  Resources scaled but not ready after {max_wait_time}s.")
            print(f"     The pods may still be initializing. Check status with:")
            print(f"     kubectl get pods -n {namespace} -w\n")
            return False
        
        # No existing resources to scale - cluster needs provisioning
        print(f"  ❌ {namespace}: Not provisioned\n")
        
        if not auto_provision:
            response = input(f"Would you like to provision {namespace} now? (y/n): ").strip().lower()
            if response not in ["y", "yes"]:
                print(f"\n❌ Cluster provisioning skipped. Please provision manually:")
                print(f"   cd gke-manifest")
                print(f"   ./deploy-namespace-cluster.sh {namespace}\n")
                return False
        
        # Provision cluster
        if not self.provision_namespace(namespace):
            return False
        
        # Wait for pods to start
        print("Waiting for cluster to be ready...\n")
        time.sleep(15)
        
        # Verify cluster is now running
        is_healthy, pod_count = self.check_cluster_health(namespace)
        if is_healthy:
            print(f"  ✅ {namespace}: Ready ({pod_count} pods)\n")
            # Wait for benchmark client to be fully initialized
            if not self._wait_for_benchmark_client_ready(namespace):
                return False
            return True
        else:
            print(f"  ⚠️  {namespace}: Still starting up (may need more time)")
            print(f"   Monitor with: kubectl get pods -n {namespace} -w\n")
            return False
    
    def _ensure_benchmark_client_deployed(self, namespace: str) -> bool:
        """
        Ensure the benchmark client StatefulSet is deployed in the namespace.
        
        Args:
            namespace: Kubernetes namespace
            
        Returns:
            True if deployed or already exists, False on error
        """
        # Check if StatefulSet exists
        cmd = ["kubectl", "get", "statefulset", "opensearch-benchmark-client", "-n", namespace]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True  # Already exists
        except subprocess.CalledProcessError:
            # StatefulSet doesn't exist, deploy it
            print(f"  📦 Deploying benchmark client StatefulSet to {namespace}...")
            manifest_path = self.project_root / "gke-manifest" / "opensearch-benchmark-client.yaml"
            
            if not manifest_path.exists():
                print(f"  ❌ Benchmark client manifest not found: {manifest_path}")
                return False
            
            deploy_cmd = ["kubectl", "apply", "-f", str(manifest_path), "-n", namespace]
            try:
                subprocess.run(deploy_cmd, capture_output=True, text=True, check=True)
                print(f"  ✅ Benchmark client StatefulSet deployed")
                
                # Wait for pod to be created and start running
                print(f"  ⏳ Waiting for pod to be created...")
                max_wait = 60  # Wait up to 60 seconds for pod creation
                for i in range(max_wait):
                    time.sleep(1)
                    check_pod = ["kubectl", "get", "pod", "opensearch-benchmark-client-0", "-n", namespace, "-o", "jsonpath={.status.phase}"]
                    try:
                        result = subprocess.run(check_pod, capture_output=True, text=True, check=True)
                        phase = result.stdout.strip()
                        if phase in ["Running", "Pending"]:
                            print(f"  ✅ Pod created (status: {phase})")
                            return True
                    except subprocess.CalledProcessError:
                        # Pod doesn't exist yet, keep waiting
                        if i % 10 == 0 and i > 0:
                            print(f"     Still waiting for pod creation... ({i}s)")
                        continue
                
                print(f"  ⚠️  Pod not created after {max_wait}s")
                return False
                
            except subprocess.CalledProcessError as e:
                print(f"  ❌ Failed to deploy benchmark client: {e.stderr}")
                return False
    
    def _wait_for_benchmark_client_ready(self, namespace: str, timeout: int = 180) -> bool:
        """
        Wait for the benchmark client pod to be fully initialized.
        This ensures the git repository has been cloned successfully and the pod is ready for commands.
        
        Args:
            namespace: Kubernetes namespace
            timeout: Maximum time to wait in seconds
            
        Returns:
            True if benchmark client is ready, False otherwise
        """
        # First ensure the StatefulSet is deployed
        if not self._ensure_benchmark_client_deployed(namespace):
            return False
        
        pod_name = "opensearch-benchmark-client-0"
        workload_path = "/datasets/opensearch-benchmark-workloads"
        
        print(f"  🔄 Waiting for benchmark client to initialize...")
        
        start_time = time.time()
        last_status = ""
        
        while time.time() - start_time < timeout:
            # Check if the git repository has been cloned successfully
            # We verify:
            # 1. Directory exists
            # 2. It's a valid git repository (.git directory exists)
            # 3. It has at least one commit (not an empty/failed clone)
            cmd = [
                "kubectl", "exec", "-n", namespace, pod_name,
                "--", "sh", "-c",
                f"test -d {workload_path}/.git && cd {workload_path} && git rev-parse HEAD"
            ]
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and result.stdout.strip():
                    # Successfully got a git commit hash - repository is ready
                    commit_hash = result.stdout.strip()[:8]
                    print(f"  ✅ Benchmark client initialized (commit: {commit_hash})\n")
                    return True
                else:
                    # Command failed or no output - still initializing
                    current_status = "Cloning repository..."
                    if current_status != last_status:
                        print(f"     {current_status}")
                        last_status = current_status
            except subprocess.TimeoutExpired:
                current_status = "Pod not responding yet..."
                if current_status != last_status:
                    print(f"     {current_status}")
                    last_status = current_status
            except subprocess.CalledProcessError:
                # Pod exists but command failed - still initializing
                pass
            
            # Wait a bit before checking again
            time.sleep(5)
        
        print(f"  ⚠️  Benchmark client initialization timed out after {timeout}s")
        print(f"     The workload repository may still be cloning or failed to clone.")
        print(f"     Check pod logs: kubectl logs -n {namespace} {pod_name}\n")
        return False


# Made with Bob