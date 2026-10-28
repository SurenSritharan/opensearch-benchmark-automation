"""Run scale-down checks with a fake kubectl; never contacts a cluster."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name('scale-down-clusters.sh')
FAKE_KUBECTL = '''#!/bin/sh
case "$*" in
  "get namespaces "*)
    if [ "$MOCK_DISCOVERY_FAIL" = 1 ]; then exit 1; fi
    echo os-develop-jvector ;;
  "get namespace "*) echo namespace/os-develop-jvector ;;
  "get statefulset -n "*) echo statefulset.apps/opensearch-data ;;
  "get statefulset opensearch-data "*|"get statefulset opensearch-cluster-manager "*) ;;
  "get pods "*)
    if [ "$MOCK_POD_GET_FAIL" = 1 ]; then exit 1; fi
    if [ "$MOCK_PODS_REMAIN" = 1 ]; then echo pod/opensearch-data-0; fi ;;
  "get pvc "*) ;;
  "scale "*)
    if [ "$MOCK_SCALE_FAIL" = 1 ]; then exit 1; fi ;;
  *) echo "Unexpected kubectl call: $*" >&2; exit 2 ;;
esac
'''


class ScaleDownTests(unittest.TestCase):
    def run_script(self, namespace='os-develop-jvector', **settings):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / 'kubectl'
            fake.write_text(FAKE_KUBECTL)
            fake.chmod(0o755)
            env = os.environ.copy()
            env.update(PATH=f'{directory}:{env["PATH"]}', SCALE_DOWN_TIMEOUT_SECONDS='0',
                       MOCK_PODS_REMAIN='0', MOCK_SCALE_FAIL='0', MOCK_DISCOVERY_FAIL='0',
                       MOCK_POD_GET_FAIL='0')
            env.update(settings)
            return subprocess.run(['bash', str(SCRIPT), namespace],
                                  env=env, capture_output=True, text=True)

    def test_success_only_when_pods_are_gone(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('All pods terminated', result.stdout)

    def test_remaining_pod_is_failure(self):
        result = self.run_script(MOCK_PODS_REMAIN='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Timeout waiting for pods', result.stderr)

    def test_failed_scale_is_failure(self):
        result = self.run_script(MOCK_SCALE_FAIL='1')
        self.assertNotEqual(result.returncode, 0)

    def test_failed_pod_query_is_failure(self):
        result = self.run_script(MOCK_POD_GET_FAIL='1')
        self.assertNotEqual(result.returncode, 0)

    def test_failed_all_namespace_discovery_is_failure(self):
        result = self.run_script(namespace='all', MOCK_DISCOVERY_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Scale Down Complete', result.stdout)


if __name__ == '__main__':
    unittest.main()
