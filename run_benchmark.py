#!/usr/bin/env python3
import time
import json
import concurrent.futures
from typing import Callable, Any, Optional
from lib.config_manager import ConfigManager
from lib.dataset_manager import DatasetManager
from lib.benchmark_executor import BenchmarkExecutor
from lib.profiling_manager import ProfilingManager


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


def run_benchmark_with_profiling(
    executor: BenchmarkExecutor,
    profiler: ProfilingManager,
    namespace: str,
    scenario_name: str,
    workload_path: str,
    test_procedure: str,
    workload_params: Optional[str],
    extra_args: list[str],
    enable_profiling: bool = True,
    warmup_seconds: int = 60,
    profile_duration: int = 45,
) -> bool:
    """
    Execute a benchmark with optional concurrent profiling during steady-state operation.
    
    Args:
        executor: BenchmarkExecutor instance to run the benchmark
        profiler: ProfilingManager instance to capture performance data
        namespace: Kubernetes namespace for the cluster
        scenario_name: Name identifier for the scenario
        workload_path: Path to the workload configuration
        test_procedure: Test procedure to execute
        workload_params: Optional path to workload parameters file
        extra_args: Additional arguments for the benchmark command
        enable_profiling: Whether to enable profiling (default: True)
        warmup_seconds: Seconds to wait before starting profiling (default: 60)
        profile_duration: Duration in seconds for profiling (default: 45)
    
    Returns:
        bool: True if benchmark succeeded, False otherwise
    """
    if enable_profiling:
        # Run benchmark with concurrent profiling
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as loop_pool:
            # 1. Fire off the OSB benchmark in the background
            bench_job = loop_pool.submit(
                executor.run_osb_command,
                scenario_name,
                workload_path,
                test_procedure,
                workload_params,
                extra_args
            )

            # 2. Allow benchmark to pass warm-up noise and hit a steady state
            time.sleep(warmup_seconds)

            # 3. Trigger low-overhead sampling profilers on the nodes for a precise window
            prof_job = loop_pool.submit(
                profiler.profile_steady_state,
                namespace,
                scenario_name,
                duration=profile_duration,
            )

            # Wait for benchmark to complete first
            bench_job.result()
            
            # Then wait for profiling and get buffered output
            profiling_output = prof_job.result()
            
            # Display profiling output after benchmark completes
            if profiling_output:
                for line in profiling_output:
                    print(line)
            
            # Get benchmark result
            success, _ = bench_job.result()
            return success
    else:
        # Run benchmark without profiling
        success, _ = executor.run_osb_command(
            scenario_name,
            workload_path,
            test_procedure,
            workload_params,
            extra_args
        )
        return success


