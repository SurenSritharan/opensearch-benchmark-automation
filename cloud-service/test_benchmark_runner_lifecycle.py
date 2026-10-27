"""Regression tests for OSB subprocess output and scenario deadlines."""

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark_runner import BenchmarkRunner, _kill_proc_group, _scenario_timeout_seconds, _sweep_status


class TestBenchmarkProcessLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_run_dir = os.environ.get('RUN_DIR')
        os.environ['RUN_DIR'] = str(self.root / 'run')
        self.runner = BenchmarkRunner.__new__(BenchmarkRunner)
        self.runner.benchmark_home = self.root
        self.runner.datasets_root = self.root
        self.runner._active_lock = threading.Lock()
        self.runner._active = {}
        self.runner._append_heap_dump_fn = None
        self.runner._get_job_fn = None
        self.runner._save_job_fn = None

    def tearDown(self):
        if self.old_run_dir is None:
            os.environ.pop('RUN_DIR', None)
        else:
            os.environ['RUN_DIR'] = self.old_run_dir
        self.temp.cleanup()

    def run_child(self, code, name, timeout):
        cmd = [sys.executable, '-c', code]
        result_dir = self.root / name
        proc = self.runner._launch_process(cmd, os.environ.copy(), name, None, result_dir)
        return self.runner._poll_until_done(
            proc, cmd, name, name, 'unused', None,
            results_dir=result_dir, timeout_seconds=timeout,
        )

    def test_large_output_cannot_block_child(self):
        result = self.run_child(
            'import sys; sys.stdout.write("A"*200000); sys.stderr.write("B"*200000)',
            'large-output', 10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 200000)
        self.assertEqual(len(result.stderr), 200000)
        self.assertEqual((self.root / 'large-output' / 'osb-stdout.log').stat().st_mode & 0o777, 0o600)

    def test_deadline_returns_failure_with_reason(self):
        result = self.run_child('import time; time.sleep(10)', 'deadline', 0.1)
        self.assertEqual(result.returncode, -1)
        self.assertIn('Exceeded scenario deadline', result.stderr)

    def test_kill_proc_group_reaches_both_kinds_of_grandchild(self):
        for detached in (False, True):
            with self.subTest(detached=detached):
                pid_file = self.root / f'grandchild-{detached}.pid'
                heartbeat = self.root / f'grandchild-{detached}.heartbeat'
                grandchild_code = (
                    'import pathlib, time; '
                    f'p = pathlib.Path({str(heartbeat)!r}); '
                    '[(p.write_text(str(time.time())), time.sleep(0.02)) for _ in range(1500)]'
                )
                child_code = (
                    'import os, subprocess, sys, time; '
                    f'p = subprocess.Popen([sys.executable, "-c", {grandchild_code!r}], '
                    f'start_new_session={detached}); '
                    f'open({str(pid_file)!r}, "w").write(str(p.pid)); '
                    'time.sleep(30)'
                )
                proc = subprocess.Popen([sys.executable, '-c', child_code], start_new_session=True)
                grandchild_pid = None
                try:
                    for _ in range(100):
                        if pid_file.exists() and heartbeat.exists():
                            grandchild_pid = int(pid_file.read_text())
                            break
                        time.sleep(0.02)
                    self.assertIsNotNone(grandchild_pid)
                    # The local macOS sandbox forbids process enumeration. Mock
                    # only discovery, then kill the real process tree.
                    with patch('benchmark_runner.psutil.Process') as process:
                        process.return_value.children.return_value = [Mock(pid=grandchild_pid)]
                        _kill_proc_group(proc)
                        process.return_value.children.assert_called_once_with(recursive=True)
                    first = heartbeat.stat().st_mtime_ns
                    time.sleep(0.2)
                    self.assertEqual(first, heartbeat.stat().st_mtime_ns)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                    if grandchild_pid:
                        try:
                            os.kill(grandchild_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    def test_deadline_only_applies_to_pure_search(self):
        params = {'warmup_time_period': 60, 'time_period': 300}
        for scenario in ('vector-search', 'bm25-search', 'hybrid-search', 'openrag-hybrid-search'):
            self.assertEqual(_scenario_timeout_seconds(scenario, params), 960)
        self.assertEqual(_scenario_timeout_seconds('bulk-ingest-and-search', params), 86400)

    def test_direct_cancel_sets_event_and_reports_cancelled(self):
        event = threading.Event()
        name = 'direct-cancel'
        cmd = [sys.executable, '-c', 'import time; time.sleep(30)']
        result_dir = self.root / name
        proc = self.runner._launch_process(cmd, os.environ.copy(), name, event, result_dir)
        self.assertFalse(event.is_set())
        self.runner.cancel(name)
        self.assertTrue(event.is_set())
        result = self.runner._poll_until_done(
            proc, cmd, name, name, 'unused', event,
            results_dir=result_dir, timeout_seconds=10,
        )
        self.assertEqual(result.returncode, -9)
        self.assertTrue(event.is_set())
        self.assertEqual(_sweep_status(result.returncode, event), 'cancelled')
        self.assertEqual(_sweep_status(-9, threading.Event()), 'failed')


if __name__ == '__main__':
    unittest.main()
