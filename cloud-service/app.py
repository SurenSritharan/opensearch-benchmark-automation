#!/usr/bin/env python3
"""Cloud-native OpenSearch Benchmark REST API Service"""
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from collections import deque
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
    """Process a batch job by running multiple dataset+scenario combinations sequentially
    
    Uses scenarios list which contains {dataset, label, procedure_name, params} for each test.
    """
    engine = job['engine']
    scenarios = job.get('scenarios', [])
    results_base = job.get('results_base', job_id)
    
    logger.info(f"Starting batch job {job_id}: {len(scenarios)} tests")
    
    # Track overall batch results
    batch_results = {
        'scenarios_completed': 0,
        'scenarios_failed': 0,
        'scenario_results': {}
    }
    
    # Run each test sequentially
    for idx, scenario in enumerate(scenarios):
        dataset = scenario['dataset']
        label = scenario['label']
        procedure_name = scenario['procedure_name']
        
        try:
            # Create unique path for each test: results_base/dataset-label
            # This prevents tests from overwriting each other
            scenario_key = f"{dataset}-{label}"
            scenario_job_id = f"{results_base}/{scenario_key}"
            
            # Update current scenario in job
            job_data = get_job(job_id)
            if job_data:
                job_data['current_scenario'] = scenario_key
                job_data['current_scenario_index'] = idx
                if 'scenario_status' not in job_data:
                    job_data['scenario_status'] = {}
                job_data['scenario_status'][scenario_key] = 'running'
                save_job(job_id, job_data)
            
            logger.info(f"Batch job {job_id}: Running test {idx+1}/{len(scenarios)}: dataset={dataset}, scenario={label} (procedure: {procedure_name})")
            
            # Merge global params with scenario-specific params
            # Scenario params override global params
            global_params = options.get('workload_params', {}) or {}
            scenario_params = scenario.get('params', {}) or {}
            workload_params = {**global_params, **scenario_params}
            
            logger.info(f"Batch job {job_id}, test {label}: merged params = {workload_params}")
            
            # Run the benchmark for this test using the actual procedure name
            result = benchmark_runner.run_benchmark(
                dataset=dataset,
                engine=engine,
                scenario=procedure_name,  # Use actual procedure name
                job_id=scenario_job_id,
                enable_profiling=not options.get('no_profiling', False),
                enable_metrics=not options.get('no_metrics', False),
                workload_params=workload_params if workload_params else None
            )
            
            # Store scenario result using dataset-label as key for better identification
            result['dataset'] = dataset
            result['scenario_label'] = label
            # Store relative path for get_job_results to find sweep directories
            result['results_subdir'] = scenario_key
            batch_results['scenario_results'][scenario_key] = result
            
            if result.get('status') == 'completed':
                batch_results['scenarios_completed'] += 1
                # Update scenario status
                job_data = get_job(job_id)
                if job_data and 'scenario_status' in job_data:
                    job_data['scenario_status'][scenario_key] = 'completed'
                    save_job(job_id, job_data)
                logger.info(f"Batch job {job_id}: Scenario {scenario_key} completed successfully")
            else:
                batch_results['scenarios_failed'] += 1
                # Update scenario status
                job_data = get_job(job_id)
                if job_data and 'scenario_status' in job_data:
                    job_data['scenario_status'][scenario_key] = 'failed'
                    save_job(job_id, job_data)
                logger.error(f"Batch job {job_id}: Scenario {scenario_key} failed")
                
        except Exception as e:
            scenario_key = f"{dataset}-{label}"
            logger.error(f"Batch job {job_id}: Error running scenario {scenario_key}: {e}", exc_info=True)
            batch_results['scenarios_failed'] += 1
            # Update scenario status
            job_data = get_job(job_id)
            if job_data and 'scenario_status' in job_data:
                job_data['scenario_status'][scenario_key] = 'error'
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
            
            # Validate: check for duplicate procedure names without aliases
            name_counts = {}
            for proc in procedures:
                name = proc.get('name') if isinstance(proc, dict) else proc
                name_counts[name] = name_counts.get(name, 0) + 1
            
            # Build procedure metadata with UI labels and actual procedure names
            procedures_metadata = []
            
            for idx, proc in enumerate(procedures):
                name = proc.get('name') if isinstance(proc, dict) else proc
                alias = proc.get('alias') if isinstance(proc, dict) else None
                
                # Validate: if multiple procedures with same name, alias is required
                if name_counts[name] > 1 and not alias:
                    error_msg = f"Dataset '{dataset_name}': Multiple procedures with name '{name}' found but no alias defined. Please add 'alias' field to distinguish them."
                    logger.error(error_msg)
                    return jsonify({'error': error_msg}), 500
                
                # Use alias if available, otherwise use name directly
                ui_label = alias if alias else name
                
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
    """Trigger a batch benchmark job with multiple dataset+scenario combinations for one engine
    
    Request format:
    {
        "engine": "jvector",
        "tests": [
            {"dataset": "msmarco", "scenario": "vector-search-k100-ef100"},
            {"dataset": "cohere-1m", "scenario": "search-only-k10"}
        ]
    }
    """
    try:
        request_data = request.get_json() or {}
        
        # Extract parameters
        engine = request_data.get('engine')
        tests = request_data.get('tests', [])
        
        if not engine:
            return jsonify({'error': 'engine parameter is required'}), 400
        if not tests or not isinstance(tests, list):
            return jsonify({'error': 'tests parameter is required and must be a list'}), 400
        
        # Validate each test has dataset and scenario
        for idx, test in enumerate(tests):
            if not isinstance(test, dict):
                return jsonify({'error': f'Test {idx+1} must be an object with dataset and scenario'}), 400
            if 'dataset' not in test or 'scenario' not in test:
                return jsonify({'error': f'Test {idx+1} must have both dataset and scenario fields'}), 400
        
        # Process each test and build scenarios list
        scenarios = []  # List of {dataset, label, procedure_name, params}
        
        for test in tests:
            dataset = test['dataset']
            scenario_label = test['scenario']
            
            # Get procedures for this dataset
            procedures = config_loader.get_test_procedures(dataset)
            
            # Validate: check for duplicate procedure names without aliases
            name_counts = {}
            for proc in procedures:
                name = proc.get('name') if isinstance(proc, dict) else proc
                name_counts[name] = name_counts.get(name, 0) + 1
            
            procedure_name = None
            matched_proc = None
            
            # Find matching procedure using alias or name
            for proc in procedures:
                name = proc.get('name') if isinstance(proc, dict) else proc
                alias = proc.get('alias') if isinstance(proc, dict) else None
                
                # Validate: if multiple procedures with same name, alias is required
                if name_counts[name] > 1 and not alias:
                    error_msg = f"Dataset '{dataset}': Multiple procedures with name '{name}' found but no alias defined. Please add 'alias' field to distinguish them."
                    logger.error(error_msg)
                    return jsonify({'error': error_msg}), 500
                
                # Use alias if available, otherwise use name directly
                label = alias if alias else name
                
                if label == scenario_label:
                    procedure_name = name
                    matched_proc = proc
                    break
            
            if not procedure_name:
                # Build list of available scenarios for error message
                available = []
                for proc in procedures:
                    name = proc.get('name') if isinstance(proc, dict) else proc
                    alias = proc.get('alias') if isinstance(proc, dict) else None
                    label = alias if alias else name
                    available.append(label)
                
                return jsonify({
                    'error': f'Invalid scenario "{scenario_label}" for dataset "{dataset}"',
                    'available_scenarios': available
                }), 400
            
            # Validate engine is available for this dataset
            error = benchmark_runner.validate_benchmark_request(dataset, engine, procedure_name)
            if error:
                return jsonify({'error': f'Dataset "{dataset}": {error}'}), 400
            
            # Extract scenario-specific params
            scenario_params = {}
            if matched_proc and isinstance(matched_proc, dict):
                scenario_params = matched_proc.get('params', {})
            
            scenarios.append({
                'dataset': dataset,
                'label': scenario_label,
                'procedure_name': procedure_name,
                'params': scenario_params
            })
            
            logger.info(f"Test: dataset='{dataset}', scenario='{scenario_label}' -> procedure '{procedure_name}'")
        
        # Create batch job ID with timestamp (no "batch-" prefix)
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        batch_id = timestamp
        
        # Create results directory structure: timestamp/engine/ (no dataset since we have multiple)
        results_base = f"{timestamp}/{engine}"
        
        # Get current queue position for this engine
        with db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.execute("""
                    SELECT COALESCE(MAX(queue_position), 0) + 1
                    FROM jobs
                    WHERE engine = ? AND status IN ('queued', 'running')
                """, (engine,))
                queue_position = cursor.fetchone()[0]
        
        # Get unique datasets for summary
        unique_datasets = list(set(s['dataset'] for s in scenarios))
        datasets_summary = ', '.join(unique_datasets) if len(unique_datasets) <= 3 else f"{len(unique_datasets)} datasets"
        
        # Create batch job data
        batch_data = {
            'job_id': batch_id,
            'status': 'queued',
            'dataset': 'multi',  # Indicate multiple datasets
            'datasets': unique_datasets,  # List of all datasets used
            'engine': engine,
            'scenario': 'batch',  # Set to 'batch' to avoid null issues
            'ui_scenario': f"{len(scenarios)} tests across {datasets_summary}",  # Summary for display
            'scenarios': scenarios,  # List of {dataset, label, procedure_name, params}
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
        
        logger.info(f"Batch job {batch_id} queued at position {queue_position}: engine={engine}, tests={len(scenarios)}, datasets={unique_datasets}")
        
        return jsonify({
            'job_id': batch_id,
            'status': 'queued',
            'queue_position': queue_position,
            'datasets': unique_datasets,
            'engine': engine,
            'tests': [{'dataset': s['dataset'], 'scenario': s['label']} for s in scenarios],
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
        
        # Look up the actual procedure name from UI label (support aliases)
        procedures = config_loader.get_test_procedures(dataset)
        
        # Validate: check for duplicate procedure names without aliases
        name_counts = {}
        for proc in procedures:
            name = proc.get('name') if isinstance(proc, dict) else proc
            name_counts[name] = name_counts.get(name, 0) + 1
        
        procedure_name = None
        
        for proc in procedures:
            name = proc.get('name') if isinstance(proc, dict) else proc
            alias = proc.get('alias') if isinstance(proc, dict) else None
            
            # Validate: if multiple procedures with same name, alias is required
            if name_counts[name] > 1 and not alias:
                error_msg = f"Dataset '{dataset}': Multiple procedures with name '{name}' found but no alias defined. Please add 'alias' field to distinguish them."
                logger.error(error_msg)
                return jsonify({'error': error_msg}), 500
            
            # Use alias if available, otherwise use name directly
            ui_label = alias if alias else name
            
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


@app.route('/api/v1/benchmark/<job_id>/cancel', methods=['POST'])
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


@app.route('/api/v1/benchmark/<job_id>', methods=['DELETE'])
def delete_job(job_id: str):
    """
    Delete a job from the database and optionally clean up its results.
    
    Query parameters:
    - cleanup_results: If 'true', also delete the results directory (default: false)
    """
    try:
        job = get_job(job_id)
        if not job:
            return jsonify({'error': 'Job not found'}), 404
        
        status = job['status']
        
        # Don't allow deletion of running jobs
        if status == 'running':
            return jsonify({
                'error': 'Cannot delete a running job. Cancel it first.',
                'job_id': job_id,
                'status': status
            }), 400
        
        # Check if we should clean up results
        cleanup_results = request.args.get('cleanup_results', 'false').lower() == 'true'
        results_deleted = False
        
        if cleanup_results:
            # Determine results directory
            results_dir = None
            if job.get('options', {}).get('_batch_metadata', {}).get('results_base'):
                # Batch job
                results_dir = Path(job['options']['_batch_metadata']['results_base'])
            elif job.get('result', {}).get('results_dir'):
                # Single job
                results_dir = Path(job['result']['results_dir'])
            
            # Delete results directory if it exists
            if results_dir and results_dir.exists():
                import shutil
                try:
                    shutil.rmtree(results_dir)
                    results_deleted = True
                    logger.info(f"Deleted results directory for job {job_id}: {results_dir}")
                except Exception as e:
                    logger.error(f"Failed to delete results directory {results_dir}: {e}")
        
        # Delete job from database
        with db_lock:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                conn.commit()
        
        logger.info(f"Deleted job {job_id} from database")
        
        return jsonify({
            'message': 'Job deleted successfully',
            'job_id': job_id,
            'previous_status': status,
            'results_deleted': results_deleted
        })
        
    except Exception as e:
        logger.error(f"Error deleting job {job_id}: {e}")
        return jsonify({
            'error': f'Failed to delete job: {str(e)}',
            'job_id': job_id
        }), 500


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


def _parse_sweep_directory(
    sweep_dir: Path, 
    sweep_name: str, 
    scenario_label: Optional[str] = None, 
    dataset: Optional[str] = None
) -> dict:
    """Helper function to cleanly read and parse artifacts for a single sweep directory."""
    sweep_data = {
        'sweep_name': sweep_name,
        'test_run': None,
        'workload_params': None,
        'benchmark_log': None,
        'scenario_label': scenario_label,
        'dataset': dataset
    }
    
    if not sweep_dir.exists():
        return sweep_data

    # 1. Safely load JSON configuration files
    for file_name, key in [('test_run.json', 'test_run'), ('workload-params.json', 'workload_params')]:
        target_file = sweep_dir / file_name
        if target_file.exists():
            try:
                sweep_data[key] = json.loads(target_file.read_text())
            except Exception as e:
                logger.error(f"Error reading {file_name} in {sweep_dir}: {e}")

    # 2. Read benchmark.log safely (Memory-efficient tailing using a deque)
    log_file = sweep_dir / 'benchmark.log'
    if log_file.exists():
        try:
            # Using open() as a context manager streams the file line-by-line
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                # A deque with maxlen keeps at most 100 items in memory at a time
                last_lines = deque(f, maxlen=100)
                
            sweep_data['benchmark_log'] = ''.join(last_lines)
        except Exception as e:
            logger.error(f"Error reading benchmark.log in {sweep_dir}: {e}")

    return sweep_data


@app.route('/api/v1/benchmark/<job_id>/results')
def get_job_results(job_id: str):
    """Get results for a specific job by driving file resolution from job metadata."""
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    #  FIXED: Safe .get() to prevent KeyError if status is missing
    if job.get('status') not in ['completed', 'error']:
        return jsonify({'error': f'Job is {job.get("status", "unknown")}, no results available yet'}), 400
    
    results_dir = Path('/results') / job_id
    if not results_dir.exists():
        return jsonify({'error': 'Results directory not found'}), 404

    sweeps = []
    scenario_results = job.get('result', {}).get('scenario_results')
    
    if scenario_results:
        # --- Batch Job Path ---
        for scenario_key, s_data in scenario_results.items():
            subdir = s_data.get('results_subdir', '')
            dataset = s_data.get('dataset', '')
            label = s_data.get('scenario_label', scenario_key)
            
            for sweep in s_data.get('sweep_results', []):
                sweep_name = sweep.get('sweep_name')
                if sweep_name:
                    sweep_path = results_dir / subdir / sweep_name
                    sweeps.append(_parse_sweep_directory(sweep_path, sweep_name, label, dataset))
    else:
        # --- Single Job Path ---
        dataset = job.get('result', {}).get('dataset') or job.get('dataset')
        label = job.get('result', {}).get('scenario_label') or job.get('ui_scenario') or job.get('scenario')
        
        #  OPTIMIZED: Sorted by folder name explicitly to prevent nested path sorting anomalies
        sorted_dirs = sorted(results_dir.glob('**/sweep-*'), key=lambda p: p.name)
        for sweep_dir in sorted_dirs:
            sweeps.append(_parse_sweep_directory(sweep_dir, sweep_dir.name, label, dataset))

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

