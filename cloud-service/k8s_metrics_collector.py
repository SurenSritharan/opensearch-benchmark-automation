#!/usr/bin/env python3
"""
Kubernetes-native metrics collector using direct HTTPS API URL requests.
No official 'kubernetes' package dependency - loads service account tokens dynamically.
Outputs the exact target payload schema required.
"""

import time
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Any, List
from collections import defaultdict
import requests

logger = logging.getLogger(__name__)


class K8sMetricsCollector:
    """Collects GKE/Kubernetes metrics using raw HTTP requests to the cluster control plane."""
    
    def __init__(self, namespace: str, results_dir: Path, enabled: bool = True):
        self.namespace = namespace
        self.results_dir = results_dir
        self.enabled = enabled
        
        if not enabled:
            logger.info("Metrics collection is disabled")
            return

        self.token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
        self.ca_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
        self.base_url = "https://kubernetes.default.svc"
        
        if self.token_path.exists() and self.ca_path.exists():
            logger.info("✓ Running inside cluster. Using ServiceAccount token.")
        else:
            logger.warning("⚠ ServiceAccount files not found at default paths.")
            self.base_url = "http://127.0.0.1:8001"
            self.ca_path = None
        
        # Internal state tracking
        self.scenario_name = ""
        self.start_iso = ""
        self.end_iso = ""
        self.total_samples = 0
        self.final_payload = None
        self._stop_event = threading.Event()
        
        # Aggregation buckets for the specified summary structure
        self.node_cpu_raw = defaultdict(list)
        self.node_mem_raw = defaultdict(list)
        self.node_pools = {}
        self.pod_metrics_history = defaultdict(lambda: {"cpu_samples": [], "memory_samples": []})

    def _get_api_headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token_path.exists():
            try:
                headers["Authorization"] = f"Bearer {self.token_path.read_text().strip()}"
            except Exception as e:
                logger.error(f"Failed to read service account token: {e}")
        return headers

    def _make_request(self, endpoint: str) -> Optional[Dict[str, Any]]:
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

    def _parse_cpu(self, cpu_str: str) -> float:
        """Converts K8s CPU strings (e.g., '188m', '2') to float cores."""
        if not cpu_str:
            return 0.0
        if cpu_str.endswith('m'):
            return float(cpu_str[:-1]) / 1000.0
        if cpu_str.endswith('n'):
            return float(cpu_str[:-1]) / 1000000000.0
        return float(cpu_str)

    def _parse_memory_to_mi(self, mem_str: str) -> float:
        """Converts K8s Memory strings (e.g., '2449Mi', '16Gi') to float MiB."""
        if not mem_str:
            return 0.0
        if mem_str.endswith('Ki'):
            return float(mem_str[:-2]) / 1024.0
        if mem_str.endswith('Mi'):
            return float(mem_str[:-2])
        if mem_str.endswith('Gi'):
            return float(mem_str[:-2]) * 1024.0
        return float(mem_str)

    def _cache_node_pools(self):
        """Fetches nodes once at initialization to map node-pools."""
        nodes_data = self._make_request("/api/v1/nodes")
        if nodes_data:
            for node in nodes_data.get('items', []):
                node_name = node['metadata']['name']
                labels = node['metadata'].get('labels', {})
                node_pool = labels.get('cloud.google.com/gke-nodepool', 'unknown')
                self.node_pools[node_name] = node_pool

    def collect_sample(self):
        """Polls endpoints and stores sample values in correct formats."""
        if not self.enabled:
            return
        
        self.total_samples += 1

        # 1. Fetch and store Node Metric points
        metrics_data = self._make_request("/apis/metrics.k8s.io/v1beta1/nodes")
        if metrics_data:
            for item in metrics_data.get('items', []):
                node_name = item['metadata']['name']
                usage = item.get('usage', {})
                self.node_cpu_raw[node_name].append(self._parse_cpu(usage.get('cpu', '0')))
                self.node_mem_raw[node_name].append(self._parse_memory_to_mi(usage.get('memory', '0')))

        # 2. Fetch and store Pod Metric points
        pod_data = self._make_request(f"/apis/metrics.k8s.io/v1beta1/namespaces/{self.namespace}/pods")
        if pod_data:
            for item in pod_data.get('items', []):
                pod_name = item['metadata']['name']
                
                total_pod_cpu_m = 0
                total_pod_mem_ki = 0
                
                for container in item.get('containers', []):
                    cpu_str = container.get('usage', {}).get('cpu', '0')
                    mem_str = container.get('usage', {}).get('memory', '0')
                    
                    # Normalize CPU string chunks to millicores integer
                    if cpu_str.endswith('m'):
                        total_pod_cpu_m += int(cpu_str[:-1])
                    elif cpu_str.endswith('n'):
                        total_pod_cpu_m += int(cpu_str[:-1]) // 1000000
                    else:
                        total_pod_cpu_m += int(float(cpu_str) * 1000)
                        
                    # Normalize Memory string chunks to KiB integer
                    if mem_str.endswith('Ki'):
                        total_pod_mem_ki += int(mem_str[:-2])
                    elif mem_str.endswith('Mi'):
                        total_pod_mem_ki += int(mem_str[:-2]) * 1024
                    elif mem_str.endswith('Gi'):
                        total_pod_mem_ki += int(mem_str[:-2]) * 1024 * 1024
                
                # Append formatted string samples matching specifications
                self.pod_metrics_history[pod_name]["cpu_samples"].append(f"{total_pod_cpu_m}m")
                self.pod_metrics_history[pod_name]["memory_samples"].append(f"{int(total_pod_mem_ki / 1024)}Mi")

    def start_collection(self, scenario_name: str, interval: int = 10, duration: Optional[int] = None):
        if not self.enabled:
            return
        
        self.scenario_name = scenario_name
        self.final_payload = None
        self._stop_event.clear()
        self._cache_node_pools()
        
        # Python 3.11+ preferred over utcnow()
        self.start_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "")
        start_time = time.time()
        end_epoch = start_time + duration if duration else float('inf')
        
        logger.info(f"Starting metrics capture loop for scenario: {scenario_name}")
        while not self._stop_event.is_set() and time.time() < end_epoch:
            loop_start = time.time()
            self.collect_sample()
            
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, interval - elapsed)
            if duration and (time.time() + sleep_time) >= end_epoch:
                break
            self._stop_event.wait(sleep_time)
        
        self.end_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "")
        duration_seconds = time.time() - start_time
        
        # Build payload structure matching target output format
        self.final_payload = {
            "scenario": self.scenario_name,
            "namespace": self.namespace,
            "start_time": self.start_iso,
            "end_time": self.end_iso,
            "duration_seconds": duration_seconds,
            "summary": self._calculate_summary()
        }

    def _calculate_summary(self) -> Dict[str, Any]:
        summary = {
            "nodes": {},
            "pods": dict(self.pod_metrics_history),
            "total_samples": self.total_samples
        }
        
        all_nodes = set(self.node_cpu_raw.keys()).union(self.node_pools.keys())
        for node in all_nodes:
            cpu_list = self.node_cpu_raw.get(node, [0.0])
            mem_list = self.node_mem_raw.get(node, [0.0])
            
            # Safe fallbacks if nodes disappear mid-run
            cpu_avg = sum(cpu_list) / len(cpu_list) if cpu_list else 0.0
            cpu_max = max(cpu_list) if cpu_list else 0.0
            cpu_min = min(cpu_list) if cpu_list else 0.0
            
            mem_avg = sum(mem_list) / len(mem_list) if mem_list else 0.0
            mem_max = max(mem_list) if mem_list else 0.0
            mem_min = min(mem_list) if mem_list else 0.0
            
            summary["nodes"][node] = {
                "node_pool": self.node_pools.get(node, "unknown"),
                "cpu_avg": cpu_avg,
                "cpu_max": cpu_max,
                "cpu_min": cpu_min,
                "memory_avg": mem_avg,
                "memory_max": mem_max,
                "memory_min": mem_min
            }
            
        return summary

    def stop_collection(self):
        if not self.enabled:
            return
        self._stop_event.set()

    def save_metrics(self, scenario_name: str):
        if not self.enabled or not self.total_samples or not self.final_payload:
            return

        self.final_payload["scenario"] = scenario_name
        self.results_dir.mkdir(parents=True, exist_ok=True)
        
        with open(self.results_dir / "k8s_metrics.json", 'w') as f:
            json.dump(self.final_payload, f, indent=2)
        logger.info(f"✓ Target JSON format payload saved to: {self.results_dir}")

    def reset(self):
        self.total_samples = 0
        self.final_payload = None
        self._stop_event.clear()
        self.node_cpu_raw.clear()
        self.node_mem_raw.clear()
        self.node_pools.clear()
        self.pod_metrics_history.clear()