import argparse
import sys
from pathlib import Path
from datetime import datetime
import yaml

class ConfigManager:
    """Handles CLI parsing, interactive fallback menus, and execution layout boundaries."""
    
    def __init__(self):
        # Parse command line tokens first to check for --results-dir
        self.args, unknown = self._parse_flags()
        
        # Use provided results directory or create new timestamped one
        if self.args.results_dir:
            self.results_root = Path(self.args.results_dir)
            self.timestamp = self.results_root.name  # Extract timestamp from directory name
        else:
            self.timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.results_root = Path(f"./results/{self.timestamp}")
        
        # Load dataset specifications to validate selection boundaries dynamically
        self.config_dir = Path("config")
        self.datasets_manifest = self._load_datasets_yaml()
        self.cluster_config = self._load_cluster_yaml()
        
        # Get provisioning settings from cluster config or CLI args
        provisioning_config = self.cluster_config.get("provisioning", {})
        self.auto_provision = self.args.quiet or provisioning_config.get("auto_provision", False)
        self.auto_deprovision = self.args.auto_deprovision or provisioning_config.get("auto_deprovision", False)
        
        # Determine execution runtime mode: Interactive vs Programmatic Flags
        if len(sys.argv) == 1:
            self._run_interactive_menu()
        else:
            self._validate_programmatic_args()
            
        self._display_configuration_and_confirm()

    def _load_datasets_yaml(self) -> dict:
        yaml_path = self.config_dir / "datasets.yaml"
        if not yaml_path.exists():
            return {"datasets": {
                "cohere-1m": {"dimension": 768, "format": "hdf5", "space_type": "l2"},
                "msmarco": {"dimension": 1024, "format": "fvec", "space_type": "cos"}
            }}
        with open(yaml_path, "r") as f:
            return yaml.safe_load(f)
    
    def _get_default_dataset(self) -> str:
        """Get the default dataset from datasets.yaml"""
        return self.datasets_manifest.get("default", "msmarco")

    def _load_cluster_yaml(self) -> dict:
        """Load cluster configuration from config/cluster.yaml"""
        yaml_path = self.config_dir / "cluster.yaml"
        if not yaml_path.exists():
            print(f"\n⚠️  Warning: {yaml_path} not found!")
            print("Please create config/cluster.yaml with the following content:")
            print("---")
            print("cluster_endpoint: \"opensearch-cluster:9200\"")
            print("client_options:")
            print("  timeout: 300")
            print("  use_ssl: true")
            print("  verify_certs: false")
            print("  basic_auth_user: \"admin\"")
            print("  basic_auth_password: \"admin\"")
            print("---")
            print("\nUsing defaults\n")
            return {
                "cluster_endpoint": "opensearch-cluster:9200",
                "client_options": {
                    "timeout": 300,
                    "use_ssl": True,
                    "verify_certs": False,
                    "basic_auth_user": "admin",
                    "basic_auth_password": "admin"
                },
                "pod_label_selector": "app=opensearch-cluster"
            }
        with open(yaml_path, "r") as f:
            config = yaml.safe_load(f)
            if not config or "cluster_endpoint" not in config:
                print(f"\n❌ Error: config/cluster.yaml must contain 'cluster_endpoint' setting")
                sys.exit(1)
            if "client_options" not in config:
                print(f"\n❌ Error: config/cluster.yaml must contain 'client_options' setting")
                sys.exit(1)
            return config

    def _parse_flags(self):
        parser = argparse.ArgumentParser(description="OSB Hybrid Automation Engine", add_help=False)
        parser.add_argument("--engine", type=str, default=None)
        parser.add_argument("--dataset", type=str, default=None)
        parser.add_argument("--scenario", type=str, default=None)
        parser.add_argument("--enable-profiling", action="store_true", default=False,
                          help="enable performance profiling during benchmark execution")
        parser.add_argument("--enable-metrics", action="store_true", default=False,
                          help="enable resource metrics collection and graphing")
        parser.add_argument("--quiet", "-q", action="store_true", default=False,
                          help="Skip confirmation prompt and proceed automatically")
        parser.add_argument("--auto-deprovision", action="store_true", default=False,
                          help="Automatically deprovision cluster after benchmark completes")
        parser.add_argument("--results-dir", type=str, default=None,
                          help="Use specific results directory (for parallel execution)")
        parser.add_argument("--help", "-h", action="store_true")
        return parser.parse_known_args()

    def _show_help(self):
        print("Usage: ./run-benchmark.sh [OPTIONS]")
        print("\nIf no options are provided, the script launches an interactive configuration menu.\n")
        print("Options:")
        print("  --engine <engine>       Specify engine(s): jvector, faiss, lucene, or all")
        print("  --dataset <dataset>     Specify dataset: cohere-1m, msmarco")
        print("  --scenario <scenario>   Specify scenario(s) comma-separated: index, merge, search, or all")
        print("  --enable-profiling     enable performance profiling (enabled by default)")
        print("  --help, -h              Show this help message")
        print("\nExamples:")
        print("  ./run-benchmark.sh --engine faiss --dataset msmarco --scenario all")
        print("  ./run-benchmark.sh --engine all --scenario search --enable-profiling")
        sys.exit(0)

    def _validate_programmatic_args(self):
        if self.args.help:
            self._show_help()
            
        if not self.args.engine:
            print("❌ Error: Non-interactive mode requires a targeted --engine parameter.")
            sys.exit(1)

    def _run_interactive_menu(self):
        """Run interactive menu to collect user configuration"""
        print("==========================================")
        print("OpenSearch Benchmark Test Configuration")
        print("==========================================")
        print("\nSelect target vector engine to test:")
        print("  j) jvector (Namespace: os-jvector)")
        print("  f) faiss   (Namespace: os-faiss)")
        print("  l) lucene  (Namespace: os-lucene)")
        print("  a) ALL Engines sequentially (Sweep Matrix)")
        
        engine_choice = input("\nChoose engine(s) [j/f/l/a]: ").strip().lower()
        engine_map = {
            'j': 'jvector', 'jvector': 'jvector',
            'f': 'faiss', 'faiss': 'faiss',
            'l': 'lucene', 'lucene': 'lucene',
            'a': 'all', 'all': 'all'
        }
        
        if engine_choice not in engine_map:
            print("❌ Invalid engine selection"); sys.exit(1)
        self.args.engine = engine_map[engine_choice]

        default_dataset = self._get_default_dataset()
        datasets = self.datasets_manifest.get("datasets", {})
        dataset_list = list(datasets.keys())
        
        print("\nSelect dataset to use:")
        for idx, dataset_name in enumerate(dataset_list, 1):
            dataset_info = datasets[dataset_name]
            dimension = dataset_info.get("dimension", "unknown")
            default_marker = " - default" if dataset_name == default_dataset else ""
            print(f"  {idx}) {dataset_name} ({dimension} dimensions){default_marker}")
        
        max_choice = len(dataset_list)
        dataset_choice = input(f"\nChoose dataset [1-{max_choice}] (press Enter for default): ").strip()
        
        if dataset_choice == "" or dataset_choice == "1":
            # Default or first option
            self.args.dataset = default_dataset if dataset_choice == "" else dataset_list[0]
        elif dataset_choice.isdigit() and 1 <= int(dataset_choice) <= max_choice:
            self.args.dataset = dataset_list[int(dataset_choice) - 1]
        else:
            print("❌ Invalid dataset selection"); sys.exit(1)

        print("\nSelect scenarios to run (use numbers, comma-separated):")
        print("  1) Index Creation & Data Ingestion")
        print("  2) Force Merge")
        print("  3) Search Tests (concurrency sweep)")
        print("  4) All Scenarios (1+2+3)")
        
        scenario_choices = input("\nEnter scenario choices (e.g., 1,3 or 4 for all): ").strip()
        selected_tokens = [t.strip() for t in scenario_choices.split(",")]
        
        scenarios = []
        if "4" in selected_tokens:
            scenarios = ["index", "merge", "search"]
        else:
            if "1" in selected_tokens:
                scenarios.append("index")
            if "2" in selected_tokens:
                scenarios.append("merge")
            if "3" in selected_tokens:
                scenarios.append("search")
            
        if not scenarios:
            print("❌ Error: No execution scenarios selected."); sys.exit(1)
            
        self.args.scenario = ",".join(scenarios)
        

    def _display_configuration_and_confirm(self):
        """Displays config metrics and requests execution confirmation."""
        dataset_name = self.args.dataset or self._get_default_dataset()
        meta = self.datasets_manifest["datasets"].get(dataset_name, {"dimension": "Unknown", "format": "Unknown", "space_type": "Unknown"})
        
        print("\n==========================================")
        print("📋 Selected Target Matrix Configuration:")
        print("==========================================")
        print(f"  Targeted Engines: {', '.join(self.target_engines)}")
        print(f"  Dataset:          {dataset_name} ({meta.get('dimension')}D, format: {meta.get('format')}, space: {meta.get('space_type')})")
        print(f"  Index Creation:   {'✅ YES' if 'index' in self.target_scenarios else '❌ NO'}")
        print(f"  Force Merge:      {'✅ YES' if 'merge' in self.target_scenarios else '❌ NO'}")
        print(f"  Search Tests:     {'✅ YES' if 'search' in self.target_scenarios else '❌ NO'}")
        print(f"  Profiling:        {'✅ ENABLED' if self.profiling_enabled else '❌ DISABLED'}")
        print(f"  Metrics:          {'✅ ENABLED' if self.metrics_enabled else '❌ DISABLED'}")
        print("==========================================")
        
        # Build and display the equivalent command line
        cmd_parts = ["./run-benchmark.sh"]
        cmd_parts.append(f"--engine {self.args.engine}")
        cmd_parts.append(f"--dataset {dataset_name}")
        cmd_parts.append(f"--scenario {self.args.scenario or 'all'}")
        if self.profiling_enabled:
            cmd_parts.append("--enable-profiling")
        
        print("\n💡 To run this configuration again without the menu:")
        print(f"   {' '.join(cmd_parts)}")
        print()
        
        # Skip confirmation if --quiet flag is provided
        if self.args.quiet:
            print("✅ Auto-confirmed (--quiet flag provided)")
            return
        
        confirm = input("Launch baseline suite sweep? (y/n): ").strip().lower()
        if confirm not in ['y', 'yes']:
            print("Execution cancelled.")
            sys.exit(0)
            
    @property
    def profiling_enabled(self) -> bool:
        """
        Returns whether profiling is enabled.
        Priority: CLI flag > cluster.yaml config > default (false)
        """
        # CLI flag takes precedence
        if self.args.enable_profiling:
            return True
        
        # Check cluster.yaml configuration
        profiling_config = self.cluster_config.get("profiling", {})
        return profiling_config.get("enabled", False)  # Default to disabled
    
    @property
    def metrics_enabled(self) -> bool:
        """
        Returns whether metrics collection is enabled.
        Priority: CLI flag > cluster.yaml config > default (false)
        """
        # CLI flag takes precedence
        if self.args.enable_metrics:
            return True
        
        # Check cluster.yaml configuration
        metrics_config = self.cluster_config.get("metrics", {})
        return metrics_config.get("enabled", False)  # Default to disabled
    
    @property
    def metrics_interval(self) -> int:
        """Returns the metrics collection interval in seconds."""
        metrics_config = self.cluster_config.get("metrics", {})
        return metrics_config.get("interval_seconds", 10)
    
    @property
    def metrics_generate_graphs(self) -> bool:
        """Returns whether to automatically generate graphs after metrics collection."""
        metrics_config = self.cluster_config.get("metrics", {})
        return metrics_config.get("generate_graphs", True)

    @property
    def target_engines(self) -> list:
        if self.args.engine == "all":
            return ["jvector", "faiss", "lucene"]
        return [t.strip() for t in self.args.engine.split(",")] if self.args.engine else ["jvector"]

    @property
    def target_scenarios(self) -> list:
        scenario_str = self.args.scenario or "index,merge,search"
        if "all" in scenario_str:
            return ["index", "merge", "search"]
        
        # Standardizes various aliases from the shell script seamlessly
        aliases = {
            "indexing": "index", "create-index": "index", "1": "index",
            "force-merge": "merge", "2": "merge",
            "search-only": "search", "3": "search"
        }
        
        raw_scenarios = [s.strip() for s in scenario_str.split(",")]
        return list(set(aliases.get(s, s) for s in raw_scenarios))

    @property
    def cluster_endpoint(self) -> str:
        """Returns the configured cluster endpoint."""
        return self.cluster_config.get("cluster_endpoint", "opensearch-cluster:9200")

    @property
    def client_options(self) -> str:
        """Returns the formatted client options string for opensearch-benchmark."""
        opts = self.cluster_config.get("client_options", {})
        # Convert dict to comma-separated key:value format
        parts = []
        for key, value in opts.items():
            # Convert Python boolean to lowercase string for opensearch-benchmark
            if isinstance(value, bool):
                value = str(value).lower()
            parts.append(f"{key}:{value}")
        return ",".join(parts)

    @property
    def pod_label_selector(self) -> str:
        """Returns the pod label selector for discovering OpenSearch pods."""
        return self.cluster_config.get("pod_label_selector", "app=opensearch-cluster")
