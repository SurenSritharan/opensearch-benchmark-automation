import json
import yaml
import sys
from pathlib import Path
from kubernetes import client, config
from kubernetes.stream import stream
from kubernetes.client.exceptions import ApiException

class DatasetManager:
    """Manages multi-layered engine/dataset parameters and pushes Jinja2 templates."""
    def __init__(self, dataset_name: str, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.dataset_name = dataset_name
        
        # Parse global dataset metadata
        datasets_manifest = self._load_yaml(self.config_dir / "datasets.yaml")
        if dataset_name not in datasets_manifest["datasets"]:
            raise ValueError(f"Dataset '{dataset_name}' not defined in datasets.yaml")
        
        self.dataset_data = datasets_manifest["datasets"][dataset_name]
        self.workload_name = self.dataset_data.get("workload_name", "vectorsearch")
        self.is_official = self.dataset_data.get("is_official", False)
        
        # For official workloads, use the pre-installed workload path; for custom, use custom path
        if self.is_official:
            self.workload_path = f"/root/opensearch-benchmark-workloads/{self.workload_name}"
        else:
            self.workload_path = f"/root/custom-workloads/{self.workload_name}"
        
        # Load custom test procedures if defined, with defaults
        custom_procedures = self.dataset_data.get("test_procedures", {})
        self.test_procedures = {
            "index": custom_procedures.get("index", "no-train-test-index-only"),
            "bulk": custom_procedures.get("bulk", "no-train-test"),
            "search": custom_procedures.get("search", "search-only")
        }
        
        config.load_kube_config()
        self.k8s_core = client.CoreV1Api()

    def _load_yaml(self, path: Path) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def generate_runtime_parameters(self, engine: str, client_count: int, preset = None, query_count = None) -> Path:
        """Returns the parameter file for the workload."""
        if self.is_official:
            # For official workloads, use the pre-defined parameter file from local copy
            param_files = self.dataset_data.get("param_files", {})
            if engine not in param_files:
                raise ValueError(f"No parameter file defined for engine '{engine}' in dataset '{self.dataset_name}'")
            
            # Read the local parameter file
            local_param_file = Path("workloads") / self.workload_name / param_files[engine]
            if not local_param_file.exists():
                raise FileNotFoundError(f"Parameter file not found: {local_param_file}")
            
            # Check if custom index template exists or query_count override is needed
            local_workload_dir = Path("workloads") / self.workload_name
            custom_index_file = local_workload_dir / "indices" / f"{engine}-index.json"
            
            if custom_index_file.exists() or query_count is not None:
                # Modify parameter file to use custom index template and/or query count
                params = json.loads(local_param_file.read_text())
                
                # Set path to custom template (template is already injected to pod)
                if custom_index_file.exists():
                    params["target_index_body"] = f"indices/{engine}-index.json"
                
                # Override query count if specified
                if query_count is not None:
                    params["query_count"] = query_count
                
                # Write modified params to temp file
                temp_param_file = Path(f"./tmp_{engine}_{self.dataset_name}_params.json")
                temp_param_file.write_text(json.dumps(params, indent=2))
                return temp_param_file
            
            return local_param_file
        else:
            # For custom workloads, generate parameters dynamically
            params = {
                "vertex_dimension": self.dataset_data["dimension"],
                "index_name": f"{engine}_index",
                "target_clients": client_count,
                "engine_settings": {"type": engine}
            }

            # Merge base engine defaults
            engine_file = self.config_dir / f"engines/{engine}.yaml"
            if engine_file.exists():
                engine_data = self._load_yaml(engine_file)
                settings = engine_data.get("defaults", {}).copy()
                if preset and preset in engine_data.get("tuning_presets", {}):
                    settings.update(engine_data["tuning_presets"][preset])
                params["engine_settings"].update(settings)

            # Merge dataset-specific engine overrides
            dataset_overrides = self.dataset_data.get("engine_overrides", {}).get(engine, {})
            params["engine_settings"].update(dataset_overrides)

            output_path = Path(f"./tmp_{engine}_workload_params.json")
            output_path.write_text(json.dumps(params, indent=2))
            return output_path

    def inject_all_templates(self, namespace: str, pod_name: str = "opensearch-benchmark-client"):
        """Prepares workload files on the pod."""
        if self.is_official:
            # For official workloads, inject custom templates into the pre-installed workload
            local_workload_dir = Path("workloads") / self.workload_name
            local_templates_dir = local_workload_dir / "indices"
            local_test_procedures_dir = local_workload_dir / "test_procedures"
            
            has_custom_files = False
            
            # Inject custom index templates
            if local_templates_dir.exists():
                print(f"  📝 Injecting custom index templates into official workload '{self.workload_name}'...")
                
                # Inject templates into the pre-installed workload at /root/opensearch-benchmark-workloads
                remote_workload_indices = f"{self.workload_path}/indices"
                
                # Ensure the indices directory exists
                self._exec_raw_command(namespace, pod_name, ["mkdir", "-p", remote_workload_indices])
                
                # Inject custom index templates
                for template_file in local_templates_dir.glob("*.json"):
                    payload = template_file.read_text(encoding="utf-8")
                    remote_path = f"{remote_workload_indices}/{template_file.name}"
                    self._write_file_to_pod(namespace, pod_name, remote_path, payload)
                    print(f"  ✅ Injected index template: {template_file.name}")
                has_custom_files = True
            
            # Inject custom test procedures
            if local_test_procedures_dir.exists():
                print(f"  📝 Injecting custom test procedures into official workload '{self.workload_name}'...")
                
                remote_test_procedures = f"{self.workload_path}/test_procedures"
                
                # Ensure the test_procedures directory exists
                self._exec_raw_command(namespace, pod_name, ["mkdir", "-p", remote_test_procedures])
                
                # Inject custom test procedures
                for procedure_file in local_test_procedures_dir.glob("*.json"):
                    payload = procedure_file.read_text(encoding="utf-8")
                    remote_path = f"{remote_test_procedures}/{procedure_file.name}"
                    self._write_file_to_pod(namespace, pod_name, remote_path, payload)
                    print(f"  ✅ Injected test procedure: {procedure_file.name}")
                has_custom_files = True
            
            if has_custom_files:
                print(f"  ✅ Using official workload at {self.workload_path} with custom files")
            else:
                print(f"  ℹ️  Using official workload '{self.workload_name}' with default files")
        else:
            # For custom workloads, copy entire workload structure
            print(f"  📝 Setting up custom workload '{self.workload_name}'...")
            
            local_workload_dir = Path("workloads") / self.workload_name
            remote_base = self.workload_path
            
            # Create directory structure
            self._exec_raw_command(namespace, pod_name, ["mkdir", "-p", f"{remote_base}/indices"])
            
            # Copy workload files
            for file in ["workload.json", "workload.py", "__init__.py", "runners.py"]:
                local_file = local_workload_dir / file
                if local_file.exists():
                    payload = local_file.read_text(encoding="utf-8")
                    self._write_file_to_pod(namespace, pod_name, f"{remote_base}/{file}", payload)
            
            # Copy operations directory if exists
            self._copy_directory_to_pod(namespace, pod_name, local_workload_dir / "operations", f"{remote_base}/operations")
            
            # Copy test_procedures directory if exists
            self._copy_directory_to_pod(namespace, pod_name, local_workload_dir / "test_procedures", f"{remote_base}/test_procedures")
            
            # Inject custom index templates from workload directory
            local_templates_dir = local_workload_dir / "indices"
            if local_templates_dir.exists():
                for template_file in local_templates_dir.glob("*.json"):
                    payload = template_file.read_text(encoding="utf-8")
                    self._write_file_to_pod(namespace, pod_name, f"{remote_base}/indices/{template_file.name}", payload)
                    print(f"  ✅ Injected: {template_file.name}")
            
            print(f"  ✅ Custom workload ready at {remote_base}")
            
            # Download data files if specified
            self._download_data_files(namespace, pod_name)

    def _download_data_files(self, namespace: str, pod_name: str):
        """Download data files specified in dataset configuration."""
        data_files = self.dataset_data.get("data_files", [])
        if not data_files:
            return
        
        data_dir = self.dataset_data.get("data_dir", f"/datasets/{self.dataset_name}")
        
        print(f"  📥 Checking data files in {data_dir}...")
        
        # Create data directory
        self._exec_raw_command(namespace, pod_name, ["mkdir", "-p", data_dir])
        
        for file_info in data_files:
            file_name = file_info.get("name")
            file_url = file_info.get("url")
            file_range = file_info.get("range")
            
            if not file_name or not file_url:
                continue
            
            file_path = f"{data_dir}/{file_name}"
            
            # Calculate expected file size from range
            expected_size = None
            if file_range:
                try:
                    # Parse range like "0-4100000000" to get size
                    start, end = file_range.split("-")
                    expected_size = int(end) - int(start) + 1
                except:
                    pass
            
            # Check if file exists and has correct size
            needs_download = False
            try:
                # Get file size
                stat_output = self._exec_raw_command(namespace, pod_name, ["stat", "-c", "%s", file_path])
                current_size = int(stat_output.strip())
                
                if expected_size and current_size != expected_size:
                    print(f"  ⚠️  File size mismatch for {file_name}")
                    print(f"     Current: {current_size} bytes, Expected: {expected_size} bytes")
                    print(f"     Re-downloading with updated range...")
                    needs_download = True
                else:
                    print(f"  ✅ File already exists with correct size: {file_name}")
                    continue
            except:
                # File doesn't exist
                needs_download = True
            
            if needs_download:
                print(f"  📥 Downloading {file_name}...")
                print(f"     URL: {file_url}")
                if file_range:
                    if expected_size:
                        size_mb = expected_size / (1024 * 1024)
                        print(f"     Range: {file_range} ({size_mb:.1f} MB)")
                    else:
                        print(f"     Range: {file_range}")
                
                # Use wget in silent mode - we'll show our own progress messages
                wget_cmd = ["wget", "-q", "-O", file_path]
                
                # Add range header if specified
                if file_range:
                    wget_cmd.extend(["--header", f"Range: bytes={file_range}"])
                
                wget_cmd.append(file_url)
                
                # Execute download
                try:
                    output = self._exec_raw_command(namespace, pod_name, wget_cmd)
                    
                    # Verify download completed
                    stat_output = self._exec_raw_command(namespace, pod_name, ["stat", "-c", "%s", file_path])
                    downloaded_size = int(stat_output.strip())
                    downloaded_mb = downloaded_size / (1024 * 1024)
                    
                    print(f"  ✅ Downloaded: {file_name} ({downloaded_mb:.1f} MB)")
                except Exception as e:
                    print(f"  ❌ Failed to download {file_name}: {str(e)}")
                    raise

    def _write_file_to_pod(self, namespace: str, pod_name: str, dest_path: str, content: str):
        try:
            resp = stream(
                self.k8s_core.connect_get_namespaced_pod_exec, pod_name, namespace,
                container="benchmark", command=['bash', '-c', f'cat > {dest_path}'],
                stderr=True, stdin=True, stdout=True, tty=False, _preload_content=False
            )
            resp.write_stdin(content)
            resp.close()
        except ApiException as e:
            self._handle_k8s_error(e, pod_name, namespace)
        except AttributeError as e:
            if "'NoneType' object has no attribute 'decode'" in str(e):
                self._handle_pod_not_found_error(pod_name, namespace)
            else:
                raise
        except Exception as e:
            print(f"\n❌ Error: Unexpected error writing to pod '{pod_name}': {str(e)}")
            sys.exit(1)

    def _exec_raw_command(self, namespace: str, pod_name: str, cmd: list):
        try:
            resp = stream(
                self.k8s_core.connect_get_namespaced_pod_exec, pod_name, namespace,
                container="benchmark", command=cmd,
                stderr=True, stdin=False, stdout=True, tty=False,
                _preload_content=False
            )
            # Read and close the response
            output = ""
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    output += resp.read_stdout()
                if resp.peek_stderr():
                    stderr = resp.read_stderr()
                    if stderr:
                        print(f"  ⚠️  Command stderr: {stderr}")
            resp.close()
            return output
        except ApiException as e:
            self._handle_k8s_error(e, pod_name, namespace)
        except AttributeError as e:
            if "'NoneType' object has no attribute 'decode'" in str(e):
                self._handle_pod_not_found_error(pod_name, namespace)
            else:
                raise
        except Exception as e:
            print(f"\n❌ Error: Unexpected error executing command on pod '{pod_name}': {str(e)}")
            sys.exit(1)
    
    def _handle_k8s_error(self, e: ApiException, pod_name: str, namespace: str):
        """Handle Kubernetes API exceptions with clear error messages."""
        print(f"\n❌ Error: Failed to connect to pod '{pod_name}' in namespace '{namespace}'")
        if e.status == 404:
            self._handle_pod_not_found_error(pod_name, namespace)
        else:
            print(f"   Kubernetes API error: {e.reason}")
            sys.exit(1)
    
    def _handle_pod_not_found_error(self, pod_name: str, namespace: str):
        """Handle pod not found errors with helpful guidance."""
        print(f"   Pod '{pod_name}' not found in namespace '{namespace}'")
        print(f"\n   💡 Troubleshooting steps:")
        print(f"   1. Check if the pod exists:")
        print(f"      kubectl get pods -n {namespace}")
        print(f"   2. If the pod is in a different namespace, check:")
        print(f"      kubectl get pods --all-namespaces | grep {pod_name}")
        print(f"   3. Ensure the benchmark client pod is deployed:")
        print(f"      kubectl apply -f gke-manifest/opensearch-benchmark-client.yaml")
        sys.exit(1)
    def _copy_directory_to_pod(self, namespace: str, pod_name: str, local_dir: Path, remote_dir: str):
        """Recursively copy a directory to the pod."""
        if not local_dir.exists():
            return
        
        self._exec_raw_command(namespace, pod_name, ["mkdir", "-p", remote_dir])
        
        for item in local_dir.rglob("*"):
            if item.is_file():
                relative_path = item.relative_to(local_dir)
                remote_path = f"{remote_dir}/{relative_path}"
                remote_parent = f"{remote_dir}/{relative_path.parent}" if relative_path.parent != Path(".") else remote_dir
                
                self._exec_raw_command(namespace, pod_name, ["mkdir", "-p", remote_parent])
                payload = item.read_text(encoding="utf-8")
                self._write_file_to_pod(namespace, pod_name, remote_path, payload)