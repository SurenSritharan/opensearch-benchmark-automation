#!/usr/bin/env python3
"""Cloud-native OpenSearch Benchmark REST API Service"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import threading
import uuid
import logging
import os
import sqlite3
import json
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from filelock import FileLock
from queue import Queue
from config_loader import ConfigLoader
from benchmark_runner import BenchmarkRunner

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='web', static_url_path='')
CORS(app)

# Initialize components
config_loader = ConfigLoader(workspace_dir='/workspace')
benchmark_runner = BenchmarkRunner(config_loader, results_dir='/results')

# SQLite database for job storage (shared across all workers)
DB_PATH = "/workspace/jobs.db"
db_lock = threading.RLock()

# Track which engine processors are running (in-memory per worker)
running_processors = set()
processor_lock = threading.Lock()

def init_db():
    """Initialize SQLite database for job storage and queue"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                dataset TEXT,
                engine TEXT,
                scenario TEXT,
                ui_scenario TEXT,
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                options TEXT,
                result TEXT,
                error TEXT,
                queue_position INTEGER
            )
        """)
        # Index for efficient queue queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_engine_status_queue
            ON jobs(engine, status, queue_position)
        """)
        conn.commit()
    logger.info(f"Initialized SQLite database at {DB_PATH}")

# Initialize database on module load
init_db()
logger.info(f"Initialized shared state in process (PID: {os.getpid()})")

def ensure_processor_running(engine: str):
    """Ensure a queue processor is running for the given engine"""
    with processor_lock:
        if engine not in running_processors:
            running_processors.add(engine)
            executor.submit(process_engine_queue, engine)
            logger.info(f"Started queue processor for engine: {engine}")
        else:
            logger.debug(f"Queue processor already running for engine: {engine}")

