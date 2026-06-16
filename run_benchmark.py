#!/usr/bin/env python3
from ast import Tuple
import time
import json
import concurrent.futures
import subprocess
from typing import Callable, Any, Optional
from pathlib import Path
from lib.config_manager import ConfigManager
from lib.dataset_manager import DatasetManager
from lib.benchmark_executor import BenchmarkExecutor
from lib.profiling_manager import ProfilingManager
from lib.metrics_collector import MetricsCollector
from lib.server_log_collector import ServerLogCollector
from lib.cluster_provisioner import ClusterProvisioner

def print_header(dataset_name: str):
    """Prints the primary execution header banner at framework startup."""
    print("=" * 66)
    print(f"🚀 RUNNING HYBRID ENGINE MATRIX FOR DATASET: {dataset_name}")
    print("=" * 66)


def print_separator(title: str):
    """Prints a clean, structured visual boundary for console logging output."""
    print("\n" + "━" * 66)
    print(f"📦 SCENARIO: {title}")
    print("━" * 66)
    
    print(f"\n  ▶ Running {title}...")


def retrieve_and_merge_params(
    namespace: str,
    pod_name: str,
    workload_params: Optional[str],
    extra_params: Optional[dict[str, Any]] = None
) -> Optional[str]:
    """
    Retrieves and parses base configurations from a remote JSON file, a raw JSON string,
    or a comma-separated key-value string, then merges them with runtime overrides.
    """
    merged_dict: dict[str, Any] = {}
    has_valid_base = False

    if workload_params and workload_params.strip():
        clean_params = workload_params.strip()
        
        # Format 1: Remote JSON file path
        if clean_params.endswith('.json'):
            try:
                cat_cmd = ["kubectl", "exec", "-n", namespace, pod_name, "--", "cat", clean_params]
                result = subprocess.run(cat_cmd, capture_output=True, text=True, check=True)
                
                if result.stdout.strip():
                    merged_dict = json.loads(result.stdout)
                    has_valid_base = True
            except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
                print(f"⚠️  Warning: Could not read remote config file '{clean_params}' ({type(e).__name__}).")
        
        else:
            try:
                # Format 2: Raw inline JSON string (e.g., '{"query_k":50}')
                merged_dict = json.loads(clean_params)
                has_valid_base = True
            except json.JSONDecodeError:
                # Format 3: Plain key-value pairs (e.g., "query_k:50,clients:4")
                try:
                    # Split into individual pairs, then split each pair into key and value
                    pairs = [pair.split(':', 1) for pair in clean_params.split(',') if ':' in pair]
                    if pairs:
                        for key, val in pairs:
                            # Clean up whitespace and attempt to cast numbers/booleans dynamically
                            key = key.strip()
                            val = val.strip()
                            if val.isdigit():
                                merged_dict[key] = int(val)
                            elif val.lower() == 'true':
                                merged_dict[key] = True
                            elif val.lower() == 'false':
                                merged_dict[key] = False
                            else:
                                merged_dict[key] = val
                        has_valid_base = True
                except Exception:
                    print(f"⚠️  Warning: Provided workload_params string '{clean_params}' could not be parsed.")

    # Guard Rail: Only update if we successfully parsed a base config OR have extra params to layer in
    if has_valid_base or extra_params:
        if extra_params:
            merged_dict.update(extra_params)
        
        # Always output a clean, unified, minified JSON block back to OpenSearch Benchmark
        return json.dumps(merged_dict, separators=(',', ':'))
    
    return None


