#!/usr/bin/env python3
"""
Kubernetes-native metrics collector using direct HTTPS API URL requests.
No official 'kubernetes' package dependency - loads service account tokens dynamically.
"""

import time
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any
import requests

logger = logging.getLogger(__name__)


class K8sMetricsCollector:
    """Collects GKE/Kubernetes metrics using raw HTTP requests to the cluster control plane."""
    
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
        
        if not enabled:
            logger.info("Metrics collection is disabled")
            return

        # Paths injected natively by Kubernetes into Pods
        self.token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        self.ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        
        # Base internal API URL for control plane
        self.base_url = "https://kubernetes.default.svc"
        
        # Simple environment validation
        if self.token_path.exists() and self.ca_path.exists():
            logger.info("✓ Running inside cluster. Using ServiceAccount token.")
        else:
            logger.warning("⚠ ServiceAccount files not found at default paths. If running locally, ensure proxy or token mounts exist.")
            # Fallback for local testing if needed (requires a running 'kubectl proxy' on default port)
            self.base_url = "http://127.0.0.1:8001"
            self.ca_path = None
        
        # Metrics data storage
        self.metrics_data = {
            "start_time": None,
            "end_time": None,
            "duration_seconds": 0,
            "namespace": namespace,
            "samples": [],
            "summary": {}
        }

    def _get_api_headers(self) -> Dict[str, str]:
        """Dynamically read token to handle standard Kubernetes token rotation natively."""
        headers = {"Accept": "application/json"}
        if self.token_path.exists():
            try:
                token = self.token_path.read_text().strip()
                headers["Authorization"] = f"Bearer {token}"
            except Exception as e:
                logger.error(f"Failed to read service account token: {e}")
        return headers

    def _make_request(self, endpoint: str) -> Optional[Dict[str, Any]]:
        """Helper to safely make secure requests to the K8s control plane."""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_api_headers()
        verify_ssl = str(self.ca_path) if self.ca_path and self.ca_path.exists() else False

        try:
            response = requests.get(url, headers=headers, verify=verify_ssl, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"K8s API HTTP request failed for {endpoint}: {e}")
            return None

    def _get_node_metrics(self) -> Dict[str, Any]:
        """Collect metrics for all nodes in the cluster using metrics.k8s.io API URL."""
        if not self.enabled:
            return {}
        
        node_metrics = {}
        
        # 1. Get Core Node metadata (for GKE node pools)
        nodes_data = self._make_request("/api/v1/nodes")
        node_pool_map = {}
        if nodes_data:
            for node in nodes_data.get('items', []):
                node_name = node['metadata']['name']
                labels = node['metadata'].get('labels', {})
                node_pool = labels.get('cloud.google.com/gke-nodepool', 'unknown')
                node_pool_map[node_name] = node_pool

        # 2. Get Node usage metrics
        metrics_data = self._make_request("/apis/metrics.k8s.io/v1beta1/nodes")
        if metrics_data:
            for item in metrics_data.get('items', []):
                node_name = item['metadata']['name']
                usage = item.get('usage', {})
                
                node_metrics[node_name] = {
                    'node_pool': node_pool_map.get(node_name, 'unknown'),
                    'cpu': usage.get('cpu', '0'),
                    'memory': usage.get('memory', '0'),
                    'timestamp': item.get('timestamp', datetime.utcnow().isoformat() + "Z")
                }
            logger.debug(f"Collected metrics for {len(node_metrics)} nodes")
            
        return node_metrics
    
    def _get_pod_metrics(self) -> Dict[str, Any]:
        """Collect metrics for all pods in the specific namespace."""
        if not self.enabled:
            return {}
        
        pod_metrics = {}
        endpoint = f"/apis/metrics.k8s.io/v1beta1/namespaces/{self.namespace}/pods"
        metrics_data = self._make_request(endpoint)
        
        if metrics_data:
            for item in metrics_data.get('items', []):
                pod_name = item['metadata']['name']
                containers = item.get('containers', [])
                
                container_details = []
                for container in containers:
                    usage = container.get('usage', {})
                    container_details.append({
                        'name': container.get('name'),
                        'cpu': usage.get('cpu', '0'),
                        'memory': usage.get('memory', '0')
                    })
                
                pod_metrics[pod_name] = {
                    'containers': container_details,
                    'timestamp': item.get('timestamp', datetime.utcnow().isoformat() + "Z")
                }
            logger.debug(f"Collected metrics for {len(pod_metrics)} pods in namespace {self.namespace}")
            
        return pod_metrics
    
    def _get_pod_status(self) -> Dict[str, Any]:
        """Get detailed pod status information via core API."""
        if not self.enabled:
            return {}
        
        pod_status = {}
        endpoint = f"/api/v1/namespaces/{self.namespace}/pods"
        pods_data = self._make_request(endpoint)
        
        if pods_data:
            for pod in pods_data.get('items', []):
                pod_name = pod['metadata']['name']
                status = pod.get('status', {})
                spec = pod.get('spec', {})
                
                pod_status[pod_name] = {
                    'phase': status.get('phase'),
                    'node': spec.get('nodeName'),
                    'pod_ip': status.get('podIP'),
                    'start_time': status.get('startTime'),
                    'containers': []
                }
                
                # Container statuses parsing
                for container_status in status.get('containerStatuses', []):
                    pod_status[pod_name]['containers'].append({
                        'name': container_status.get('name'),
                        'ready': container_status.get('ready'),
                        'restart_count': container_status.get('restartCount', 0),
                        'image': container_status.get('image')
                    })
            logger.debug(f"Collected status for {len(pod_status)} pods")
            
        return pod_status
    
    def collect_sample(self) -> Dict[str, Any]:
        """Collect a single metrics sample."""
        if not self.enabled:
            return {}
        
        return {
            'timestamp': datetime.utcnow().isoformat() + "Z",
            'epoch': time.time(),
            'node_metrics': self._get_node_metrics(),
            'pod_metrics': self._get_pod_metrics(),
            'pod_status': self._get_pod_status()
        }
    
    def collect_single_snapshot(self, label: str = "snapshot") -> Dict[str, Any]:
        """Collect a single snapshot of metrics."""
        if not self.enabled:
            logger.warning("Metrics collection is disabled")
            return {}
        
        logger.info(f"📸 Collecting metrics snapshot: {label}")
        
        return {
            'label': label,
            'timestamp': datetime.utcnow().isoformat() + "Z",
            'namespace': self.namespace,
            'metrics': self.collect_sample()
        }
    
    def start_collection(self, scenario_name: str, interval: int = 10, duration: Optional[int] = None):
        """Start continuous metrics collection."""
        if not self.enabled:
            logger.info("Metrics collection is disabled")
            return
        
        logger.info(f"📊 Starting metrics collection for scenario: {scenario_name}")
        logger.info(f"   Namespace: {self.namespace}")
        logger.info(f"   Interval: {interval}s")
        if duration:
            logger.info(f"   Duration: {duration}s")
        
        self.metrics_data['start_time'] = datetime.utcnow().isoformat() + "Z"
        self.metrics_data['scenario'] = scenario_name
        self.metrics_data['interval_seconds'] = interval
        
        start_time = time.time()
        sample_count = 0
        
        try:
            while True:
                sample = self.collect_sample()
                if sample:
                    self.metrics_data['samples'].append(sample)
                    sample_count += 1
                    logger.debug(f"Collected sample #{sample_count}")
                
                if duration and (time.time() - start_time) >= duration:
                    logger.info(f"Duration limit reached ({duration}s)")
                    break
                
                time.sleep(interval)
                
        except KeyboardInterrupt:
            logger.info("Metrics collection interrupted by user")
        
        self.metrics_data['end_time'] = datetime.utcnow().isoformat() + "Z"
        self.metrics_data['duration_seconds'] = time.time() - start_time
        
        logger.info(f"✓ Collected {sample_count} samples over {self.metrics_data['duration_seconds']:.1f}s")
    
    def save_metrics(self, scenario_name: str):
        """Save collected metrics to JSON file."""
        if not self.enabled or not self.metrics_data['samples']:
            logger.warning("No metrics data to save")
            return
        
        output_dir = self.results_dir / scenario_name
        output_dir.mkdir(parents=True, exist_ok=True)
        
        metrics_file = output_dir / "k8s_metrics.json"
        with open(metrics_file, 'w') as f:
            json.dump(self.metrics_data, f, indent=2)
        
        logger.info(f"✓ Metrics saved to: {metrics_file}")
        
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
        
        for sample in self.metrics_data['samples']:
            for node_name, metrics in sample.get('node_metrics', {}).items():
                if node_name not in summary['nodes']:
                    summary['nodes'][node_name] = {
                        'node_pool': metrics.get('node_pool'),
                        'samples': []
                    }
                summary['nodes'][node_name]['samples'].append(metrics)
        
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