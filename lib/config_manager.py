import argparse
import sys
from pathlib import Path
from datetime import datetime
import yaml

class ConfigManager:
    """Handles CLI parsing, interactive fallback menus, and execution layout boundaries."""
    
    def __init__(self):
        self.timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.results_root = Path(f"./results/{self.timestamp}")
        
        # Load dataset specifications to validate selection boundaries dynamically
        self.config_dir = Path("config")
        self.datasets_manifest = self._load_datasets_yaml()
        self.cluster_config = self._load_cluster_yaml()
        
        # Parse command line tokens
        self.args, unknown = self._parse_flags()
        
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
        parser.add_argument("--clients", type=str, default=None,
                          help="Client counts for search concurrency (comma-separated, e.g., '10,50,100' or single value '50')")
        parser.add_argument("--queries", type=int, default=None,
                          help="Number of queries to execute during search tests (default: workload-specific)")
        parser.add_argument("--disable-profiling", action="store_true", default=False,
                          help="Disable performance profiling during benchmark execution")
        parser.add_argument("--help", "-h", action="store_true")
        return parser.parse_known_args()

    def _show_help(self):
        print("Usage: ./run-benchmark.sh [OPTIONS]")
        print("\nIf no options are provided, the script launches an interactive configuration menu.\n")
        print("Options:")
        print("  --engine <engine>       Specify engine(s): jvector, faiss, lucene, or all")
        print("  --dataset <dataset>     Specify dataset: cohere-1m, msmarco")
        print("  --scenario <scenario>   Specify scenario(s) comma-separated: index, merge, search, or all")
        print("  --clients <counts>      Client counts for search (comma-separated or single, e.g., '50' or '10,50,100')")
        print("  --queries <count>       Number of queries to execute during search tests")
        print("  --disable-profiling     Disable performance profiling (enabled by default)")
        print("  --help, -h              Show this help message")
        print("\nExamples:")
        print("  ./run-benchmark.sh --engine faiss --dataset msmarco --scenario all")
        print("  ./run-benchmark.sh --engine all --scenario search --disable-profiling")
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

        print("\nSelect dataset to use:")
        print("  1) cohere-1m (768 dimensions) - default")
        print("  2) msmarco (1024 dimensions)")
        
        dataset_choice = input("\nChoose dataset [1/2] (press Enter for default): ").strip()
        if dataset_choice == "1" or dataset_choice == "":
            self.args.dataset = "cohere-1m"
        elif dataset_choice == "2":
            self.args.dataset = "msmarco"
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
        
        # Ask for query count if search scenarios are selected
        if "search" in scenarios:
            query_input = input("\nNumber of queries for search tests (press Enter for workload default): ").strip()
            if query_input:
                try:
                    self.args.queries = int(query_input)
                except ValueError:
                    print("❌ Invalid query count. Using workload default.")
                    self.args.queries = None
        
        # Profiling is enabled by default in interactive mode (no prompt)
        self.args.disable_profiling = False

    def _display_configuration_and_confirm(self):
        """Displays config metrics and requests execution confirmation."""
        dataset_name = self.args.dataset or "cohere-1m"
        meta = self.datasets_manifest["datasets"].get(dataset_name, {"dimension": "Unknown", "format": "Unknown", "space_type": "Unknown"})
        
        print("\n==========================================")
        print("📋 Selected Target Matrix Configuration:")
        print("==========================================")
        print(f"  Targeted Engines: {', '.join(self.target_engines)}")
        print(f"  Dataset:          {dataset_name} ({meta.get('dimension')}D, format: {meta.get('format')}, space: {meta.get('space_type')})")
        print(f"  Index Creation:   {'✅ YES' if 'index' in self.target_scenarios else '❌ NO'}")
        print(f"  Force Merge:      {'✅ YES' if 'merge' in self.target_scenarios else '❌ NO'}")
        print(f"  Search Tests:     {'✅ YES' if 'search' in self.target_scenarios else '❌ NO'}")
        if 'search' in self.target_scenarios:
            query_display = f"{self.args.queries}" if self.args.queries else "workload default"
            print(f"  Query Count:      {query_display}")
            print(f"  Search Clients:   {', '.join(map(str, self.search_client_counts))}")
        print(f"  Profiling:        {'✅ ENABLED' if not self.args.disable_profiling else '❌ DISABLED'}")
        print("==========================================")
        
        confirm = input("\nLaunch baseline suite sweep? (y/n): ").strip().lower()
        if confirm != 'y' and confirm != 'yes':
            print("Execution cancelled.")
            sys.exit(0)

    @property
    def profiling_enabled(self) -> bool:
        """Returns whether profiling is enabled (enabled by default unless disabled)."""
        return not self.args.disable_profiling

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

    @property
    def search_client_counts(self) -> list:
        """Returns the list of client counts for search concurrency testing."""
        if self.args.clients:
            # Parse comma-separated values
            return [int(c.strip()) for c in self.args.clients.split(",")]
        # Default sweep
        return [10, 50, 100]

    @property
    def query_count(self):
        """Returns the number of queries to execute during search tests, or None for workload default."""
        return self.args.queries
