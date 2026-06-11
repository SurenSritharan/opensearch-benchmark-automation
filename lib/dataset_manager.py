import json
import yaml
import sys
import tempfile
from pathlib import Path
from lib.kubectl_helper import KubectlHelper

class DatasetManager:
    """Manages multi-layered engine/dataset parameters and pushes Jinja2 templates."""
    def __init__(self, dataset_name: str, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.dataset_name = dataset_name
        self.kubectl = KubectlHelper()
        
        # Parse global dataset metadata
        datasets_manifest = self._load_yaml(self.config_dir / "datasets.yaml")
        if dataset_name not in datasets_manifest["datasets"]:
            raise ValueError(f"Dataset '{dataset_name}' not defined in datasets.yaml")
        
        self.base_workload_path = "/datasets/opensearch-benchmark-workloads"
        self.dataset_data = datasets_manifest["datasets"][dataset_name]
        self.workload_name = self.dataset_data.get("workload_name", "default")
        self.workload_path = f"{self.base_workload_path}/{self.workload_name}"
        self.test_procedures = self.dataset_data.get("test_procedures", [])
        self.default_params = self.dataset_data.get("default_params", {})
    
    def get_filtered_procedures(self, target_scenarios: list) -> list:
        """Filter test procedures based on user-selected scenarios.
        
        Args:
            target_scenarios: List of scenario names (e.g., ['index', 'search', 'merge'])
        
        Returns:
            List of tuples: (test_procedure_name, scenario_type, procedure_config)
            where scenario_type is one of: 'create-index', 'bulk', 'search', 'merge'
            and procedure_config is the full procedure dict (may contain parameter_sweeps)
        """
        filtered = []
        
        for proc in self.test_procedures:
            # Handle both old format (string) and new format (dict with 'name' key)
            if isinstance(proc, str):
                proc_name = proc
                proc_config = {}
            else:
                proc_name = proc.get('name', '')
                proc_config = proc
            
            proc_lower = proc_name.lower()
            
            # Determine scenario type and check if user wants it
            if 'create-index' in proc_lower or 'index-only' in proc_lower:
                if 'index' in target_scenarios:
                    filtered.append((proc_name, 'create-index', proc_config))
            
            elif 'ingest' in proc_lower:
                if 'index' in target_scenarios:
                    filtered.append((proc_name, 'ingest', proc_config))
            
            elif 'search' in proc_lower:
                if 'search' in target_scenarios:
                    filtered.append((proc_name, 'search', proc_config))
            
            elif 'merge' in proc_lower or 'force-merge' in proc_lower:
                if 'merge' in target_scenarios:
                    filtered.append((proc_name, 'merge', proc_config))
        
        return filtered
    
    def get_default_params(self) -> dict:
        """Returns default workload parameters from dataset configuration.
        
        These parameters will be merged with workload params and can include
        any Jinja2 template variables like target_index_primary_shards,
        ef_construction, m, etc.
        
        Returns:
            Dictionary of default parameters
        """
        return self.default_params.copy()

    def _load_yaml(self, path: Path) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def pull_workload_repo(self, namespace: str, pod_name: str = "opensearch-benchmark-client-0"):
        """Pull latest changes from the workload repository on the pod."""
        
        remote_base = self.base_workload_path
        
        print(f"  🔄 Pulling latest workload definitions from git repository...")
        
        # Pull latest changes from the repository
        pull_cmd = ["sh", "-c", f"cd {remote_base} && git pull origin main"]
        output = self._exec_raw_command(namespace, pod_name, pull_cmd)
        
        if "Already up to date" in output:
            print(f"  ✅ Workload repository already up to date")
        else:
            print(f"  ✅ Updated workload repository with latest changes")
        
        # Show current commit for verification
        commit_cmd = ["sh", "-c", f"cd {remote_base} && git log -1 --oneline"]
        commit_info = self._exec_raw_command(namespace, pod_name, commit_cmd)
        print(f"  📌 Current commit: {commit_info.strip()}")
        
        if 'data_files' in self.dataset_data:
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
                # Check if file exists using test command (more reliable than stat)
                test_cmd = ["test", "-f", file_path]
                test_result = self._exec_raw_command(namespace, pod_name, test_cmd, allow_failure=True)
                
                if "Execution failed" in test_result:
                    # File doesn't exist
                    needs_download = True
                else:
                    # File exists, check size
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
            except Exception as e:
                # File doesn't exist or error checking
                print(f"  ℹ️  File not found: {file_name}, will download")
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

    def _exec_raw_command(self, namespace: str, pod_name: str, cmd: list, allow_failure: bool = False):
        """Execute a command in a pod using kubectl exec."""
        output = self.kubectl.exec_command(namespace, pod_name, "benchmark", cmd)
        if output.startswith("Execution failed"):
            if allow_failure:
                return output
            print(f"\n❌ Error: {output}")
            sys.exit(1)
        return output