def main():
    # Initialize infrastructure automation components
    config = ConfigManager()
    dataset = DatasetManager(dataset_name=config.args.dataset)
    profiler = ProfilingManager(config)

    print_header(dataset.dataset_name)

    # Sweep sequentially across target engine nodes (e.g., jvector, faiss, lucene)
    for engine in config.target_engines:
        ns = f"os-{engine}"
        engine_dir = config.results_root / f"{engine}-metrics"
        executor = BenchmarkExecutor(engine, ns, engine_dir, config)

        # Step 1: Provision and push the static workload templates out to GKE
        dataset.inject_all_templates(namespace=ns)

        # -------------------------------------------------------------
        # SCENARIO 1: INDEX CREATION AND BULK LOADING
        # -------------------------------------------------------------
        if "index" in config.target_scenarios:
            print_separator(f"Create Index & Bulk Load [{engine}]")
            
            # Get parameter file
            local_param_file = dataset.generate_runtime_parameters(engine, client_count=1, query_count=config.query_count)
            remote_param_path = None
            
            # Copy parameter file to pod for both official and custom workloads
            remote_param_path = f"/tmp/params-{engine}-index.json"
            dataset._write_file_to_pod(
                ns,
                "opensearch-benchmark-client",
                remote_param_path,
                local_param_file.read_text(),
            )
            
            # Run index creation only (quick operation)
            print(f"  ▶ Creating index...")
            success, output = executor.run_osb_command(
                scenario_name="scenario-1-create-index",
                workload_path=dataset.workload_path,
                test_procedure=dataset.test_procedures["index"],
                workload_params=remote_param_path,
                extra_args=[]
            )
            
            # Stop if index creation failed
            if not success:
                print(f"\n❌ Index creation failed for {engine}. Skipping remaining scenarios.\n")
                if dataset.is_official and local_param_file.exists():
                    local_param_file.unlink(missing_ok=True)
                continue  # Skip to next engine
            
            # Validate that the index has proper knn_vector field type
            # Read index name from parameter file
            params = json.loads(local_param_file.read_text())
            index_name = params.get("target_index_name", f"{engine}_index")
            print(f"  🔍 Validating index mapping for '{index_name}'...")
            is_valid, message = executor.validate_vector_field_type(index_name)
            
            if not is_valid:
                print(f"\n❌ Index validation failed: {message}")
                print(f"   The index was created but does not have the proper knn_vector mapping.")
                print(f"   This indicates an issue with the index template. Skipping remaining scenarios.\n")
                if dataset.is_official and local_param_file.exists():
                    local_param_file.unlink(missing_ok=True)
                continue  # Skip to next engine
            
            print(f"  ✅ Index mapping validated successfully")
            
            # Now run bulk ingestion with profiling
            print(f"  ▶ Starting bulk data ingestion...")
            success = run_benchmark_with_profiling(
                executor=executor,
                profiler=profiler,
                namespace=ns,
                scenario_name="scenario-1-bulk-ingest",
                workload_path=dataset.workload_path,
                test_procedure=dataset.test_procedures["bulk"],
                workload_params=remote_param_path,
                extra_args=[],
                enable_profiling=config.profiling_enabled,
                warmup_seconds=60,
                profile_duration=45,
            )
            
            # Clean up temp file
            if local_param_file.exists():
                local_param_file.unlink(missing_ok=True)
            
            # Stop if bulk ingestion failed
            if not success:
                print(f"\n❌ Bulk ingestion failed for {engine}. Skipping remaining scenarios.\n")
                continue  # Skip to next engine

        # -------------------------------------------------------------
        # SCENARIO 2: FORCE MERGE
        # -------------------------------------------------------------
        if "merge" in config.target_scenarios:
            print_separator(f"Force Merge [{engine}]")
            
            # Get parameter file
            local_param_file = dataset.generate_runtime_parameters(engine, client_count=1, query_count=config.query_count)
            
            # Copy parameter file to pod for both official and custom workloads
            remote_param_path = f"/tmp/params-{engine}-merge.json"
            dataset._write_file_to_pod(
                ns,
                "opensearch-benchmark-client",
                remote_param_path,
                local_param_file.read_text(),
            )

            success = run_benchmark_with_profiling(
                executor=executor,
                profiler=profiler,
                namespace=ns,
                scenario_name="scenario-2-force-merge",
                workload_path=dataset.workload_path,
                test_procedure=dataset.test_procedures["merge"],
                workload_params=remote_param_path,
                extra_args=[],
                enable_profiling=config.profiling_enabled,
                warmup_seconds=30,
                profile_duration=60,
            )
            
            # Clean up temp file
            if local_param_file.exists():
                local_param_file.unlink(missing_ok=True)
            
            # Stop if force merge failed
            if not success:
                print(f"\n❌ Force merge failed for {engine}. Skipping remaining scenarios.\n")
                continue  # Skip to next engine

        # -------------------------------------------------------------
        # SCENARIO 3: SEARCH CONCURRENCY MATRIX WITH PROFILING
        # -------------------------------------------------------------
        if "search" in config.target_scenarios:
            print_separator(f"Search Concurrency sweeps [{engine}]")
            client_matrix = config.search_client_counts

            for clients in client_matrix:
                print(
                    f"  ▶ Running search sweep with {clients} concurrent clients..."
                )

                # Generate performance configuration parameters
                local_param_file = dataset.generate_runtime_parameters(
                    engine, client_count=clients, query_count=config.query_count
                )
                remote_param_path = f"/tmp/params-{engine}-{clients}.json"

                # Copy parameter file to pod
                dataset._write_file_to_pod(
                    ns,
                    "opensearch-benchmark-client",
                    remote_param_path,
                    local_param_file.read_text(),
                )

                run_benchmark_with_profiling(
                    executor=executor,
                    profiler=profiler,
                    namespace=ns,
                    scenario_name=f"scenario-search-only/clients-{clients}",
                    workload_path=dataset.workload_path,
                    test_procedure=dataset.test_procedures["search"],
                    workload_params=remote_param_path,
                    extra_args=[],
                    enable_profiling=config.profiling_enabled,
                    warmup_seconds=30,
                    profile_duration=45,
                )

                # Housekeeping: Unlink temporary workspace configuration file locally
                if local_param_file.exists():
                    local_param_file.unlink(missing_ok=True)

        # Collect cluster telemetry after all scenarios complete for this engine
        print(f"\n{'='*66}")
        print(f"📊 Collecting Cluster Telemetry ... ")
        print(f"{'='*66}")
        executor.collect_telemetry()

    print(
        f"\n✅ Matrix Finished. Output stored in: {config.results_root}"
    )


if __name__ == "__main__":
    main()