"""Helper module for kubectl operations to improve code readability and maintainability."""

import subprocess
import json
from typing import List, Dict, Any, Optional


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

# Made with Bob