def run_benchmark_with_profiling(
    executor: BenchmarkExecutor,
    profiler: ProfilingManager,
    metrics_collector: Optional[MetricsCollector],
    namespace: str,
    scenario_name: str,
    workload_name: str,
    workload_path: str,
    test_procedure: str,
    workload_params: Optional[str],
    extra_args: list[str],
    extra_params: Optional[dict] = None,
    enable_profiling: bool = True,
    warmup_seconds: int = 60,
    profile_duration: int = 45,
) -> bool:
    """
    Execute a benchmark with optional concurrent profiling and metrics collection.
    
    Args:
        executor: BenchmarkExecutor instance to run the benchmark
        profiler: ProfilingManager instance to capture performance data
        metrics_collector: Optional MetricsCollector instance for resource metrics
        namespace: Kubernetes namespace for the cluster
        scenario_name: Name identifier for the scenario (may include sweep subdirectory)
        workload_name: Name of the workload
        workload_path: Path to the workload configuration
        test_procedure: Test procedure to execute
        workload_params: Optional path to workload parameters file
        extra_args: Additional arguments for the benchmark command
        extra_params: Optional dictionary of extra parameters to merge
        enable_profiling: Whether to enable profiling (default: True)
        warmup_seconds: Seconds to wait before starting profiling (default: 60)
        profile_duration: Duration in seconds for profiling (default: 45)
    
    Returns:
        bool: True if benchmark succeeded, False otherwise
    """
    # Determine number of concurrent workers needed
    max_workers = 1  # Benchmark always runs
    if enable_profiling:
        max_workers += 1
    if metrics_collector and metrics_collector.enabled:
        max_workers += 1
    
    if max_workers > 1:
        # Run benchmark with concurrent profiling and/or metrics collection
        if enable_profiling:
            print(f"\n🔥 Profiling enabled for {profile_duration}s window")
        if metrics_collector and metrics_collector.enabled:
            print(f"📊 Metrics collection enabled")
        print()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as loop_pool:
            # 1. Fire off the OSB benchmark in the background
            bench_job = loop_pool.submit(
                executor.run_osb_command,
                scenario_name,
                workload_name,
                workload_path,
                test_procedure,
                workload_params,
                extra_params,
                extra_args
            )

            # 2. Start metrics collection if enabled
            metrics_job = None
            if metrics_collector and metrics_collector.enabled:
                metrics_job = loop_pool.submit(
                    metrics_collector.start_collection,
                    scenario_name,
                    interval=10
                )

            # 3. Allow benchmark to pass warm-up noise and hit a steady state
            time.sleep(warmup_seconds)

            # 4. Trigger low-overhead sampling profilers on the nodes for a precise window
            prof_job = None
            if enable_profiling:
                prof_job = loop_pool.submit(
                    profiler.profile_steady_state,
                    namespace,
                    scenario_name,
                    duration=profile_duration,
                    engine_results_dir=executor.results_dir,
                )

            # Wait for benchmark to complete first
            success, _ = bench_job.result()
            
            # Stop metrics collection now that benchmark is done
            if metrics_collector and metrics_collector.enabled:
                metrics_collector.stop_collection()
            
            # Then wait for profiling if enabled
            if prof_job:
                profiling_output = prof_job.result()
                if profiling_output:
                    for line in profiling_output:
                        print(line)
            
            # Wait for metrics collection to finish and save
            if metrics_job and metrics_collector:
                metrics_job.result()  # Wait for collection thread to finish
                metrics_collector.save_metrics(scenario_name)
            
            return success
    else:
        # Run benchmark without profiling or metrics
        success, _ = executor.run_osb_command(
            scenario_name,
            workload_name,
            workload_path,
            test_procedure,
            workload_params,
            extra_params,
            extra_args
        )
        return success


