#!/usr/bin/env python3
"""
Kubernetes-native metrics collector using the Kubernetes Python client.
No kubectl dependency - works directly with Kubernetes API.
"""

import time
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class K8sMetricsCollector:
    """Collects GKE/Kubernetes metrics using native Python client."""
    
    def __init__(self, namespace: str, results_dir: Path, enabled: bool = True):
        """
        Initialize the Kubernetes metrics collector.
        
        Args:
            namespace: Kubernetes namespace to monitor
            results_dir: Directory to store metrics data
            enabled: Whether metrics collection is enabled
        """
        self.namespace = namespace
        self.results_dir = results_dir
        self.enabled = enabled
        self.in_cluster = False
        
        if not enabled:
            logger.info("Metrics collection is disabled")
            return
        
        # Initialize Kubernetes client
        try:
            # Try in-cluster config first (when running in a pod)
            config.load_incluster_config()
            self.in_cluster = True
            logger.info("✓ Using in-cluster Kubernetes configuration")
        except config.ConfigException:
            try:
                # Fall back to local kubeconfig
                config.load_kube_config()
                logger.info("✓ Using local kubeconfig")
            except Exception as e:
                logger.error(f"Failed to load Kubernetes config: {e}")
                self.enabled = False
                return
        
        # Initialize API clients
        self.core_api = client.CoreV1Api()
        self.custom_api = client.CustomObjectsApi()
        
        # Metrics data storage
        self.metrics_data = {
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
            "namespace": namespace,
            "samples": [],
            "summary": {}
        }
    
    def _get_node_metrics(self) -> Dict[str, Any]:
        """
        Collect metrics for all nodes in the cluster using Kubernetes API.
        
        Returns:
            Dictionary with node metrics including CPU, memory, and node pool
        """
        if not self.enabled:
            return {}
        
        node_metrics = {}
        
        try:
            # Get node metadata (for node pool labels)
            nodes = self.core_api.list_node()
            node_pool_map = {}
            
            for node in nodes.items:
                node_name = node.metadata.name
                labels = node.metadata.labels or {}
                node_pool = labels.get('cloud.google.com/gke-nodepool', 'unknown')
                node_pool_map[node_name] = node_pool
            
            # Get node metrics from metrics.k8s.io API
            metrics = self.custom_api.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes"
            )
            
            for item in metrics.get('items', []):
                node_name = item['metadata']['name']
                usage = item.get('usage', {})
                
                node_metrics[node_name] = {
                    'node_pool': node_pool_map.get(node_name, 'unknown'),
                    'cpu': usage.get('cpu', '0'),
                    'memory': usage.get('memory', '0'),
                    'timestamp': item.get('timestamp', datetime.utcnow().isoformat())
                }
            
            logger.debug(f"Collected metrics for {len(node_metrics)} nodes")
            
        except ApiException as e:
            logger.error(f"Error fetching node metrics: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in _get_node_metrics: {e}")
        
        return node_metrics
    
    def _get_pod_metrics(self) -> Dict[str, Any]:
        """
        Collect metrics for all pods in the namespace.
        
        Returns:
            Dictionary with pod metrics including CPU and memory per container
        """
        if not self.enabled:
            return {}
        
        pod_metrics = {}
        
        try:
            # Get pod metrics from metrics.k8s.io API
            metrics = self.custom_api.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=self.namespace,
                plural="pods"
            )
            
            for item in metrics.get('items', []):
                pod_name = item['metadata']['name']
                containers = item.get('containers', [])
                
                # Aggregate container metrics
                total_cpu = 0
                total_memory = 0
                container_details = []
                
                for container in containers:
                    usage = container.get('usage', {})
                    cpu_str = usage.get('cpu', '0')
                    mem_str = usage.get('memory', '0')
                    
                    container_details.append({
                        'name': container.get('name'),
                        'cpu': cpu_str,
                        'memory': mem_str
                    })
                
                pod_metrics[pod_name] = {
                    'containers': container_details,
                    'timestamp': item.get('timestamp', datetime.utcnow().isoformat())
                }
            
            logger.debug(f"Collected metrics for {len(pod_metrics)} pods in namespace {self.namespace}")
            
        except ApiException as e:
            logger.error(f"Error fetching pod metrics: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in _get_pod_metrics: {e}")
        
        return pod_metrics
    
    def _get_pod_status(self) -> Dict[str, Any]:
        """
        Get detailed pod status information.
        
        Returns:
            Dictionary with pod status, node assignment, and container states
        """
        if not self.enabled:
            return {}
        
        pod_status = {}
        
        try:
            pods = self.core_api.list_namespaced_pod(namespace=self.namespace)
            
            for pod in pods.items:
                pod_name = pod.metadata.name
                status = pod.status
                
                pod_status[pod_name] = {
                    'phase': status.phase,
                    'node': pod.spec.node_name,
                    'pod_ip': status.pod_ip,
                    'start_time': status.start_time.isoformat() if status.start_time else None,
                    'containers': []
                }
                
                # Get container statuses
                if status.container_statuses:
                    for container_status in status.container_statuses:
                        pod_status[pod_name]['containers'].append({
                            'name': container_status.name,
                            'ready': container_status.ready,
                            'restart_count': container_status.restart_count,
                            'image': container_status.image
                        })
            
            logger.debug(f"Collected status for {len(pod_status)} pods")
            
        except ApiException as e:
            logger.error(f"Error fetching pod status: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in _get_pod_status: {e}")
        
        return pod_status
    
    def collect_sample(self) -> Dict[str, Any]:
        """
        Collect a single metrics sample.
        
        Returns:
            Dictionary containing all metrics for this sample
        """
        if not self.enabled:
            return {}
        
        sample = {
            'timestamp': datetime.utcnow().isoformat(),
            'epoch': time.time(),
            'node_metrics': self._get_node_metrics(),
            'pod_metrics': self._get_pod_metrics(),
            'pod_status': self._get_pod_status()
        }
        
        return sample
    
    def collect_single_snapshot(self, label: str = "snapshot") -> Dict[str, Any]:
        """
        Collect a single snapshot of metrics.
        
        Args:
            label: Label for this snapshot
            
        Returns:
            Dictionary with snapshot data
        """
        if not self.enabled:
            logger.warning("Metrics collection is disabled")
            return {}
        
        logger.info(f"📸 Collecting metrics snapshot: {label}")
        
        snapshot = {
            'label': label,
            'timestamp': datetime.utcnow().isoformat(),
            'namespace': self.namespace,
            'in_cluster': self.in_cluster,
            'metrics': self.collect_sample()
        }
        
        return snapshot
    
    def start_collection(self, scenario_name: str, interval: int = 10, duration: Optional[int] = None):
        """
        Start continuous metrics collection.
        
        Args:
            scenario_name: Name of the benchmark scenario
            interval: Seconds between samples (default: 10)
            duration: Optional duration in seconds (None = run until stopped)
        """
        if not self.enabled:
            logger.info("Metrics collection is disabled")
            return
        
        logger.info(f"📊 Starting metrics collection for scenario: {scenario_name}")
        logger.info(f"   Namespace: {self.namespace}")
        logger.info(f"   Interval: {interval}s")
        if duration:
            logger.info(f"   Duration: {duration}s")
        
        self.metrics_data['start_time'] = datetime.utcnow().isoformat()
        self.metrics_data['scenario'] = scenario_name
        self.metrics_data['interval_seconds'] = interval
        
        start_time = time.time()
        sample_count = 0
        
        try:
            while True:
                # Collect sample
                sample = self.collect_sample()
                if sample:
                    self.metrics_data['samples'].append(sample)
                    sample_count += 1
                    logger.debug(f"Collected sample #{sample_count}")
                
                # Check if we should stop
                if duration and (time.time() - start_time) >= duration:
                    logger.info(f"Duration limit reached ({duration}s)")
                    break
                
                # Wait for next interval
                time.sleep(interval)
                
        except KeyboardInterrupt:
            logger.info("Metrics collection interrupted by user")
        
        self.metrics_data['end_time'] = datetime.utcnow().isoformat()
        self.metrics_data['duration_seconds'] = time.time() - start_time
        
        logger.info(f"✓ Collected {sample_count} samples over {self.metrics_data['duration_seconds']:.1f}s")
    
    def save_metrics(self, scenario_name: str):
        """
        Save collected metrics to JSON file.
        
        Args:
            scenario_name: Name of the scenario (used for directory structure)
        """
        if not self.enabled or not self.metrics_data['samples']:
            logger.warning("No metrics data to save")
            return
        
        # Create output directory
        output_dir = self.results_dir / scenario_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Save full metrics
        metrics_file = output_dir / "k8s_metrics.json"
        with open(metrics_file, 'w') as f:
            json.dump(self.metrics_data, f, indent=2)
        
        logger.info(f"✓ Metrics saved to: {metrics_file}")
        
        # Calculate and save summary
        summary = self._calculate_summary()
        summary_file = output_dir / "k8s_metrics_summary.json"
        with open(summary_file, 'w') as f:
            json.dump(summary, f, indent=2)
        
        logger.info(f"✓ Summary saved to: {summary_file}")
    
    def _calculate_summary(self) -> Dict[str, Any]:
        """Calculate summary statistics from collected samples."""
        if not self.metrics_data['samples']:
            return {}
        
        summary = {
            'total_samples': len(self.metrics_data['samples']),
            'nodes': {},
            'pods': {}
        }
        
        # Aggregate node metrics
        for sample in self.metrics_data['samples']:
            for node_name, metrics in sample.get('node_metrics', {}).items():
                if node_name not in summary['nodes']:
                    summary['nodes'][node_name] = {
                        'node_pool': metrics.get('node_pool'),
                        'samples': []
                    }
                summary['nodes'][node_name]['samples'].append(metrics)
        
        # Aggregate pod metrics
        for sample in self.metrics_data['samples']:
            for pod_name, metrics in sample.get('pod_metrics', {}).items():
                if pod_name not in summary['pods']:
                    summary['pods'][pod_name] = {'samples': []}
                summary['pods'][pod_name]['samples'].append(metrics)
        
        return summary
    
    def reset(self):
        """Reset metrics data for a new collection."""
        self.metrics_data = {
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
            "namespace": self.namespace,
            "samples": [],
            "summary": {}
        }
        logger.info("Metrics data reset")

# Made with Bob
