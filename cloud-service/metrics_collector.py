"""Select and manage the background metrics collector for one benchmark sweep."""
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from event_tracer import RunTrace, trace_event
from k8s_metrics_collector import K8sMetricsCollector
from node_stats_poller import NodeStatsPoller
from config_loader import get_os_namespace

logger = logging.getLogger(__name__)

# Worst-case time for the background thread to finish one full collection cycle and
# exit its loop when the stop signal could not be delivered:
#   K8sMetricsCollector: up to 3×5s (k8s API) + 5s (JVM stats) + 10s sleep + same again ≈ 50s
#   NodeStatsPoller:     up to 15s (_nodes/stats) + 10s sleep + 15s again               ≈ 40s
# 60s covers both with a small margin; only used in the anomalous signal-failure path.
_STOP_SIGNAL_TIMEOUT = 60


class MetricsCollector:
    """Own collector selection, polling thread lifecycle, and metrics trace events."""

    def __init__(self, engine: str, target_host: str, results_dir: Path,
                 fetch_node_stats: Callable):
        self.engine = engine
        self.target_host = target_host
        self.results_dir = Path(results_dir)
        self.fetch_node_stats = fetch_node_stats
        self.backend = None
        self.thread: Optional[threading.Thread] = None
        self._scenario_name = ""
        self._trace_context = {}

    def start(self, dataset: str, scenario: str, sweep: int, interval: int = 10) -> None:
        """Create the appropriate backend and start its polling loop in a thread."""
        scenario_name = f"{dataset}-{scenario}-sweep{sweep}"
        use_k8s_metrics = os.environ.get("ENABLE_K8S_METRICS", "true").lower() == "true"
        self._trace_context = {
            "dataset": dataset,
            "scenario": scenario,
            "sweep": sweep,
            "component": "metrics",
        }
        trace_event(self.results_dir, "metrics_initializing", dataset=dataset,
                    scenario=scenario, sweep=sweep, enabled=use_k8s_metrics)

        try:
            if use_k8s_metrics:
                namespace = get_os_namespace(self.engine)
                logger.info("Initializing Kubernetes metrics collection for namespace %s", namespace)
                self.backend = K8sMetricsCollector(
                    namespace=namespace,
                    results_dir=self.results_dir,
                    enabled=True,
                    opensearch_host=self.target_host,
                )
                backend_name = "k8s_metrics_collector"
            else:
                logger.info("Kubernetes metrics disabled; using OpenSearch node-stats polling")
                self.backend = NodeStatsPoller(
                    target_host=self.target_host,
                    results_dir=self.results_dir,
                    fetch_node_stats=self.fetch_node_stats,
                )
                backend_name = "node_stats_poller"
        except Exception as e:
            logger.warning("Failed to initialize metrics collector: %s", e)
            trace_event(self.results_dir, "metrics_initialization_failed", error=str(e))
            self.backend = None
            return

        self._scenario_name = scenario_name
        backend = self.backend  # close over the instance, not self, so the thread
                                # is unaffected if stop() clears self.backend later

        def collect() -> None:
            with RunTrace(self.results_dir, **self._trace_context) as trace:
                try:
                    with trace.span("metrics_collection", collector=backend_name):
                        backend.start_collection(
                            scenario_name=scenario_name,
                            interval=interval,
                            duration=None,
                        )
                        backend.save_metrics(scenario_name)
                    trace.event(
                        "metrics_collection_finished",
                        collector=backend_name,
                        samples=getattr(backend, "total_samples", None),
                        request_attempts=getattr(backend, "request_attempts", None),
                        failed_samples=getattr(backend, "failed_samples", None),
                        last_error=getattr(backend, "last_error", None),
                        file_written=(self.results_dir / "k8s_metrics.json").exists(),
                    )
                except Exception as e:
                    logger.exception("Metrics collection failed")
                    trace.event("metrics_collection_failed", collector=backend_name, error=str(e))

        self.thread = threading.Thread(target=collect, daemon=True)
        self.thread.start()
        trace_event(self.results_dir, "metrics_collection_started", collector=backend_name)
        logger.info("Metrics collection started in background")

    def stop(self) -> bool:
        """Stop the polling loop and wait for the thread to finish.

        Two cases:
        - Signal succeeds: the polling loop will exit at its next sleep boundary,
          so join() unconditionally — the thread *will* finish.
        - Signal raises: the loop is still running; use a bounded join so we do
          not block forever.  If the thread is still alive after the timeout we
          leave self.thread and self.backend intact so the next call can retry,
          and log a warning rather than silently losing the reference.
        """
        if self.thread is None:
            return True
        if self.thread.ident is None:
            # start() may have failed before the worker actually launched.
            self.thread = None
            self.backend = None
            return True
        if self.backend is None:
            self.thread.join(timeout=_STOP_SIGNAL_TIMEOUT)
            if self.thread.is_alive():
                return False
            self.thread = None
            return True

        logger.info("Stopping metrics collection")
        signal_ok = False
        try:
            self.backend.stop_collection()
            signal_ok = True
        except Exception as e:
            logger.error("Error signalling metrics collector to stop: %s", e)
            trace_event(self.results_dir, "metrics_stop_signal_failed", error=str(e))

        if signal_ok:
            self.thread.join()
            self.thread = None
            self.backend = None
            return True
        else:
            self.thread.join(timeout=_STOP_SIGNAL_TIMEOUT)
            if self.thread.is_alive():
                logger.warning(
                    "Metrics thread still alive %ss after failed stop signal — "
                    "leaving references intact for retry", _STOP_SIGNAL_TIMEOUT,
                )
                return False
            else:
                self.thread = None
                self.backend = None
                return True
