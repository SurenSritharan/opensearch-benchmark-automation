"""Cluster provisioning manager for OpenSearch clusters."""

import subprocess
import json
import time
from pathlib import Path
from typing import Optional, Tuple


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
    def check_cluster_health(namespace: str) -> Tuple[bool, int]:
        """
        Check if OpenSearch cluster is healthy and running in a namespace.
        
        Args:
            namespace: Kubernetes namespace name
            
        Returns:
            Tuple of (is_healthy, running_pod_count)
        """
        if not ClusterProvisioner.check_namespace_exists(namespace):
            return False, 0
        
        # Check for running pods
        cmd = [
            "kubectl", "get", "pods",
            "-n", namespace,
            "--field-selector=status.phase=Running",
            "-o", "json"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pods_data = json.loads(result.stdout)
            running_pods = len(pods_data.get("items", []))
            return running_pods > 0, running_pods
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
    
    def ensure_cluster_ready(self, namespace: str, auto_provision: bool = False) -> bool:
        """
        Ensure a specific cluster is provisioned and ready.
        Prompts for provisioning if cluster is missing (unless auto_provision is True).
        
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
            return True
        
        # Cluster needs provisioning
        print(f"  ❌ {namespace}: Not provisioned\n")
        
        if not auto_provision:
            response = input(f"Would you like to provision {namespace} now? (yes/no): ").strip().lower()
            if response != "yes":
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
            return True
        else:
            print(f"  ⚠️  {namespace}: Still starting up (may need more time)")
            print(f"   Monitor with: kubectl get pods -n {namespace} -w\n")
            return False


# Made with Bob