def save_job(job_id: str, job_data: Dict[str, Any]):
    """Save or update a job in the database"""
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            # Serialize complex fields to JSON
            options_json = json.dumps(job_data.get('options', {}))
            result_json = json.dumps(job_data.get('result', {})) if 'result' in job_data else None
            
            # For batch jobs, store scenarios list in options to preserve it
            if 'scenarios' in job_data and isinstance(job_data['scenarios'], list):
                options_data = job_data.get('options', {}).copy()
                options_data['_batch_scenarios'] = job_data['scenarios']
                options_data['_batch_metadata'] = {
                    'results_base': job_data.get('results_base'),
                    'scenario_status': job_data.get('scenario_status', {}),
                    'scenario_results': job_data.get('scenario_results', {}),
                    'current_scenario': job_data.get('current_scenario'),
                    'current_scenario_index': job_data.get('current_scenario_index', 0)
                }
                options_json = json.dumps(options_data)
            
            conn.execute("""
                INSERT OR REPLACE INTO jobs
                (job_id, status, dataset, engine, scenario, ui_scenario,
                 created_at, started_at, completed_at, options, result, error, queue_position)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id,
                job_data.get('status'),
                job_data.get('dataset'),
                job_data.get('engine'),
                job_data.get('scenario'),
                job_data.get('ui_scenario'),
                job_data.get('created_at'),
                job_data.get('started_at'),
                job_data.get('completed_at'),
                options_json,
                result_json,
                job_data.get('error'),
                job_data.get('queue_position')
            ))
            conn.commit()

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job from the database"""
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            job = dict(row)
            # Deserialize JSON fields
            job['options'] = json.loads(job['options']) if job['options'] else {}
            job['result'] = json.loads(job['result']) if job['result'] else {}
            
            # Restore batch job fields from options if present
            if '_batch_scenarios' in job['options']:
                job['scenarios'] = job['options'].pop('_batch_scenarios')
                batch_meta = job['options'].pop('_batch_metadata', {})
                job['results_base'] = batch_meta.get('results_base')
                job['scenario_status'] = batch_meta.get('scenario_status', {})
                job['scenario_results'] = batch_meta.get('scenario_results', {})
                job['current_scenario'] = batch_meta.get('current_scenario')
                job['current_scenario_index'] = batch_meta.get('current_scenario_index', 0)
            
            return job

def get_all_jobs(limit: int = 50) -> list:
    """Get all jobs from the database"""
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
            
            jobs = []
            for row in rows:
                job = dict(row)
                # Deserialize JSON fields
                job['options'] = json.loads(job['options']) if job['options'] else {}
                job['result'] = json.loads(job['result']) if job['result'] else {}
                jobs.append(job)
            
            return jobs

def update_job_status(job_id: str, status: str, **kwargs):
    """Update job status and other fields"""
    job = get_job(job_id)
    if job:
        job['status'] = status
        job.update(kwargs)
        save_job(job_id, job)

def get_next_queued_job(engine: str) -> Optional[Dict[str, Any]]:
    """Get the next queued job for the specified engine"""
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM jobs
                WHERE engine = ? AND status = 'queued'
                ORDER BY queue_position ASC, created_at ASC
                LIMIT 1
            """, (engine,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            job = dict(row)
            job['options'] = json.loads(job['options']) if job['options'] else {}
            job['result'] = json.loads(job['result']) if job['result'] else {}
            return job

def get_engine_lock(engine: str, timeout: int = -1):
    """Creates a lock file on the disk.
    All Gunicorn workers/threads looking at this file will respect it.
    
    Args:
        engine: The engine name
        timeout: Lock timeout in seconds. -1 means wait forever (blocking)
    """
    lock_path = f"/workspace/locks/{engine}.lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    return FileLock(lock_path, timeout=timeout)

def is_engine_busy(engine: str) -> bool:
    """Check if the engine lock file is currently locked by ANY worker"""
    lock = get_engine_lock(engine)
    return lock.is_locked

# Thread pool for running benchmarks
MAX_CONCURRENT_JOBS = 3
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS)


def process_engine_queue(engine: str):
    """Process queued jobs for a specific engine (runs in background thread)
    This thread never terminates - it waits for new jobs indefinitely"""
    import time
    job = None
    
    logger.info(f"Queue processor started for engine: {engine}")
    
    while True:
        try:
            # Get next queued job
            job = get_next_queued_job(engine)
            if not job:
                # No queued jobs, wait a bit and check again
                time.sleep(5)
                continue
            
            job_id = job['job_id']
            
            # Check if job was cancelled while queued
            current_job = get_job(job_id)
            if current_job and current_job['status'] == 'cancelled':
                logger.info(f"Job {job_id} was cancelled, skipping")
                continue
            
            dataset = job['dataset']
            options = job['options']
            
            logger.info(f"Job {job_id} waiting for engine lock: {engine}")
            
            # Get a blocking lock (will wait indefinitely until lock is available)
            engine_lock = get_engine_lock(engine, timeout=-1)
            
            # Acquire lock and run job (blocks until lock is available)
            with engine_lock:
                # Double-check job wasn't cancelled while waiting for lock
                current_job = get_job(job_id)
                if current_job and current_job['status'] == 'cancelled':
                    logger.info(f"Job {job_id} was cancelled while waiting for lock, skipping")
                    continue
                
                logger.info(f"Job {job_id} acquired engine lock: {engine}")
                
                # Update job status to running
                update_job_status(job_id, 'running', started_at=datetime.utcnow().isoformat())
                
                # Check if this is a batch job (has 'scenarios' list) or single job (has 'scenario' string)
                if 'scenarios' in job and isinstance(job['scenarios'], list):
                    # Handle batch job - run multiple scenarios sequentially
                    logger.info(f"Processing BATCH job {job_id} with {len(job['scenarios'])} scenarios")
                    process_batch_job(job_id, job, options)
                else:
                    # Handle single scenario job
                    logger.info(f"Processing SINGLE job {job_id}")
                    scenario = job['scenario']
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
                    job_result = get_job(job_id)
                    if job_result:
                        # Store the benchmark result in the 'result' field
                        job_result['result'] = result
                        # Update job status based on benchmark result
                        job_result['status'] = result.get('status', 'completed')
                        job_result['completed_at'] = datetime.utcnow().isoformat()
                        save_job(job_id, job_result)
                    
                    logger.info(f"Job {job_id} completed with status: {result.get('status', 'completed')}")
            
        except Exception as e:
            logger.error(f"Error processing queue for engine {engine}: {e}", exc_info=True)
            # Try to update job status if we have a job_id
            if job and 'job_id' in job:
                try:
                    update_job_status(
                        job['job_id'],
                        'error',
                        error=str(e),
                        completed_at=datetime.utcnow().isoformat()
                    )
                except:
                    pass
            # Don't break - continue processing queue
            time.sleep(5)


def process_batch_job(job_id: str, job: Dict[str, Any], options: Dict[str, Any]):
    """Process a batch job by running multiple scenarios sequentially
    
    Uses scenarios list which contains {label, procedure_name} for each scenario.
    """
    dataset = job['dataset']
    engine = job['engine']
    scenarios = job.get('scenarios', [])
    results_base = job.get('results_base', job_id)
    
    logger.info(f"Starting batch job {job_id}: {len(scenarios)} scenarios")
    
    # Track overall batch results
    batch_results = {
        'scenarios_completed': 0,
        'scenarios_failed': 0,
        'scenario_results': {}
    }
    
    # Run each scenario sequentially
    for idx, scenario in enumerate(scenarios):
        label = scenario['label']
        procedure_name = scenario['procedure_name']
        
        try:
            # Update current scenario in job
            job_data = get_job(job_id)
            if job_data:
                job_data['current_scenario'] = label
                job_data['current_scenario_index'] = idx
                if 'scenario_status' not in job_data:
                    job_data['scenario_status'] = {}
                job_data['scenario_status'][label] = 'running'
                save_job(job_id, job_data)
            
            logger.info(f"Batch job {job_id}: Running scenario {idx+1}/{len(scenarios)}: {label} (procedure: {procedure_name})")
            
            # Create scenario-specific job ID for results directory (use label for directory name)
            scenario_job_id = f"{results_base}/{label}"
            
            # Extract workload params from options
            workload_params = options.get('workload_params', None)
            
            # Run the benchmark for this scenario using the actual procedure name
            result = benchmark_runner.run_benchmark(
                dataset=dataset,
                engine=engine,
                scenario=procedure_name,  # Use actual procedure name
                job_id=scenario_job_id,
                enable_profiling=not options.get('no_profiling', False),
                enable_metrics=not options.get('no_metrics', False),
                workload_params=workload_params
            )
            
            # Store scenario result using label as key
            batch_results['scenario_results'][label] = result
            
            if result.get('status') == 'completed':
                batch_results['scenarios_completed'] += 1
                # Update scenario status
                job_data = get_job(job_id)
                if job_data and 'scenario_status' in job_data:
                    job_data['scenario_status'][label] = 'completed'
                    save_job(job_id, job_data)
                logger.info(f"Batch job {job_id}: Scenario {label} completed successfully")
            else:
                batch_results['scenarios_failed'] += 1
                # Update scenario status
                job_data = get_job(job_id)
                if job_data and 'scenario_status' in job_data:
                    job_data['scenario_status'][label] = 'failed'
                    save_job(job_id, job_data)
                logger.error(f"Batch job {job_id}: Scenario {label} failed")
                
        except Exception as e:
            logger.error(f"Batch job {job_id}: Error running scenario {label}: {e}", exc_info=True)
            batch_results['scenarios_failed'] += 1
            # Update scenario status
            job_data = get_job(job_id)
            if job_data and 'scenario_status' in job_data:
                job_data['scenario_status'][label] = 'error'
                save_job(job_id, job_data)
    
    # Update final batch job status
    job_data = get_job(job_id)
    if job_data:
        job_data['result'] = batch_results
        job_data['current_scenario'] = None
        
        # Determine overall status
        if batch_results['scenarios_failed'] == 0:
            job_data['status'] = 'completed'
        elif batch_results['scenarios_completed'] == 0:
            job_data['status'] = 'failed'
        else:
            job_data['status'] = 'partial'
        
        job_data['completed_at'] = datetime.utcnow().isoformat()
        save_job(job_id, job_data)
    
    logger.info(f"Batch job {job_id} finished: {batch_results['scenarios_completed']} completed, {batch_results['scenarios_failed']} failed")


@app.route('/')
def index():
    """Serve the web UI"""
    return send_from_directory('web', 'index.html')


@app.route('/health')
def health():
    """Health check endpoint"""
    try:
        all_jobs = get_all_jobs(limit=1000)  # Get all jobs for counting
        active_jobs = sum(1 for j in all_jobs if j.get('status') == 'running')
        total_jobs = len(all_jobs)
        
        return jsonify({
            'status': 'healthy',
            'active_jobs': active_jobs,
            'total_jobs': total_jobs
        })
    except Exception as e:
        logger.error(f"Error in health check: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e),
            'active_jobs': 0,
            'total_jobs': 0
        })


@app.route('/api/v1/sync', methods=['POST'])
def sync_config():
    """Pull latest changes from git and reload configuration
    
    This performs:
    1. git pull to get latest config/datasets.yaml and workload params
    2. git pull to get latest workload definitions (workload.py, operations, etc.)
    3. Reload configuration into memory
    """
    try:
        logger.info("Syncing configuration from git...")
        result = config_loader.reload_config(git_pull=True)
        
        # Build user-friendly message
        automation_status = "✓ Synced automation repo" if result['git_pull_success'] else "⚠ Automation repo sync failed"
        workloads_status = "✓ Synced workloads repo" if result['workloads_git_pull_success'] else "⚠ Workloads repo sync failed"
        datasets_status = f"✓ Loaded {result['datasets_count']} datasets: {', '.join(result['datasets'])}"
        
        message = f"{automation_status}\n{workloads_status}\n{datasets_status}"
        
        logger.info(f"Sync complete: {result}")
        
        return jsonify({
            'status': 'success',
            'message': message,
            'git_pull_success': result['git_pull_success'],
            'git_output': result['git_output'],
            'workloads_git_pull_success': result['workloads_git_pull_success'],
            'workloads_git_output': result['workloads_git_output'],
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


@app.route('/api/v1/benchmark/batch', methods=['POST'])
def trigger_batch_benchmark():
    """Trigger a batch benchmark job with multiple scenarios
    
    Accepts scenario labels (e.g., ["vector-search-scenario-1", "vector-search-scenario-2"])
    and maps them to actual procedure names, just like the single benchmark endpoint.
    """
    try:
        request_data = request.get_json() or {}
        
        # Extract parameters
        dataset = request_data.get('dataset')
        engine = request_data.get('engine')
        scenario_labels = request_data.get('scenarios', [])  # List of scenario labels from UI
        
        if not dataset:
            return jsonify({'error': 'dataset parameter is required'}), 400
        if not engine:
            return jsonify({'error': 'engine parameter is required'}), 400
        if not scenario_labels or not isinstance(scenario_labels, list):
            return jsonify({'error': 'scenarios parameter is required and must be a list'}), 400
        
        # Map scenario labels to actual procedure names (same logic as single benchmark endpoint)
        procedures = config_loader.get_test_procedures(dataset)
        scenarios = []  # List of {label, procedure_name}
        
        for scenario_label in scenario_labels:
            procedure_name = None
            proc_counts = {}
            
            # Find matching procedure
            for proc in procedures:
                name = proc.get('name') if isinstance(proc, dict) else proc
                proc_counts[name] = proc_counts.get(name, 0) + 1
                
                # Reconstruct the label for this procedure
                total_count = sum(1 for p in procedures if (p.get('name') if isinstance(p, dict) else p) == name)
                if total_count > 1:
                    label = f"{name}-scenario-{proc_counts[name]}"
                else:
                    label = name
                
                if label == scenario_label:
                    procedure_name = name
                    break
            
            if not procedure_name:
                available = []
                proc_counts = {}
                for proc in procedures:
                    name = proc.get('name') if isinstance(proc, dict) else proc
                    proc_counts[name] = proc_counts.get(name, 0) + 1
                    total_count = sum(1 for p in procedures if (p.get('name') if isinstance(p, dict) else p) == name)
                    label = f"{name}-scenario-{proc_counts[name]}" if total_count > 1 else name
                    available.append(label)
                
                return jsonify({
                    'error': f'Invalid scenario: {scenario_label}',
                    'available_scenarios': available
                }), 400
            
            scenarios.append({
                'label': scenario_label,
                'procedure_name': procedure_name
            })
            
            logger.info(f"Scenario '{scenario_label}' maps to procedure '{procedure_name}'")
        
        # Validate engine with first scenario
        error = benchmark_runner.validate_benchmark_request(dataset, engine, scenarios[0]['procedure_name'])
        if error:
            return jsonify({'error': error}), 400
        
        # Create batch job ID with timestamp (no "batch-" prefix)
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        batch_id = timestamp
        
        # Create results directory structure: timestamp/dataset-engine/
        results_base = f"{timestamp}/{dataset}-{engine}"
        
        # Get current queue position for this engine
        with db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.execute("""
                    SELECT COALESCE(MAX(queue_position), 0) + 1
                    FROM jobs
                    WHERE engine = ? AND status IN ('queued', 'running')
                """, (engine,))
                queue_position = cursor.fetchone()[0]
        
        # Create batch job data
        batch_data = {
            'job_id': batch_id,
            'status': 'queued',
            'dataset': dataset,
            'engine': engine,
            'scenarios': scenarios,  # List of {label, procedure_name}
            'results_base': results_base,
            'created_at': datetime.utcnow().isoformat(),
            'queue_position': queue_position,
            'scenario_status': {s['label']: 'queued' for s in scenarios},
            'scenario_results': {},
            'current_scenario': None,
            'current_scenario_index': 0,
            'options': {
                'no_profiling': request_data.get('no_profiling', False),
                'no_metrics': request_data.get('no_metrics', False),
                'workload_params': request_data.get('workload_params', None)
            }
        }
        
        # Save batch job to database
        save_job(batch_id, batch_data)
        
        # Ensure queue processor is running for this engine
        ensure_processor_running(engine)
        
        logger.info(f"Batch job {batch_id} queued at position {queue_position}: dataset={dataset}, engine={engine}, scenarios={[s['label'] for s in scenarios]}")
        
        return jsonify({
            'job_id': batch_id,
            'status': 'queued',
            'queue_position': queue_position,
            'dataset': dataset,
            'engine': engine,
            'scenarios': [s['label'] for s in scenarios],
            'results_base': results_base,
            'status_url': f'/api/v1/benchmark/{batch_id}'
        }), 202
        
    except Exception as e:
        logger.error(f"Error triggering batch benchmark: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/benchmark', methods=['POST'])
def trigger_benchmark():
    """Trigger a new benchmark job (single scenario)"""
    try:
        request_data = request.get_json() or {}
        
        # Extract parameters
        dataset = request_data.get('dataset')
        engines = request_data.get('engines', 'all')
        ui_scenario = request_data.get('scenarios', 'search')  # This is the UI label
        
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
        
        # Get current queue position for this engine
        with db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.execute("""
                    SELECT COALESCE(MAX(queue_position), 0) + 1
                    FROM jobs
                    WHERE engine = ? AND status IN ('queued', 'running')
                """, (engine,))
                queue_position = cursor.fetchone()[0]
        
        job_data = {
            'job_id': job_id,
            'job_type': 'single',
            'status': 'queued',
            'dataset': dataset,
            'engine': engine,
            'scenario': procedure_name,  # Store actual procedure name
            'ui_scenario': ui_scenario,  # Store UI label for display
            'created_at': datetime.utcnow().isoformat(),
            'queue_position': queue_position,
            'options': {
                'no_profiling': request_data.get('no_profiling', False),
                'no_metrics': request_data.get('no_metrics', False),
                'workload_params': request_data.get('workload_params', None)
            }
        }
        
        # Save job to database
        save_job(job_id, job_data)
        
        # Ensure queue processor is running for this engine
        ensure_processor_running(engine)
        
        logger.info(f"Job {job_id} queued at position {queue_position}: dataset={dataset}, engine={engine}, procedure={procedure_name} (UI: {ui_scenario})")
        
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
    job = get_job(job_id)
    
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    # Calculate progress if applicable
    if job['status'] == 'running':
        # Could add more detailed progress tracking here
        job['progress'] = 'in_progress'
    
    # Extract sweep_results from result and expose as sweeps array
    # This provides the expected API format for the UI
    if 'result' in job and isinstance(job['result'], dict):
        sweep_results = job['result'].get('sweep_results', [])
        job['sweeps'] = sweep_results
    else:
        job['sweeps'] = []
    
    return jsonify(job)


@app.route('/api/v1/benchmark/<job_id>', methods=['DELETE'])
def cancel_job(job_id: str):
    """Cancel a queued or running job"""
    import subprocess as sp
    
    job = get_job(job_id)
    
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    status = job['status']
    
    if status == 'queued':
        # Cancel queued job - just update status
        update_job_status(
            job_id,
            'cancelled',
            completed_at=datetime.utcnow().isoformat(),
            error='Job cancelled by user'
        )
        logger.info(f"Job {job_id} cancelled (was queued)")
        return jsonify({
            'message': 'Job cancelled successfully',
            'job_id': job_id,
            'previous_status': status
        })
    
    elif status == 'running':
        # Gracefully terminate opensearch-benchmark process
        try:
            import time
            
            # Step 1: Send SIGINT (Ctrl+C) for graceful shutdown
            logger.info(f"Job {job_id}: Sending SIGINT to opensearch-benchmark process...")
            result = sp.run(
                ['pkill', '-SIGINT', '-f', 'opensearch-benchmark'],
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                logger.warning(f"Job {job_id}: No opensearch-benchmark process found (pkill returned {result.returncode})")
            
            # Step 2: Wait up to 60 seconds for graceful shutdown
            logger.info(f"Job {job_id}: Waiting up to 60 seconds for graceful shutdown...")
            for i in range(60):
                time.sleep(1)
                # Check if process still exists
                check = sp.run(
                    ['pgrep', '-f', 'opensearch-benchmark'],
                    capture_output=True,
                    text=True
                )
                if check.returncode != 0:
                    # Process has terminated
                    logger.info(f"Job {job_id}: Process terminated gracefully after {i+1} seconds")
                    break
            else:
                # Step 3: If still running after 60s, send SIGTERM
                logger.warning(f"Job {job_id}: Process still running after 60s, sending SIGTERM...")
                sp.run(
                    ['pkill', '-SIGTERM', '-f', 'opensearch-benchmark'],
                    capture_output=True,
                    text=True
                )
                
                # Wait another 10 seconds for SIGTERM
                time.sleep(10)
                check = sp.run(
                    ['pgrep', '-f', 'opensearch-benchmark'],
                    capture_output=True,
                    text=True
                )
                if check.returncode != 0:
                    logger.info(f"Job {job_id}: Process terminated after SIGTERM")
                else:
                    # Step 4: Last resort - SIGKILL
                    logger.error(f"Job {job_id}: Process still running, sending SIGKILL...")
                    sp.run(
                        ['pkill', '-SIGKILL', '-f', 'opensearch-benchmark'],
                        capture_output=True,
                        text=True
                    )
            
            # Update job status
            update_job_status(
                job_id,
                'cancelled',
                completed_at=datetime.utcnow().isoformat(),
                error='Job cancelled by user'
            )
            
            logger.info(f"Job {job_id} cancelled successfully")
            return jsonify({
                'message': 'Job cancelled successfully',
                'job_id': job_id,
                'previous_status': status
            })
        except Exception as e:
            logger.error(f"Error cancelling job {job_id}: {e}")
            # Still mark as cancelled even if kill failed
            update_job_status(
                job_id,
                'cancelled',
                error=f'Job cancellation requested but process termination failed: {e}'
            )
            return jsonify({
                'message': 'Job marked as cancelled but process termination may have failed',
                'job_id': job_id,
                'previous_status': status,
                'error': str(e)
            }), 500
    
    elif status in ['completed', 'error', 'cancelled']:
        return jsonify({
            'error': f'Job already {status}',
            'job_id': job_id,
            'status': status
        }), 400
    
    else:
        return jsonify({
            'error': f'Unknown job status: {status}',
            'job_id': job_id
        }), 400


@app.route('/api/v1/benchmark')
def list_jobs():
    """List all jobs"""
    job_list = get_all_jobs(limit=50)
    
    # Get total count
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM jobs")
            total = cursor.fetchone()[0]
    
    return jsonify({
        'total': total,
        'jobs': job_list
    })


@app.route('/api/v1/benchmark/<job_id>/results')
def get_job_results(job_id: str):
    """Get results for a specific job"""
    job = get_job(job_id)
    
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    if job['status'] not in ['completed', 'error']:
        return jsonify({'error': f'Job is {job["status"]}, no results available yet'}), 400
    
    # Read results from the job's result directory
    results_dir = Path('/results') / job_id
    
    if not results_dir.exists():
        return jsonify({'error': 'Results directory not found'}), 404
    
    # Collect all sweep results - look in scenario subdirectories
    sweeps = []
    
    # Find all sweep directories (could be in subdirectories like vector-search/sweep-1)
    for sweep_dir in sorted(results_dir.glob('**/sweep-*')):
        sweep_data = {
            'sweep_name': sweep_dir.name,
            'test_run': None,
            'workload_params': None,
            'benchmark_log': None
        }
        
        # Read test_run.json
        test_run_file = sweep_dir / 'test_run.json'
        if test_run_file.exists():
            try:
                with open(test_run_file) as f:
                    sweep_data['test_run'] = json.load(f)
            except Exception as e:
                logger.error(f"Error reading test_run.json: {e}")
        
        # Read workload-params.json
        params_file = sweep_dir / 'workload-params.json'
        if params_file.exists():
            try:
                with open(params_file) as f:
                    sweep_data['workload_params'] = json.load(f)
            except Exception as e:
                logger.error(f"Error reading workload-params.json: {e}")
        
        # Read benchmark.log (last 100 lines)
        log_file = sweep_dir / 'benchmark.log'
        if log_file.exists():
            try:
                with open(log_file) as f:
                    lines = f.readlines()
                    sweep_data['benchmark_log'] = ''.join(lines[-100:])
            except Exception as e:
                logger.error(f"Error reading benchmark.log: {e}")
        
        sweeps.append(sweep_data)
    
    return jsonify({
        'job_id': job_id,
        'job': job,
        'sweeps': sweeps
    })


@app.route('/results/<job_id>')
def view_results(job_id: str):
    """Serve the results viewer page"""
    return send_from_directory('web', 'results.html')


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
