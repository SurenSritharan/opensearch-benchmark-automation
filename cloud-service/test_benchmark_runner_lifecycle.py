"""Regression tests for OSB subprocess output and scenario deadlines."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from benchmark_runner import BenchmarkRunner


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


if __name__ == '__main__':
    unittest.main()
