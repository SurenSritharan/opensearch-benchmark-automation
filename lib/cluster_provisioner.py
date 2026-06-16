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
        Check if a namespace is fully ready by verifying all StatefulSets have their pods Running and Ready.
        
        A namespace is healthy when:
        - All StatefulSets have desired_replicas == ready_replicas
        - All pods from those StatefulSets are Running and Ready (1/1)
        
        Args:
            namespace: Kubernetes namespace name
            verbose: If True, print detailed readiness information
            
        Returns:
            Tuple of (is_healthy, ready_pod_count)
        """
        if not ClusterProvisioner.check_namespace_exists(namespace):
            return False, 0
        
        try:
            # Get StatefulSet information
            statefulsets_info = KubectlHelper.get_replica_info(namespace)[1]
            if not statefulsets_info:
                if verbose:
                    print("     No StatefulSets found")
                return False, 0
            
            # Get all pods in namespace
            cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pods_data = json.loads(result.stdout)
            pods = pods_data.get("items", [])
            
            if not pods:
                if verbose:
                    print("     No pods found")
                return False, 0
            
            # Check StatefulSet readiness
            all_statefulsets_ready = True
            ready_statefulsets = 0
            total_expected_pods = 0
            
            for statefulset in statefulsets_info:
                name = statefulset["name"]
                desired = statefulset["desired_replicas"]
                ready = statefulset["ready_replicas"]
                current = statefulset["current_replicas"]
                
                total_expected_pods += desired
                
                if desired > 0 and desired == ready:
                    ready_statefulsets += 1
                    if verbose:
                        print(f"     ✅ {name}: {ready}/{desired} replicas ready")
                else:
                    all_statefulsets_ready = False
                    if verbose:
                        print(f"     ⏳ {name}: {ready}/{desired} replicas ready ({current} current)")
            
            # Check individual pod status
            ready_pods = 0
            running_not_ready = 0
            other_status = 0
            
            for pod in pods:
                pod_name = pod.get("metadata", {}).get("name", "")
                phase = pod.get("status", {}).get("phase", "")
                conditions = pod.get("status", {}).get("conditions", [])
                
                # Check if pod is managed by a StatefulSet (has ordinal suffix)
                is_statefulset_pod = any(pod_name.startswith(ss["name"] + "-") for ss in statefulsets_info)
                
                if not is_statefulset_pod:
                    continue  # Skip non-StatefulSet pods
                
                is_ready = any(
                    condition.get("type") == "Ready" and condition.get("status") == "True"
                    for condition in conditions
                )
                
                if phase == "Running" and is_ready:
                    ready_pods += 1
                    if verbose:
                        print(f"     ✅ {pod_name}: Running and Ready (1/1)")
                elif phase == "Running" and not is_ready:
                    running_not_ready += 1
                    if verbose:
                        print(f"     ⏳ {pod_name}: Running but not Ready (0/1)")
                else:
                    other_status += 1
                    if verbose:
                        print(f"     ⚠️  {pod_name}: {phase}")
            
            if verbose:
                print(f"     StatefulSet readiness: {ready_statefulsets}/{len(statefulsets_info)} ready")
                print(f"     Pod readiness: {ready_pods}/{total_expected_pods} ready")
            
            # Cluster is healthy if all StatefulSets are ready AND all expected pods are ready
            is_healthy = all_statefulsets_ready and ready_pods == total_expected_pods
            
            return is_healthy, ready_pods
            
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            if verbose:
                print(f"     ❌ Error checking cluster health: {e}")
            return False, 0
        except Exception:
            return False, 0
    
    def provision_namespace(self, namespace: str) -> bool:
        """
        Provision a specific OpenSearch cluster namespace.
        Uses deploy-metrics-store.sh for os-metrics, deploy-namespace-cluster.sh for others.
        
        Args:
            namespace: Kubernetes namespace to provision (e.g., "os-jvector", "os-metrics")
            
        Returns:
            True if provisioning succeeded, False otherwise
        """
        # Special handling for metrics store namespace
        if namespace == "os-metrics":
            metrics_deploy_script = self.project_root / "gke-manifest" / "deploy-metrics-store.sh"
            if not metrics_deploy_script.exists():
                print(f"\n❌ Metrics store deploy script not found: {metrics_deploy_script}")
                print("   Please check that the script exists in gke-manifest/\n")
                return False
            
            try:
                print(f"\n🚀 Provisioning metrics store: {namespace}")
                print("   Using script: deploy-metrics-store.sh\n")
                
                # Run the metrics store deploy script
                result = subprocess.run(
                    [str(metrics_deploy_script)],
                    cwd=str(metrics_deploy_script.parent),
                    check=True,
                    text=True
                )
                
                return True
                
            except subprocess.CalledProcessError as e:
                print(f"\n❌ Failed to provision metrics store {namespace}")
                print("   Please check the error messages above.\n")
                return False
        
        # Standard benchmark cluster provisioning
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
        Deprovision a specific OpenSearch cluster namespace.
        For metrics store (os-metrics), scales down to 0 replicas to preserve data.
        For benchmark clusters, scales StatefulSets down to 0 replicas to preserve PVC-backed data.
        
        Args:
            namespace: Kubernetes namespace to deprovision (e.g., "os-jvector", "os-metrics")
            
        Returns:
            True if deprovisioning succeeded, False otherwise
        """
        # Check if namespace exists
        if not self.check_namespace_exists(namespace):
            print(f"\n  ℹ️  Namespace {namespace} does not exist, nothing to deprovision\n")
            return True
        
        # Special handling for metrics store - scale down instead of destroy
        if namespace == "os-metrics":
            print(f"\n💾 Scaling down metrics store: {namespace}")
            print("   (Preserving PersistentVolumeClaims to retain metrics data)")
            
            try:
                # Scale down metrics store StatefulSet
                scale_cmd = ["kubectl", "scale", "statefulset", "opensearch-metrics-store",
                           "-n", namespace, "--replicas=0"]
                subprocess.run(scale_cmd, check=True, capture_output=True, text=True)
                print(f"  ✅ Metrics store scaled down to 0 replicas")
                
                # Scale down dashboards if they exist
                try:
                    scale_dash_cmd = ["kubectl", "scale", "deployment", "opensearch-dashboards",
                                    "-n", namespace, "--replicas=0"]
                    subprocess.run(scale_dash_cmd, check=True, capture_output=True, text=True)
                    print(f"  ✅ Dashboards scaled down to 0 replicas")
                except subprocess.CalledProcessError:
                    # Dashboards might not exist, that's okay
                    pass
                
                print(f"\n  💡 Metrics data preserved in PersistentVolumeClaims")
                print(f"     To restore: kubectl scale statefulset opensearch-metrics-store -n {namespace} --replicas=1")
                print()
                return True
                
            except subprocess.CalledProcessError as e:
                print(f"\n❌ Failed to scale down metrics store {namespace}")
                print(f"   Error: {e.stderr if e.stderr else 'Unknown error'}\n")
                return False
        
        # Standard benchmark cluster deprovisioning (preserve PVCs)
        print(f"\n💾 Deprovisioning cluster: {namespace}")
        print("   Scaling StatefulSets to 0 replicas to preserve PVC-backed data")
        
        try:
            scaled_resources = []
            
            statefulsets_info = KubectlHelper.get_statefulsets(namespace)
            if not statefulsets_info:
                print(f"  ℹ️  No StatefulSets found in {namespace}")
                return True
            
            for statefulset in statefulsets_info:
                statefulset_name = statefulset.get("metadata", {}).get("name", "unknown")
                if KubectlHelper.scale_statefulset(namespace, statefulset_name, 0):
                    scaled_resources.append(f"statefulset/{statefulset_name}")
            
            if scaled_resources:
                print(f"  ✅ Scaled down resources: {', '.join(scaled_resources)}")
            print(f"  💾 PVCs preserved in namespace {namespace}")
            print(f"     To inspect: kubectl get pvc -n {namespace}")
            print()
            return True
            
        except Exception as e:
            print(f"\n❌ Failed to deprovision cluster {namespace}")
            print(f"   Error: {str(e)}\n")
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
        
        # Check if required StatefulSets exist for benchmark clusters (not metrics store)
        if namespace != "os-metrics":
            required_statefulsets = ["opensearch-data", "opensearch-cluster-manager", "opensearch-benchmark-client"]
            existing_statefulset_names = [ss["name"] for ss in statefulsets_info]
            
            missing_statefulsets = [name for name in required_statefulsets if name not in existing_statefulset_names]
            
            if missing_statefulsets:
                print(f"     ⚠️  Missing required StatefulSets: {', '.join(missing_statefulsets)}")
                print(f"     Full provisioning required")
                return False
        
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
            print(f"  ✅ {namespace}: Running ({pod_count} StatefulSets ready)\n")
            # Wait for benchmark client to be fully initialized
            if not self._wait_for_benchmark_client_ready(namespace):
                return False
            return True
        
        # Cluster not healthy - check if we can scale existing resources
        print(f"  ⚠️  {namespace}: Not fully ready")
        
        if self._try_scale_existing_resources(namespace):
            # Resources were scaled up, wait with intelligent health monitoring
            print(f"  ⏳ Waiting for scaled resources to become ready...")
            
            return self._wait_for_cluster_ready_with_health_checks(namespace)
        
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
        
        # Wait for cluster to be ready with intelligent health monitoring
        print("Waiting for cluster to be ready...\n")
        
        return self._wait_for_cluster_ready_with_health_checks(namespace)
    
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
        # Skip benchmark client check for metrics store namespace
        if namespace == "os-metrics":
            return True
        
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
    
    def _wait_for_cluster_ready_with_health_checks(self, namespace: str) -> bool:
        """
        Wait for cluster to be ready with intelligent health monitoring.
        Performs OpenSearch cluster health checks in addition to pod readiness.
        
        Args:
            namespace: Kubernetes namespace
            
        Returns:
            True if cluster is healthy, False otherwise
        """
        base_timeout = 900  # 15 minutes maximum
        check_interval = 15
        elapsed_time = 0
        last_pod_count = 0
        no_progress_time = 0
        max_no_progress = 180  # 3 minutes without progress
        
        # Track different stages of initialization
        stages = {
            "pods_scheduled": False,
            "pods_running": False,
            "opensearch_responding": False,
            "cluster_formed": False
        }
        
        while elapsed_time < base_timeout:
            time.sleep(check_interval)
            elapsed_time += check_interval
            
            # Stage 1: Check pod scheduling and readiness
            is_healthy, pod_count = self.check_cluster_health(namespace, verbose=True)
            
            # Track progress
            if pod_count > last_pod_count:
                print(f"     ✅ Progress: {pod_count} pods ready (was {last_pod_count})")
                last_pod_count = pod_count
                no_progress_time = 0
                stages["pods_running"] = True
            else:
                no_progress_time += check_interval
            
            # Stage 2: If pods are running, check OpenSearch cluster health
            if pod_count > 0 and not stages["opensearch_responding"]:
                opensearch_healthy = self._check_opensearch_cluster_health(namespace)
                if opensearch_healthy:
                    print(f"     ✅ OpenSearch cluster is responding")
                    stages["opensearch_responding"] = True
                    stages["cluster_formed"] = True
                    no_progress_time = 0  # Reset on OpenSearch progress
            
            # Success condition: All pods ready AND OpenSearch healthy
            if is_healthy:
                if stages["opensearch_responding"]:
                    print(f"  ✅ {namespace}: Fully ready ({pod_count} pods, OpenSearch healthy) - took {elapsed_time}s\n")
                else:
                    print(f"  ✅ {namespace}: Pods ready ({pod_count} pods) - took {elapsed_time}s")
                    print(f"     Verifying OpenSearch cluster health...")
                    # Give OpenSearch a moment to fully initialize
                    time.sleep(10)
                    if self._check_opensearch_cluster_health(namespace):
                        print(f"  ✅ OpenSearch cluster healthy\n")
                    else:
                        print(f"  ⚠️  Pods ready but OpenSearch cluster not responding yet\n")
                
                # Wait for benchmark client
                if not self._wait_for_benchmark_client_ready(namespace):
                    return False
                return True
            
            # Early failure detection
            if no_progress_time >= max_no_progress:
                print(f"     ⚠️  No progress for {no_progress_time}s")
                print(f"     Current stage: {self._get_current_stage(stages)}")
                print(f"     Check: kubectl get pods -n {namespace}")
                print(f"     Events: kubectl get events -n {namespace} --sort-by='.lastTimestamp' | tail -20")
                return False
            
            # Status update
            remaining = base_timeout - elapsed_time
            stage_info = self._get_current_stage(stages)
            print(f"     Waiting... ({elapsed_time}s elapsed, {remaining}s remaining, {pod_count} pods ready, {stage_info})")
        
        # Timeout
        print(f"  ⚠️  Cluster not ready after {base_timeout}s")
        print(f"     Final stage: {self._get_current_stage(stages)}")
        return False
    
    def _check_opensearch_cluster_health(self, namespace: str) -> bool:
        """
        Check OpenSearch cluster health via API.
        
        Args:
            namespace: Kubernetes namespace
            
        Returns:
            True if OpenSearch cluster is responding and healthy
        """
        try:
            # Try to get cluster health from any data node
            cmd = [
                "kubectl", "exec", "-n", namespace,
                "opensearch-data-0", "--",
                "curl", "-sk", "-u", "admin:admin",
                "--cert", "/usr/share/opensearch/config/certs/admin.pem",
                "--key", "/usr/share/opensearch/config/certs/admin-key.pem",
                "https://localhost:9200/_cluster/health"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout:
                # Parse JSON response
                health = json.loads(result.stdout)
                status = health.get("status", "unknown")
                num_nodes = health.get("number_of_nodes", 0)
                
                # Cluster is healthy if status is green/yellow and has nodes
                return status in ["green", "yellow"] and num_nodes > 0
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            pass
        
        return False
    
    def _get_current_stage(self, stages: dict) -> str:
        """Get human-readable current initialization stage."""
        if stages["cluster_formed"]:
            return "cluster formed"
        elif stages["opensearch_responding"]:
            return "OpenSearch responding"
        elif stages["pods_running"]:
            return "pods running"
        elif stages["pods_scheduled"]:
            return "pods scheduled"
        else:
            return "waiting for pods"


# Made with Bob