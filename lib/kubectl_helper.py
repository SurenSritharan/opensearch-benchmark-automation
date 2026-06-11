"""Helper module for kubectl operations to improve code readability and maintainability."""

import subprocess
import json
from typing import List, Dict, Any, Optional, Tuple

class KubectlHelper:
    """Provides clean, reusable methods for common kubectl operations."""
    
    @staticmethod
    def get_pods(namespace: str, label_selector: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get list of pods in a namespace, optionally filtered by label selector.
        
        Args:
            namespace: Kubernetes namespace
            label_selector: Optional label selector (e.g., "app=opensearch")
            
        Returns:
            List of pod objects as dictionaries
        """
        cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
        if label_selector:
            cmd.extend(["-l", label_selector])
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pods_data = json.loads(result.stdout)
            return pods_data.get("items", [])
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to list pods: {e.stderr}")
            return []
        except json.JSONDecodeError as e:
            print(f"  ❌ Failed to parse kubectl output: {str(e)}")
            return []
    
    @staticmethod
    def exec_command(namespace: str, pod_name: str, container: str, command: List[str]) -> str:
        """
        Execute a command in a pod container.
        
        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
            container: Container name
            command: Command to execute as list of strings
            
        Returns:
            Command stdout as string
        """
        kubectl_cmd = [
            "kubectl", "exec",
            "-n", namespace,
            pod_name,
            "-c", container,
            "--"
        ] + command
        
        try:
            result = subprocess.run(kubectl_cmd, capture_output=True, text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            return f"Execution failed on pod {pod_name}: {error_msg}"
    
    @staticmethod
    def copy_to_pod(namespace: str, pod_name: str, container: str, 
                    local_path: str, remote_path: str) -> bool:
        """
        Copy a file from local machine to a pod.
        
        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
            container: Container name
            local_path: Local file path
            remote_path: Remote file path in pod
            
        Returns:
            True if successful, False otherwise
        """
        cmd = [
            "kubectl", "cp",
            local_path,
            f"{namespace}/{pod_name}:{remote_path}",
            "-c", container
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to copy file to pod: {e.stderr}")
            return False
    
    @staticmethod
    def get_pod_status(namespace: str, pod_name: str) -> str:
        """
        Get the status phase of a pod.
        
        Args:
            namespace: Kubernetes namespace
            pod_name: Name of the pod
            
        Returns:
            Pod status phase (e.g., "Running", "Pending", "Failed")
        """
        cmd = ["kubectl", "get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.status.phase}"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "Unknown"
    
    @staticmethod
    def get_deployments(namespace: str) -> List[Dict[str, Any]]:
        """
        Get list of deployments in a namespace.
        
        Args:
            namespace: Kubernetes namespace
            
        Returns:
            List of deployment objects as dictionaries
        """
        cmd = ["kubectl", "get", "deployments", "-n", namespace, "-o", "json"]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            deployments_data = json.loads(result.stdout)
            return deployments_data.get("items", [])
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to list deployments: {e.stderr}")
            return []
        except json.JSONDecodeError as e:
            print(f"  ❌ Failed to parse kubectl output: {str(e)}")
            return []
    
    @staticmethod
    def get_statefulsets(namespace: str) -> List[Dict[str, Any]]:
        """
        Get list of statefulsets in a namespace.
        
        Args:
            namespace: Kubernetes namespace
            
        Returns:
            List of statefulset objects as dictionaries
        """
        cmd = ["kubectl", "get", "statefulsets", "-n", namespace, "-o", "json"]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            statefulsets_data = json.loads(result.stdout)
            return statefulsets_data.get("items", [])
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to list statefulsets: {e.stderr}")
            return []
        except json.JSONDecodeError as e:
            print(f"  ❌ Failed to parse kubectl output: {str(e)}")
            return []
    
    @staticmethod
    def scale_deployment(namespace: str, deployment_name: str, replicas: int) -> bool:
        """
        Scale a deployment to the specified number of replicas.
        
        Args:
            namespace: Kubernetes namespace
            deployment_name: Name of the deployment
            replicas: Desired number of replicas
            
        Returns:
            True if successful, False otherwise
        """
        cmd = [
            "kubectl", "scale", "deployment", deployment_name,
            "-n", namespace,
            f"--replicas={replicas}"
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to scale deployment {deployment_name}: {e.stderr}")
            return False
    
    @staticmethod
    def scale_statefulset(namespace: str, statefulset_name: str, replicas: int) -> bool:
        """
        Scale a statefulset to the specified number of replicas.
        
        Args:
            namespace: Kubernetes namespace
            statefulset_name: Name of the statefulset
            replicas: Desired number of replicas
            
        Returns:
            True if successful, False otherwise
        """
        cmd = [
            "kubectl", "scale", "statefulset", statefulset_name,
            "-n", namespace,
            f"--replicas={replicas}"
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Failed to scale statefulset {statefulset_name}: {e.stderr}")
            return False
    
    @staticmethod
    def get_replica_info(namespace: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Get replica information for all deployments and statefulsets in a namespace.
        
        Args:
            namespace: Kubernetes namespace
            
        Returns:
            Tuple of (deployments_info, statefulsets_info) where each is a list of dicts
            containing name, current_replicas, and desired_replicas
        """
        deployments_info = []
        statefulsets_info = []
        
        # Get deployments
        deployments = KubectlHelper.get_deployments(namespace)
        for deployment in deployments:
            name = deployment.get("metadata", {}).get("name", "unknown")
            spec = deployment.get("spec", {})
            status = deployment.get("status", {})
            
            deployments_info.append({
                "name": name,
                "desired_replicas": spec.get("replicas", 0),
                "current_replicas": status.get("replicas", 0),
                "ready_replicas": status.get("readyReplicas", 0)
            })
        
        # Get statefulsets
        statefulsets = KubectlHelper.get_statefulsets(namespace)
        for statefulset in statefulsets:
            name = statefulset.get("metadata", {}).get("name", "unknown")
            spec = statefulset.get("spec", {})
            status = statefulset.get("status", {})
            
            statefulsets_info.append({
                "name": name,
                "desired_replicas": spec.get("replicas", 0),
                "current_replicas": status.get("replicas", 0),
                "ready_replicas": status.get("readyReplicas", 0)
            })
        
        return deployments_info, statefulsets_info

# Made with Bob
