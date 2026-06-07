#!/usr/bin/env python3
"""
Parallel benchmark execution wrapper that runs all engines concurrently.
Each engine's output is redirected to separate log files for background execution.
Use view_logs.py to monitor progress in real-time.
"""
import sys
import time
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
import concurrent.futures
from lib.config_manager import ConfigManager


class ParallelBenchmarkRunner:
    """Manages parallel execution of benchmarks across multiple engines."""
    
    def __init__(self, config: ConfigManager):
        self.config = config
        self.results_root = Path(config.results_root).resolve()
        self.logs_dir = self.results_root / "parallel-logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.start_time = datetime.now()
        
    def run_engine_benchmark(self, engine: str) -> Dict[str, Any]:
        """
        Run benchmark for a single engine in the background.
        
        Args:
            engine: Engine name (jvector, faiss, lucene)
            
        Returns:
            Dict with engine name, log file path, return code, and duration
        """
        log_file = (self.logs_dir / f"{engine}.log").resolve()
        error_file = (self.logs_dir / f"{engine}.error.log").resolve()
        
        # Build command to run benchmark for single engine
        cmd = [
            sys.executable,  # Use same Python interpreter
            "run_benchmark.py",
            "--engine", engine,
            "--dataset", self.config.args.dataset or "cohere-1m",
            "--quiet",  # Skip confirmation prompt for parallel execution
        ]
        
        # Add scenario if specified
        if self.config.args.scenario:
            cmd.extend(["--scenario", self.config.args.scenario])
        
            
        # Add profiling flag if enabled
        if self.config.args.enable_profiling:
            cmd.append("--enable-profiling")
        
        print(f"🚀 Starting {engine} benchmark in background...")
        print(f"   Log file: {log_file.relative_to(Path.cwd())}")
        
        start_time = time.time()
        
        try:
            # Run process with output redirected to log files
            with open(log_file, 'w') as log_out, open(error_file, 'w') as log_err:
                # Write header to log file
                log_out.write(f"{'='*80}\n")
                log_out.write(f"Benchmark Execution Log - {engine.upper()}\n")
                log_out.write(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_out.write(f"Command: {' '.join(cmd)}\n")
                log_out.write(f"{'='*80}\n\n")
                log_out.flush()
                
                process = subprocess.Popen(
                    cmd,
                    stdout=log_out,
                    stderr=log_err,
                    bufsize=1,  # Line buffered
                    universal_newlines=True
                )
                
                # Wait for completion
                return_code = process.wait()
                
            duration = time.time() - start_time
            
            # Append completion status to log
            with open(log_file, 'a') as log_out:
                log_out.write(f"\n{'='*80}\n")
                log_out.write(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                log_out.write(f"Duration: {duration:.2f} seconds\n")
                log_out.write(f"Exit Code: {return_code}\n")
                log_out.write(f"{'='*80}\n")
            
            return {
                "engine": engine,
                "log_file": log_file,
                "error_file": error_file,
                "return_code": return_code,
                "duration": duration,
                "success": return_code == 0
            }
            
        except Exception as e:
            duration = time.time() - start_time
            error_msg = f"Failed to execute benchmark: {str(e)}"
            
            with open(error_file, 'a') as log_err:
                log_err.write(f"\n{error_msg}\n")
            
            return {
                "engine": engine,
                "log_file": log_file,
                "error_file": error_file,
                "return_code": -1,
                "duration": duration,
                "success": False,
                "error": error_msg
            }
    
    def run_parallel(self) -> List[Dict]:
        """
        Execute benchmarks for all target engines in parallel.
        
        Returns:
            List of result dictionaries for each engine
        """
        engines = self.config.target_engines
        
        print("=" * 80)
        print(f"🚀 PARALLEL BENCHMARK EXECUTION")
        print("=" * 80)
        print(f"Engines: {', '.join(engines)}")
        print(f"Dataset: {self.config.args.dataset or 'cohere-1m'}")
        print(f"Scenarios: {', '.join(self.config.target_scenarios)}")
        print(f"Results: {self.results_root}")
        print(f"Logs: {self.logs_dir}")
        print("=" * 80)
        print()
        print("💡 Tip: Use './view_logs.py' to monitor progress in real-time")
        print()
        
        # Create status file for monitoring
        status_file = self.logs_dir / "status.txt"
        with open(status_file, 'w') as f:
            f.write(f"Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Engines: {', '.join(engines)}\n")
            f.write(f"Status: RUNNING\n")
        
        results = []
        
        # Execute all engines in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(engines)) as executor:
            # Submit all jobs
            future_to_engine = {
                executor.submit(self.run_engine_benchmark, engine): engine 
                for engine in engines
            }
            
            # Wait for completion and collect results
            for future in concurrent.futures.as_completed(future_to_engine):
                engine = future_to_engine[future]
                try:
                    result = future.result()
                    results.append(result)
                    
                    status = "✅ SUCCESS" if result["success"] else "❌ FAILED"
                    print(f"{status} - {engine} completed in {result['duration']:.2f}s")
                    
                except Exception as e:
                    print(f"❌ FAILED - {engine} raised exception: {str(e)}")
                    results.append({
                        "engine": engine,
                        "success": False,
                        "error": str(e)
                    })
        
        # Update status file
        total_duration = (datetime.now() - self.start_time).total_seconds()
        with open(status_file, 'a') as f:
            f.write(f"Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Total Duration: {total_duration:.2f}s\n")
            f.write(f"Status: COMPLETED\n")
        
        return results
    
    def print_summary(self, results: List[Dict]):
        """Print execution summary."""
        print()
        print("=" * 80)
        print("📊 EXECUTION SUMMARY")
        print("=" * 80)
        
        total_duration = (datetime.now() - self.start_time).total_seconds()
        successful = sum(1 for r in results if r.get("success", False))
        failed = len(results) - successful
        
        print(f"Total Duration: {total_duration:.2f}s")
        print(f"Successful: {successful}/{len(results)}")
        print(f"Failed: {failed}/{len(results)}")
        print()
        
        for result in sorted(results, key=lambda x: x.get("duration", 0), reverse=True):
            engine = result["engine"]
            duration = result.get("duration", 0)
            status = "✅" if result.get("success", False) else "❌"
            
            print(f"{status} {engine:10s} - {duration:8.2f}s")
            
            if "log_file" in result:
                print(f"   Log: {result['log_file'].relative_to(Path.cwd())}")
            
            if not result.get("success", False) and "error" in result:
                print(f"   Error: {result['error']}")
        
        print()
        print(f"Results directory: {self.results_root}")
        print(f"Logs directory: {self.logs_dir}")
        print("=" * 80)


def main():
    """Main entry point for parallel benchmark execution."""
    # Initialize configuration
    config = ConfigManager()
    
    # Force engine to 'all' if not specified for parallel execution
    if not config.args.engine or config.args.engine == 'all':
        # Already set to all engines
        pass
    else:
        # User specified specific engines, respect that
        pass
    
    # Create parallel runner
    runner = ParallelBenchmarkRunner(config)
    
    # Execute benchmarks in parallel
    results = runner.run_parallel()
    
    # Print summary
    runner.print_summary(results)
    
    # Exit with error code if any benchmark failed
    if any(not r.get("success", False) for r in results):
        sys.exit(1)
    
    sys.exit(0)


if __name__ == "__main__":
    main()

# Made with Bob