def main():
    # Initialize infrastructure automation components
    config = ConfigManager()
    dataset = DatasetManager(dataset_name=config.args.dataset)
    profiler = ProfilingManager(config)
    provisioner = ClusterProvisioner()

    print_header(dataset.dataset_name)
    
    # Provision metrics store if enabled and not already deployed
    if config.metrics_store_enabled:
        metrics_store_ns = "os-metrics"
        print(f"\n{'='*66}")
        print(f"📊 Checking Metrics Store Status...")
        print(f"{'='*66}")
        
        auto_provision = config.args.quiet if hasattr(config.args, 'quiet') else False
        if not provisioner.ensure_cluster_ready(metrics_store_ns, auto_provision=auto_provision):
            print(f"⚠️  Metrics store not available - results will only be stored locally")
            print(f"   To deploy metrics store, run: ./gke-manifest/deploy-metrics-store.sh\n")
        else:
            print(f"✅ Metrics store is ready in namespace: {metrics_store_ns}")
            
            # Auto-deploy dashboards if configured
            if config.metrics_store_deploy_dashboards:
                print(f"\n📊 Checking OpenSearch Dashboards Status...")
                try:
                    # Check if dashboards are already deployed
                    result = subprocess.run(
                        ["kubectl", "get", "deployment", "opensearch-dashboards", "-n", metrics_store_ns],
                        capture_output=True,
                        text=True
                    )
                    
                    if result.returncode != 0:
                        # Dashboards not deployed, deploy them
                        print(f"   Deploying OpenSearch Dashboards...")
                        deploy_result = subprocess.run(
                            ["./gke-manifest/deploy-dashboards.sh"],
                            capture_output=True,
                            text=True
                        )
                        
                        if deploy_result.returncode == 0:
                            print(f"✅ OpenSearch Dashboards deployed successfully")
                            # Extract and display the dashboard URL from the output
                            for line in deploy_result.stdout.split('\n'):
                                if 'URL:' in line or 'port-forward' in line:
                                    print(f"   {line.strip()}")
                        else:
                            print(f"⚠️  Failed to deploy dashboards automatically")
                            print(f"   You can deploy manually: ./gke-manifest/deploy-dashboards.sh")
                    else:
                        print(f"✅ OpenSearch Dashboards already deployed")
                        # Get the service info
                        svc_result = subprocess.run(
                            ["kubectl", "get", "svc", "opensearch-dashboards", "-n", metrics_store_ns,
                             "-o", "jsonpath={.status.loadBalancer.ingress[0].ip}"],
                            capture_output=True,
                            text=True
                        )
                        if svc_result.stdout.strip():
                            print(f"   Dashboard URL: http://{svc_result.stdout.strip()}:5601")
                        else:
                            print(f"   Use port-forward: kubectl port-forward -n {metrics_store_ns} svc/opensearch-dashboards 5601:5601")
                
                except Exception as e:
                    print(f"⚠️  Could not check/deploy dashboards: {e}")
            
            print()  # Empty line for spacing

    # Sweep sequentially across target engine nodes (e.g., jvector, faiss, lucene)
    for engine in config.target_engines:
        ns = f"os-{engine}"
        
        # Check if cluster is provisioned, provision if needed
        auto_provision = config.args.quiet if hasattr(config.args, 'quiet') else False
        if not provisioner.ensure_cluster_ready(ns, auto_provision=auto_provision):
            print(f"⚠️  Skipping {engine} - cluster not ready\n")
            continue
        engine_dir = config.results_root / f"{dataset.dataset_name}-{engine}"
        
        # Get default parameters from dataset config
        default_params = dataset.get_default_params()
        
        executor = BenchmarkExecutor(engine, ns, engine_dir, config, dataset_name=dataset.dataset_name, default_params=default_params)
        
        # Initialize metrics collector for this engine
        metrics_collector = MetricsCollector(
            namespace=ns,
            results_dir=engine_dir,
            enabled=config.metrics_enabled
        ) if config.metrics_enabled else None

        # Step 1: Pull the latest workloads in pod
        dataset.pull_workload_repo(namespace=ns)
        
        # Get index name from dataset config, fallback to workload name if not specified
        index_name = dataset.dataset_data.get("index_name", dataset.workload_name)
        print(f"\n🚢  {engine} engine selected - index: {index_name}")
        
        # Get default parameters from dataset config
        default_params = dataset.get_default_params()
        if default_params:
            print(f"  📋 Default parameters from dataset config:")
            for key, value in default_params.items():
                print(f"     {key}: {value}")
        
        # Get workload params file for this engine
        param_files = dataset.dataset_data.get("param_files", {})
        if engine not in param_files:
            print(f"\n⚠️  No parameter file defined for engine '{engine}' in dataset '{dataset.dataset_name}'")
            print(f"   Available engines: {', '.join(param_files.keys())}")
            print(f"   Skipping {engine}.\n")
            continue
        
        workload_name = f"{dataset.workload_name}"
        workload_params = f"{dataset.workload_path}/{param_files[engine]}" # path to workload params json file
        workload_path = dataset.workload_path # path to directory where workload.json is located

        # Get filtered test procedures based on user-selected scenarios
        filtered_procedures = dataset.get_filtered_procedures(config.target_scenarios)
        
        if not filtered_procedures:
            print(f"\n⚠️  No test procedures match selected scenarios for {engine}. Skipping.\n")
            continue
        
        # Execute test procedures in order
        skip_remaining = False
        for idx, (test_procedure, scenario_type, proc_config) in enumerate(filtered_procedures):
            if skip_remaining:
                break
                
            # Get parameter sweeps from procedure config (if any)
            parameter_sweeps = proc_config.get("parameter_sweeps", [])
            
            # If no sweeps defined, create a single default sweep with no extra params
            if not parameter_sweeps:
                parameter_sweeps = [{"params": {}}]
            
            if scenario_type == 'create-index':
                print_separator(f"Create Index [{engine}]")
                for sweep_idx, sweep_config in enumerate(parameter_sweeps, 1):
                    params = sweep_config.get("params", {})
                    if params:
                        param_desc = ", ".join(f"{k}={v}" for k, v in params.items())
                        print(f"  ▶ Creating index with {param_desc}...")
                    else:
                        print(f"  ▶ Creating index...")
                    
                    success, output = executor.run_osb_command(
                        scenario_name=f"scenario-{idx+1}-create-index" + (f"/sweep-{sweep_idx}" if len(parameter_sweeps) > 1 else ""),
                        workload_name=workload_name,
                        workload_path=workload_path,
                        test_procedure=test_procedure,
                        workload_params=workload_params,
                        extra_params=params if params else None,
                        extra_args=[]
                    )
                    
                    if not success:
                        print(f"\n❌ Index creation failed for {engine}. Skipping remaining scenarios.\n")
                        skip_remaining = True
                        break
                    
                    # Validate index mapping
                    print(f"  🔍 Validating index mapping for '{index_name}'...")
                    is_valid, message = executor.validate_vector_field_type(index_name)
                    
                    if not is_valid:
                        print(f"\n❌ Index validation failed: {message}")
                        print(f"   The index was created but does not have the proper knn_vector mapping.")
                        print(f"   This indicates an issue with the index template. Skipping remaining scenarios.\n")
                        skip_remaining = True
                        break
                    
                    print(f"  ✅ Index mapping validated successfully")
            
            elif scenario_type == 'ingest':
                print_separator(f"Bulk Ingest [{engine}]")
                for sweep_idx, sweep_config in enumerate(parameter_sweeps, 1):
                    params = sweep_config.get("params", {})
                    if params:
                        param_desc = ", ".join(f"{k}={v}" for k, v in params.items())
                        print(f"  ▶ Starting bulk data ingestion with {param_desc}...")
                    else:
                        print(f"  ▶ Starting bulk data ingestion...")
                    
                    success = run_benchmark_with_profiling(
                        executor=executor,
                        profiler=profiler,
                        metrics_collector=metrics_collector,
                        namespace=ns,
                        scenario_name=f"scenario-{idx+1}-bulk-ingest" + (f"/sweep-{sweep_idx}" if len(parameter_sweeps) > 1 else ""),
                        workload_name=workload_name,
                        workload_path=workload_path,
                        test_procedure=test_procedure,
                        workload_params=workload_params,
                        extra_params=params if params else None,
                        extra_args=[],
                        enable_profiling=config.profiling_enabled,
                        warmup_seconds=30,
                        profile_duration=45,
                    )
                    
                    if not success:
                        print(f"\n❌ Bulk ingestion failed for {engine}. Skipping remaining scenarios.\n")
                        skip_remaining = True
                        break
            
            elif scenario_type == 'merge':
                print_separator(f"Force Merge [{engine}]")
                for sweep_idx, sweep_config in enumerate(parameter_sweeps, 1):
                    params = sweep_config.get("params", {})
                    if params:
                        param_desc = ", ".join(f"{k}={v}" for k, v in params.items())
                        print(f"  ▶ Starting force merge with {param_desc}...")
                    else:
                        print(f"  ▶ Starting force merge...")
                    
                    success = run_benchmark_with_profiling(
                        executor=executor,
                        profiler=profiler,
                        metrics_collector=metrics_collector,
                        namespace=ns,
                        scenario_name=f"scenario-{idx+1}-force-merge" + (f"/sweep-{sweep_idx}" if len(parameter_sweeps) > 1 else ""),
                        workload_name=workload_name,
                        workload_path=workload_path,
                        test_procedure=test_procedure,
                        workload_params=workload_params,
                        extra_params=params if params else None,
                        extra_args=[],
                        enable_profiling=config.profiling_enabled,
                        warmup_seconds=30,
                        profile_duration=60,
                    )
                    
                    if not success:
                        print(f"\n❌ Force merge failed for {engine}. Skipping remaining scenarios.\n")
                        skip_remaining = True
                        break
            
            elif scenario_type == 'refresh':
                print_separator(f"Refresh Index [{engine}]")
                for sweep_idx, sweep_config in enumerate(parameter_sweeps, 1):
                    params = sweep_config.get("params", {})
                    if params:
                        param_desc = ", ".join(f"{k}={v}" for k, v in params.items())
                        print(f"  ▶ Refreshing index with {param_desc}...")
                    else:
                        print(f"  ▶ Refreshing index...")
                    
                    success = run_benchmark_with_profiling(
                        executor=executor,
                        profiler=profiler,
                        metrics_collector=metrics_collector,
                        namespace=ns,
                        scenario_name=f"scenario-{idx+1}-refresh" + (f"/sweep-{sweep_idx}" if len(parameter_sweeps) > 1 else ""),
                        workload_name=workload_name,
                        workload_path=workload_path,
                        test_procedure=test_procedure,
                        workload_params=workload_params,
                        extra_params=params if params else None,
                        extra_args=[],
                        enable_profiling=config.profiling_enabled,
                        warmup_seconds=10,
                        profile_duration=30,
                    )
                    
                    if not success:
                        print(f"\n❌ Refresh failed for {engine}. Skipping remaining scenarios.\n")
                        skip_remaining = True
                        break
            
            elif scenario_type == 'search':
                print_separator(title=f"Search [{engine}]")
                for sweep_idx, sweep_config in enumerate(parameter_sweeps, 1):
                    params = sweep_config.get("params", {})
                    if params:
                        param_desc = ", ".join(f"{k}={v}" for k, v in params.items())
                        print(f"  ▶ Running search sweep #{sweep_idx} with {param_desc}...")
                    else:
                        print(f"  ▶ Running search with default parameters...")
                    
                    run_benchmark_with_profiling(
                        executor=executor,
                        profiler=profiler,
                        metrics_collector=metrics_collector,
                        namespace=ns,
                        scenario_name=f"scenario-{idx+1}-search" + (f"/sweep-{sweep_idx}" if len(parameter_sweeps) > 1 else ""),
                        workload_name=workload_name,
                        workload_path=workload_path,
                        test_procedure=test_procedure,
                        workload_params=workload_params,
                        extra_params=params if params else None,
                        extra_args=[],
                        enable_profiling=config.profiling_enabled,
                        warmup_seconds=30,
                        profile_duration=45,
                    )
            
            

        # Collect cluster telemetry after all scenarios complete for this engine
        print(f"\n{'='*66}")
        print(f"📊 Collecting Cluster Telemetry ... ")
        print(f"{'='*66}")
        executor.collect_telemetry(index_name=index_name)
        
        # Collect server logs from OpenSearch pods on server-pool
        try:
            print(f"\n{'='*66}")
            print(f"📋 Collecting Server Logs and GC Logs from {ns} ... ")
            print(f"{'='*66}")
            log_collector = ServerLogCollector(namespace=ns, results_dir=config.results_root)
            log_collector.collect_all_logs()
        except Exception as e:
            print(f"⚠️  Warning: Failed to collect server logs: {e}")
            print(f"   Continuing with benchmark completion...")
        
        # Deprovision cluster if configured
        if config.auto_deprovision:
            provisioner.deprovision_namespace(ns)

    print(
        f"\n✅ Matrix Finished. Output stored in: {config.results_root}"
    )


if __name__ == "__main__":
    main()