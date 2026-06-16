"""
OpenSearch Metrics Store Client

This module provides a client for storing benchmark results in a dedicated
OpenSearch metrics store instance. Results are persisted across multiple runs
and can be queried for historical analysis and comparison.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from lib.kubectl_helper import KubectlHelper


class MetricsStoreClient:
    """Client for interacting with the OpenSearch metrics store."""
    
    def __init__(self, 
                 metrics_store_endpoint: str = "opensearch-metrics-store.os-metrics.svc.cluster.local:9200",
                 username: str = "admin",
                 password: str = "admin",
                 enabled: bool = True):
        """
        Initialize the metrics store client.
        
        Args:
            metrics_store_endpoint: The endpoint of the metrics store
            username: Authentication username
            password: Authentication password
            enabled: Whether metrics store is enabled
        """
        self.endpoint = metrics_store_endpoint
        self.username = username
        self.password = password
        self.enabled = enabled
        self.kubectl = KubectlHelper()
        
        # Index patterns for different metric types
        self.benchmark_results_index = "benchmark-results"
        self.telemetry_index = "benchmark-telemetry"
        self.metrics_index = "benchmark-metrics"
        
    def _exec_curl(self, method: str, path: str, data: Optional[Dict] = None,
                   namespace: str = "os-jvector", pod_name: str = "opensearch-benchmark-client-0") -> tuple[bool, str]:
        """
        Execute a curl command against the metrics store from within a pod.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., /_cluster/health)
            data: Optional JSON data to send
            namespace: Kubernetes namespace containing the pod
            pod_name: Name of the pod to execute from
            
        Returns:
            Tuple of (success, response_text)
        """
        if not self.enabled:
            return False, "Metrics store is disabled"
        
        url = f"https://{self.endpoint}{path}"
        
        # Use certificate-based authentication (admin certs are mounted in benchmark pods)
        cmd = [
            "curl", "-sk", "-X", method,
            "--cert", "/certs/admin.pem",
            "--key", "/certs/admin-key.pem",
            "-H", "Content-Type: application/json"
        ]
        
        if data:
            cmd.extend(["-d", json.dumps(data)])
        
        cmd.append(url)
        
        try:
            output = self.kubectl.exec_command(namespace, pod_name, "benchmark", cmd)
            if output and not output.startswith("ERROR") and not output.startswith("curl:"):
                return True, output
            return False, output
        except Exception as e:
            return False, str(e)
    
    def check_health(self, namespace: str = "os-jvector", 
                    pod_name: str = "opensearch-benchmark-client-0") -> bool:
        """
        Check if the metrics store is healthy and accessible.
        
        Returns:
            True if healthy, False otherwise
        """
        success, response = self._exec_curl("GET", "/_cluster/health", 
                                           namespace=namespace, pod_name=pod_name)
        if success:
            try:
                health = json.loads(response)
                return health.get("status") in ["green", "yellow"]
            except json.JSONDecodeError:
                return False
        return False
    
    def create_index_template(self, namespace: str = "os-jvector",
                             pod_name: str = "opensearch-benchmark-client-0") -> bool:
        """
        Create index templates for benchmark results storage.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        # Template for benchmark results
        benchmark_template = {
            "index_patterns": [f"{self.benchmark_results_index}-*"],
            "template": {
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "refresh_interval": "30s"
                },
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "run_id": {"type": "keyword"},
                        "dataset": {"type": "keyword"},
                        "engine": {"type": "keyword"},
                        "scenario": {"type": "keyword"},
                        "test_procedure": {"type": "keyword"},
                        "workload_params": {"type": "object", "enabled": False},
                        "results": {"type": "object", "enabled": False},
                        "metrics": {
                            "properties": {
                                "throughput": {"type": "float"},
                                "latency_p50": {"type": "float"},
                                "latency_p90": {"type": "float"},
                                "latency_p99": {"type": "float"},
                                "service_time": {"type": "float"},
                                "error_rate": {"type": "float"}
                            }
                        }
                    }
                }
            }
        }
        
        # Template for telemetry data
        telemetry_template = {
            "index_patterns": [f"{self.telemetry_index}-*"],
            "template": {
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0
                },
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "run_id": {"type": "keyword"},
                        "dataset": {"type": "keyword"},
                        "engine": {"type": "keyword"},
                        "telemetry_type": {"type": "keyword"},
                        "data": {"type": "object", "enabled": False}
                    }
                }
            }
        }
        
        # Template for resource metrics
        metrics_template = {
            "index_patterns": [f"{self.metrics_index}-*"],
            "template": {
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0
                },
                "mappings": {
                    "properties": {
                        "timestamp": {"type": "date"},
                        "run_id": {"type": "keyword"},
                        "dataset": {"type": "keyword"},
                        "engine": {"type": "keyword"},
                        "scenario": {"type": "keyword"},
                        "node_name": {"type": "keyword"},
                        "pod_name": {"type": "keyword"},
                        "cpu_percent": {"type": "float"},
                        "memory_percent": {"type": "float"},
                        "memory_bytes": {"type": "long"}
                    }
                }
            }
        }
        
        templates = [
            ("benchmark_results_template", benchmark_template),
            ("telemetry_template", telemetry_template),
            ("metrics_template", metrics_template)
        ]
        
        all_success = True
        for template_name, template_body in templates:
            success, response = self._exec_curl(
                "PUT", 
                f"/_index_template/{template_name}",
                data=template_body,
                namespace=namespace,
                pod_name=pod_name
            )
            if not success:
                print(f"⚠️  Failed to create template {template_name}: {response}")
                all_success = False
            else:
                print(f"✅ Created index template: {template_name}")
        
        return all_success
    
    def store_benchmark_result(self, 
                               run_id: str,
                               dataset: str,
                               engine: str,
                               scenario: str,
                               test_procedure: str,
                               workload_params: Dict[str, Any],
                               results: Dict[str, Any],
                               namespace: str = "os-jvector",
                               pod_name: str = "opensearch-benchmark-client-0") -> bool:
        """
        Store benchmark results in the metrics store.
        
        Args:
            run_id: Unique identifier for this benchmark run
            dataset: Dataset name
            engine: Engine name (jvector, faiss, lucene)
            scenario: Scenario name
            test_procedure: Test procedure name
            workload_params: Workload parameters used
            results: Benchmark results data
            namespace: Kubernetes namespace
            pod_name: Pod name to execute from
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        # Extract key metrics from results
        metrics = {}
        if "op_metrics" in results:
            for op in results["op_metrics"]:
                if op.get("task") == "search":
                    metrics = {
                        "throughput": op.get("throughput", {}).get("mean", 0),
                        "latency_p50": op.get("latency", {}).get("50.0", 0),
                        "latency_p90": op.get("latency", {}).get("90.0", 0),
                        "latency_p99": op.get("latency", {}).get("99.0", 0),
                        "service_time": op.get("service_time", {}).get("mean", 0),
                        "error_rate": op.get("error_rate", 0)
                    }
                    break
        
        document = {
            "timestamp": datetime.utcnow().isoformat(),
            "run_id": run_id,
            "dataset": dataset,
            "engine": engine,
            "scenario": scenario,
            "test_procedure": test_procedure,
            "workload_params": workload_params,
            "results": results,
            "metrics": metrics
        }
        
        # Use date-based index for better management
        index_name = f"{self.benchmark_results_index}-{datetime.utcnow().strftime('%Y.%m')}"
        
        success, response = self._exec_curl(
            "POST",
            f"/{index_name}/_doc",
            data=document,
            namespace=namespace,
            pod_name=pod_name
        )
        
        if success:
            print(f"✅ Stored benchmark result in metrics store: {index_name}")
            return True
        else:
            print(f"⚠️  Failed to store benchmark result: {response}")
            return False
    
    def store_telemetry(self,
                       run_id: str,
                       dataset: str,
                       engine: str,
                       telemetry_type: str,
                       data: Dict[str, Any],
                       namespace: str = "os-jvector",
                       pod_name: str = "opensearch-benchmark-client-0") -> bool:
        """
        Store telemetry data in the metrics store.
        
        Args:
            run_id: Unique identifier for this benchmark run
            dataset: Dataset name
            engine: Engine name
            telemetry_type: Type of telemetry (cluster-health, index-stats, etc.)
            data: Telemetry data
            namespace: Kubernetes namespace
            pod_name: Pod name to execute from
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        document = {
            "timestamp": datetime.utcnow().isoformat(),
            "run_id": run_id,
            "dataset": dataset,
            "engine": engine,
            "telemetry_type": telemetry_type,
            "data": data
        }
        
        index_name = f"{self.telemetry_index}-{datetime.utcnow().strftime('%Y.%m')}"
        
        success, response = self._exec_curl(
            "POST",
            f"/{index_name}/_doc",
            data=document,
            namespace=namespace,
            pod_name=pod_name
        )
        
        return success
    
    def store_resource_metrics(self,
                              run_id: str,
                              dataset: str,
                              engine: str,
                              scenario: str,
                              metrics_data: List[Dict[str, Any]],
                              namespace: str = "os-jvector",
                              pod_name: str = "opensearch-benchmark-client-0") -> bool:
        """
        Store resource metrics in the metrics store.
        
        Args:
            run_id: Unique identifier for this benchmark run
            dataset: Dataset name
            engine: Engine name
            scenario: Scenario name
            metrics_data: List of metric samples
            namespace: Kubernetes namespace
            pod_name: Pod name to execute from
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not metrics_data:
            return False
        
        index_name = f"{self.metrics_index}-{datetime.utcnow().strftime('%Y.%m')}"
        
        # Bulk insert for efficiency
        bulk_data = []
        for sample in metrics_data:
            # Add metadata for each document
            bulk_data.append(json.dumps({"index": {"_index": index_name}}))
            
            doc = {
                "timestamp": sample.get("timestamp"),
                "run_id": run_id,
                "dataset": dataset,
                "engine": engine,
                "scenario": scenario
            }
            
            # Add node metrics
            if "nodes" in sample:
                for node_name, node_data in sample["nodes"].items():
                    node_doc = doc.copy()
                    node_doc.update({
                        "node_name": node_name,
                        "cpu_percent": node_data.get("cpu_percent"),
                        "memory_percent": node_data.get("memory_percent"),
                        "memory_bytes": node_data.get("memory_bytes")
                    })
                    bulk_data.append(json.dumps(node_doc))
            
            # Add pod metrics
            if "pods" in sample:
                for pod_name_key, pod_data in sample["pods"].items():
                    pod_doc = doc.copy()
                    pod_doc.update({
                        "pod_name": pod_name_key,
                        "cpu_percent": pod_data.get("cpu_percent"),
                        "memory_percent": pod_data.get("memory_percent"),
                        "memory_bytes": pod_data.get("memory_bytes")
                    })
                    bulk_data.append(json.dumps(pod_doc))
        
        if not bulk_data:
            return False
        
        # Join with newlines and add final newline
        bulk_body = "\n".join(bulk_data) + "\n"
        
        # Use kubectl exec to send bulk request with certificate authentication
        cmd = [
            "curl", "-sk", "-X", "POST",
            "--cert", "/certs/admin.pem",
            "--key", "/certs/admin-key.pem",
            "-H", "Content-Type: application/x-ndjson",
            "--data-binary", f"@-",
            f"https://{self.endpoint}/_bulk"
        ]
        
        try:
            # Use subprocess to pipe data
            process = subprocess.Popen(
                ["kubectl", "exec", "-n", namespace, pod_name, "-c", "benchmark", "--", "sh", "-c",
                 f"echo '{bulk_body}' | {' '.join(cmd)}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                print(f"✅ Stored {len(metrics_data)} metric samples in metrics store")
                return True
            else:
                print(f"⚠️  Failed to store metrics: {stderr}")
                return False
        except Exception as e:
            print(f"⚠️  Error storing metrics: {e}")
            return False
    
    def query_results(self,
                     dataset: Optional[str] = None,
                     engine: Optional[str] = None,
                     scenario: Optional[str] = None,
                     limit: int = 100,
                     namespace: str = "os-jvector",
                     pod_name: str = "opensearch-benchmark-client-0") -> List[Dict[str, Any]]:
        """
        Query benchmark results from the metrics store.
        
        Args:
            dataset: Filter by dataset name
            engine: Filter by engine name
            scenario: Filter by scenario name
            limit: Maximum number of results to return
            namespace: Kubernetes namespace
            pod_name: Pod name to execute from
            
        Returns:
            List of matching results
        """
        if not self.enabled:
            return []
        
        # Build query
        must_clauses = []
        if dataset:
            must_clauses.append({"term": {"dataset": dataset}})
        if engine:
            must_clauses.append({"term": {"engine": engine}})
        if scenario:
            must_clauses.append({"term": {"scenario": scenario}})
        
        query = {
            "size": limit,
            "sort": [{"timestamp": "desc"}],
            "query": {
                "bool": {
                    "must": must_clauses
                }
            } if must_clauses else {"match_all": {}}
        }
        
        success, response = self._exec_curl(
            "POST",
            f"/{self.benchmark_results_index}-*/_search",
            data=query,
            namespace=namespace,
            pod_name=pod_name
        )
        
        if success:
            try:
                results = json.loads(response)
                return [hit["_source"] for hit in results.get("hits", {}).get("hits", [])]
            except json.JSONDecodeError:
                return []
        return []

# Made with Bob