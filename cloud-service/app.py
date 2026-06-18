#!/usr/bin/env python3
"""Cloud-native OpenSearch Benchmark REST API Service"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import uuid
import logging
from datetime import datetime
from typing import Dict, Any
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from config_loader import ConfigLoader
from benchmark_runner import BenchmarkRunner

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='web', static_url_path='')
CORS(app)

# Initialize components
config_loader = ConfigLoader(workspace_dir='/workspace')
benchmark_runner = BenchmarkRunner(config_loader, results_dir='/results')

# Job storage (in-memory for now, could be replaced with database)
jobs: Dict[str, Dict[str, Any]] = {}
job_lock = threading.Lock()

# Thread pool for running benchmarks
MAX_CONCURRENT_JOBS = 3
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)


def run_benchmark_job(job_id: str, dataset: str, engine: str, scenario: str, options: Dict[str, Any]):
    """Execute a benchmark job in background"""
    try:
        with job_lock:
            jobs[job_id]['status'] = 'running'
            jobs[job_id]['started_at'] = datetime.utcnow().isoformat()
        
        logger.info(f"Starting job {job_id}: dataset={dataset}, engine={engine}, scenario={scenario}")
        
        # Extract workload params from options
        workload_params = options.get('workload_params', None)
        
        # Run the benchmark
        result = benchmark_runner.run_benchmark(
            dataset=dataset,
            engine=engine,
            scenario=scenario,
            job_id=job_id,
            enable_profiling=not options.get('no_profiling', False),
            enable_metrics=not options.get('no_metrics', False),
            workload_params=workload_params
        )
        
        # Update job with results
        with job_lock:
            jobs[job_id].update(result)
            if 'status' not in result:
                jobs[job_id]['status'] = 'completed'
            jobs[job_id]['completed_at'] = datetime.utcnow().isoformat()
        
        logger.info(f"Job {job_id} completed with status: {result.get('status', 'completed')}")
        
    except Exception as e:
        logger.error(f"Job {job_id} failed with error: {e}", exc_info=True)
        with job_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = str(e)
            jobs[job_id]['completed_at'] = datetime.utcnow().isoformat()


@app.route('/')
def index():
    """Serve the web UI"""
    return send_from_directory('web', 'index.html')


@app.route('/health')
def health():
    """Health check endpoint"""
    active_jobs = sum(1 for j in jobs.values() if j['status'] == 'running')
    return jsonify({
        'status': 'healthy',
        'active_jobs': active_jobs,
        'total_jobs': len(jobs)
    })


@app.route('/api/v1/sync', methods=['POST'])
def sync_config():
    """Pull latest changes from git and reload configuration
    
    This performs:
    1. git pull to get latest config/datasets.yaml and workload params
    2. Reload configuration into memory
    """
    try:
        logger.info("Syncing configuration from git...")
        result = config_loader.reload_config(git_pull=True)
        
        # Build user-friendly message
        if result['git_pull_success']:
            message = f"✓ Synced from git\n✓ Loaded {result['datasets_count']} datasets: {', '.join(result['datasets'])}"
        else:
            message = f"⚠ Git pull failed, using existing files\n✓ Loaded {result['datasets_count']} datasets: {', '.join(result['datasets'])}"
        
        logger.info(f"Sync complete: {result}")
        
        return jsonify({
            'status': 'success',
            'message': message,
            'git_pull_success': result['git_pull_success'],
            'git_output': result['git_output'],
            'datasets_count': result['datasets_count'],
            'datasets': result['datasets']
        })
    except Exception as e:
        logger.error(f"Error syncing configuration: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


@app.route('/api/v1/discover')
def discover():
    """Discover available datasets, engines, and test procedures"""
    try:
        datasets = config_loader.get_datasets()
        
        # Add test procedures to each dataset with metadata mapping
        for dataset in datasets:
            dataset_name = dataset['name']
            procedures = config_loader.get_test_procedures(dataset_name)
            
            # Build procedure metadata with UI labels and actual procedure names
            procedures_metadata = []
            proc_counts = {}
            
            for idx, proc in enumerate(procedures):
                name = proc.get('name') if isinstance(proc, dict) else proc
                proc_counts[name] = proc_counts.get(name, 0) + 1
                
                # Determine UI label
                total_count = sum(1 for p in procedures if (p.get('name') if isinstance(p, dict) else p) == name)
                if total_count > 1:
                    ui_label = f"{name}-scenario-{proc_counts[name]}"
                else:
                    ui_label = name
                
                # Check for parameter sweeps
                has_sweeps = isinstance(proc, dict) and 'parameter_sweeps' in proc
                sweep_count = len(proc.get('parameter_sweeps', [])) if has_sweeps else 0
                
                procedures_metadata.append({
                    'ui_label': ui_label,
                    'procedure_name': name,
                    'has_parameter_sweeps': has_sweeps,
                    'sweep_count': sweep_count
                })
            
            dataset['test_procedures'] = [p['ui_label'] for p in procedures_metadata]
            dataset['procedures_metadata'] = procedures_metadata
        
        return jsonify({
            'datasets': datasets,
            'max_concurrent_jobs': MAX_CONCURRENT_JOBS
        })
    except Exception as e:
        logger.error(f"Error discovering datasets: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/benchmark', methods=['POST'])
def trigger_benchmark():
    """Trigger a new benchmark job"""
    try:
        data = request.get_json() or {}
        
        # Extract parameters
        dataset = data.get('dataset')
        engines = data.get('engines', 'all')
        ui_scenario = data.get('scenarios', 'search')  # This is the UI label
        
        if not dataset:
            return jsonify({'error': 'dataset parameter is required'}), 400
        
        # Parse engines
        if engines == 'all':
            dataset_config = config_loader.get_dataset_config(dataset)
            engine_list = list(dataset_config.get('param_files', {}).keys())
        else:
            engine_list = [e.strip() for e in engines.split(',')]
        
        if not engine_list:
            return jsonify({'error': 'No engines specified or available'}), 400
        
        # For now, support single engine (can be extended for multiple)
        engine = engine_list[0]
        
        # Look up the actual procedure name from UI label
        procedures = config_loader.get_test_procedures(dataset)
        procedure_name = ui_scenario  # Default to UI label if not found
        proc_counts = {}
        
        for proc in procedures:
            name = proc.get('name') if isinstance(proc, dict) else proc
            proc_counts[name] = proc_counts.get(name, 0) + 1
            
            # Reconstruct the UI label for this procedure
            total_count = sum(1 for p in procedures if (p.get('name') if isinstance(p, dict) else p) == name)
            if total_count > 1:
                ui_label = f"{name}-scenario-{proc_counts[name]}"
            else:
                ui_label = name
            
            if ui_label == ui_scenario:
                procedure_name = name
                break
        
        logger.info(f"UI scenario '{ui_scenario}' maps to procedure '{procedure_name}'")
        
        # Ensure we have a valid procedure name
        if not procedure_name:
            return jsonify({'error': f'Invalid scenario: {ui_scenario}'}), 400
        
        # Validate request using the actual procedure name
        error = benchmark_runner.validate_benchmark_request(dataset, engine, procedure_name)
        if error:
            return jsonify({'error': error}), 400
        
        # Create job
        job_id = str(uuid.uuid4())
        
        with job_lock:
            jobs[job_id] = {
                'job_id': job_id,
                'status': 'queued',
                'dataset': dataset,
                'engine': engine,
                'scenario': procedure_name,  # Store actual procedure name
                'ui_scenario': ui_scenario,  # Store UI label for display
                'created_at': datetime.utcnow().isoformat(),
                'options': {
                    'no_profiling': data.get('no_profiling', False),
                    'no_metrics': data.get('no_metrics', False),
                    'workload_params': data.get('workload_params', None)
                }
            }
        
        # Submit job to executor with actual procedure name
        executor.submit(
            run_benchmark_job,
            job_id,
            dataset,
            engine,
            procedure_name,  # Pass actual procedure name
            jobs[job_id]['options']
        )
        
        logger.info(f"Job {job_id} queued: dataset={dataset}, engine={engine}, procedure={procedure_name} (UI: {ui_scenario})")
        
        return jsonify({
            'job_id': job_id,
            'status': 'queued',
            'dataset': dataset,
            'engine': engine,
            'scenario': procedure_name,
            'ui_scenario': ui_scenario,
            'status_url': f'/api/v1/benchmark/{job_id}'
        }), 202
        
    except Exception as e:
        logger.error(f"Error triggering benchmark: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/benchmark/<job_id>')
def get_job_status(job_id: str):
    """Get status of a specific job"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    with job_lock:
        job = jobs[job_id].copy()
    
    # Calculate progress if applicable
    if job['status'] == 'running':
        # Could add more detailed progress tracking here
        job['progress'] = 'in_progress'
    
    return jsonify(job)


