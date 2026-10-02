"""Manage async-profiler capture and flame-graph collection for OpenSearch pods."""
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional, Union

from config_loader import get_os_namespace

logger = logging.getLogger(__name__)


class AsyncProfiler:
    """Own the profiler thread and its start, stop, and artifact collection lifecycle."""

    def __init__(self, temp_dir: Optional[Union[str, Path]] = None):
        self.temp_dir = Path(temp_dir or os.environ.get("TEMP_DIR", "/tmp"))
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self, engine: str, results_dir: Path, duration: int = 60) -> None:
        namespace = get_os_namespace(engine)
        logger.info("🔍 [profiling] Starting async-profiler on %s (duration: up to %ss)", namespace, duration)
        self._stop_event.clear()
        try:
            pods_out = subprocess.run(
                ["kubectl", "get", "pods", "-n", namespace, "-l", "app=opensearch-data",
                 "-o", "jsonpath={.items[*].metadata.name}"],
                capture_output=True, text=True, timeout=15,
            )
            pods = pods_out.stdout.split() if pods_out.returncode == 0 else []
        except Exception as e:
            logger.warning("[profiling] Could not list pods in %s: %s", namespace, e)
            return

        if not pods:
            logger.warning("[profiling] No opensearch-data pods found in %s — skipping", namespace)
            return

        def run_profiler() -> None:
            started = []
            for pod in pods:
                try:
                    result = subprocess.run(
                        ["kubectl", "exec", pod, "-c", "opensearch", "-n", namespace, "--",
                         "/usr/share/opensearch/async-profiler/bin/asprof", "start", "--event", "cpu", "1"],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        logger.info("[profiling] Started on %s", pod)
                        started.append(pod)
                    else:
                        logger.warning("[profiling] asprof start failed on %s: %s", pod, result.stderr.strip())
                except Exception as e:
                    logger.warning("[profiling] Could not start profiler on %s: %s", pod, e)

            if not started:
                logger.warning("[profiling] No pods started — nothing to collect")
                return

            logger.info("[profiling] Running on %d/%d pods in %s for up to %ss",
                        len(started), len(pods), namespace, duration)
            self._stop_event.wait(timeout=duration)
            elapsed = "early" if self._stop_event.is_set() else f"{duration}s elapsed"
            logger.info("[profiling] %s — collecting flame graphs", elapsed)

            profiling_dir = results_dir / "profiling"
            profiling_dir.mkdir(parents=True, exist_ok=True)
            for pod in started:
                remote_path = str(self.temp_dir / f"flamegraph-{pod}.html")
                local_path = profiling_dir / f"{pod}-flamegraph.html"
                try:
                    result = subprocess.run(
                        ["kubectl", "exec", pod, "-c", "opensearch", "-n", namespace, "--",
                         "/usr/share/opensearch/async-profiler/bin/asprof", "stop", "--output",
                         "flamegraph", "--file", remote_path, "1"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.returncode != 0:
                        logger.warning("[profiling] asprof stop failed on %s: %s", pod, result.stderr.strip())
                        continue

                    copied = subprocess.run(
                        ["kubectl", "cp", f"{namespace}/{pod}:{remote_path}", str(local_path),
                         "-c", "opensearch"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if copied.returncode == 0:
                        logger.info("[profiling] Flame graph saved: %s", local_path)
                    else:
                        logger.warning("[profiling] kubectl cp failed for %s: %s", pod, copied.stderr.strip())
                except Exception as e:
                    logger.warning("[profiling] Error collecting from %s: %s", pod, e)

        self.thread = threading.Thread(target=run_profiler, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Wake the profiler to collect early, wait for cleanup, and clear state."""
        if self.thread is None:
            return
        logger.info("🔍 [profiling] Stopping profiler and collecting flame graphs...")
        self._stop_event.set()
        self.thread.join(timeout=200)
        if self.thread.is_alive():
            logger.warning("[profiling] profiler thread did not finish in time — continuing")
        self.thread = None