@app.route('/api/v1/benchmark')
def list_jobs():
    """List all jobs"""
    with job_lock:
        total = len(jobs)
        job_list = sorted(jobs.values(), key=lambda x: x.get('created_at', ''), reverse=True)[:50]
    
    return jsonify({
        'total': total,
        'jobs': job_list
    })


@app.route('/api/v1/logs')
def get_benchmark_logs():
    """Get opensearch-benchmark logs"""
    try:
        log_file = Path('/datasets/opensearch-benchmark/.osb/logs/benchmark.log')
        if not log_file.exists():
            return jsonify({'error': 'Log file not found'}), 404
        
        # Get last N lines (default 100)
        lines = int(request.args.get('lines', 100))
        
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            tail_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        
        return jsonify({
            'log_file': str(log_file),
            'lines': len(tail_lines),
            'content': ''.join(tail_lines)
        })
    except Exception as e:
        logger.error(f"Error reading logs: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/cluster/<engine>/health')
def get_cluster_health(engine: str):
    """Check OpenSearch cluster health for a specific engine"""
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        
        target_host = config_loader.get_target_host(engine)
        url = f"https://{target_host}/_cluster/health"
        
        response = requests.get(
            url,
            auth=HTTPBasicAuth('admin', 'admin'),
            verify=False,
            timeout=10
        )
        
        if response.status_code == 200:
            health_data = response.json()
            return jsonify({
                'engine': engine,
                'cluster': target_host,
                'status': health_data.get('status'),
                'number_of_nodes': health_data.get('number_of_nodes'),
                'number_of_data_nodes': health_data.get('number_of_data_nodes'),
                'active_primary_shards': health_data.get('active_primary_shards'),
                'active_shards': health_data.get('active_shards'),
                'relocating_shards': health_data.get('relocating_shards'),
                'initializing_shards': health_data.get('initializing_shards'),
                'unassigned_shards': health_data.get('unassigned_shards'),
                'ready': health_data.get('status') in ['green', 'yellow']
            })
        else:
            return jsonify({
                'engine': engine,
                'cluster': target_host,
                'error': f'HTTP {response.status_code}',
                'ready': False
            }), response.status_code
            
    except Exception as e:
        logger.error(f"Error checking cluster health for {engine}: {e}", exc_info=True)
        return jsonify({
            'engine': engine,
            'error': str(e),
            'ready': False
        }), 500


if __name__ == '__main__':
    logger.info("Starting Cloud-Native OpenSearch Benchmark Service")
    logger.info(f"Workspace: /workspace")
    logger.info(f"Results: /results")
    
    # Log discovered datasets
    try:
        datasets = config_loader.get_datasets()
        logger.info(f"Discovered {len(datasets)} datasets")
        for ds in datasets:
            logger.info(f"  - {ds['name']}: engines={ds['engines']}")
    except Exception as e:
        logger.warning(f"Could not discover datasets: {e}")
    
    app.run(host='0.0.0.0', port=8080, debug=False)

# Made with Bob
