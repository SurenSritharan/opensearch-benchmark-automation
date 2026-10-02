"""OpenSearch node-statistics fallback collector for non-Kubernetes runs."""
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def diff_node_stats(before: Dict, after: Dict) -> Dict:
    """Diff two node-stat snapshots for the server-stats artifact."""
    result = {}
    for node_id, after_node in after.get("nodes", {}).items():
        before_node = before.get("nodes", {}).get(node_id, {})
        name = after_node.get("name", node_id)

        def delta(path: list):
            a, b = after_node, before_node
            for key in path:
                a = a.get(key, {}) if isinstance(a, dict) else {}
                b = b.get(key, {}) if isinstance(b, dict) else {}
            return (a - b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else a

        result[name] = {
            "jvm": {
                "heap_used_percent": after_node.get("jvm", {}).get("mem", {}).get("heap_used_percent"),
                "heap_used_mb": round(after_node.get("jvm", {}).get("mem", {}).get("heap_used_in_bytes", 0) / 1048576, 1),
                "uptime_ms": after_node.get("jvm", {}).get("uptime_in_millis"),
                "gc_young_count_delta": delta(["jvm", "gc", "collectors", "young", "collection_count"]),
                "gc_young_time_ms_delta": delta(["jvm", "gc", "collectors", "young", "collection_time_in_millis"]),
                "gc_old_count_delta": delta(["jvm", "gc", "collectors", "old", "collection_count"]),
                "gc_old_time_ms_delta": delta(["jvm", "gc", "collectors", "old", "collection_time_in_millis"]),
            },
            "os": {
                "cpu_percent": after_node.get("os", {}).get("cpu", {}).get("percent"),
                "load_1m": after_node.get("os", {}).get("cpu", {}).get("load_average", {}).get("1m"),
                "mem_used_percent": after_node.get("os", {}).get("mem", {}).get("used_percent"),
            },
            "indices": {
                "search_query_count_delta": delta(["indices", "search", "query_total"]),
                "search_query_time_ms_delta": delta(["indices", "search", "query_time_in_millis"]),
                "search_fetch_count_delta": delta(["indices", "search", "fetch_total"]),
                "indexing_count_delta": delta(["indices", "indexing", "index_total"]),
                "indexing_time_ms_delta": delta(["indices", "indexing", "index_time_in_millis"]),
            },
            "thread_pool": {
                "search_queue": after_node.get("thread_pool", {}).get("search", {}).get("queue"),
                "search_rejected": delta(["thread_pool", "search", "rejected"]),
                "write_queue": after_node.get("thread_pool", {}).get("write", {}).get("queue"),
                "write_rejected": delta(["thread_pool", "write", "rejected"]),
            },
        }
    return result


class NodeStatsPoller:
    """Poll OpenSearch node stats and write the dashboard-compatible metrics schema."""

    def __init__(self, target_host: str, results_dir: Path,
                 fetch_node_stats: Callable, interval: int = 10):
        self._target_host = target_host
        self._results_dir = Path(results_dir)
        self._fetch_node_stats = fetch_node_stats
        self._interval = interval
        self._stop = threading.Event()
        self._cpu_samples: Dict[str, list] = defaultdict(list)
        self._mem_samples: Dict[str, list] = defaultdict(list)
        self._gc_samples: list = []
        self._gc_last: Dict[str, Dict] = {}
        self.total_samples = 0
        self.request_attempts = 0
        self.failed_samples = 0
        self.last_error = None
        self.enabled = True
        self._start_iso = ""
        self._end_iso = ""

    def start_collection(self, scenario_name: str, interval: int = 10,
                         duration: Optional[int] = None) -> None:
        self._interval = interval
        self._stop.clear()
        self._start_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "")
        start_time = time.time()
        end_epoch = start_time + duration if duration else float("inf")

        logger.info("NodeStatsPoller: starting collection for %s", scenario_name)
        while not self._stop.is_set() and time.time() < end_epoch:
            loop_start = time.time()
            self._collect_sample()
            sleep_time = max(0.0, interval - (time.time() - loop_start))
            if duration and (time.time() + sleep_time) >= end_epoch:
                break
            self._stop.wait(sleep_time)

        self._end_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "")

    def stop_collection(self) -> None:
        self._stop.set()

    def save_metrics(self, scenario_name: str) -> None:
        if not self.total_samples:
            logger.info("NodeStatsPoller: no samples collected, skipping k8s_metrics.json")
            return

        nodes: Dict[str, Any] = {}
        for name in set(self._cpu_samples) | set(self._mem_samples):
            cpu_list = self._cpu_samples.get(name, [])
            mem_list = self._mem_samples.get(name, [])
            nodes[name] = {
                "node_pool": "local",
                "cpu_avg": round(sum(cpu_list) / len(cpu_list), 3) if cpu_list else 0.0,
                "cpu_max": round(max(cpu_list), 3) if cpu_list else 0.0,
                "cpu_min": round(min(cpu_list), 3) if cpu_list else 0.0,
                "memory_avg": round(sum(mem_list) / len(mem_list), 1) if mem_list else 0.0,
                "memory_max": round(max(mem_list), 1) if mem_list else 0.0,
                "memory_min": round(min(mem_list), 1) if mem_list else 0.0,
            }

        payload = {
            "scenario": scenario_name,
            "namespace": "local",
            "start_time": self._start_iso,
            "end_time": self._end_iso,
            "duration_seconds": None,
            "summary": {
                "nodes": nodes,
                "pods": {},
                "total_samples": self.total_samples,
                "gc_timeline": self._gc_samples,
            },
        }

        self._results_dir.mkdir(parents=True, exist_ok=True)
        out = self._results_dir / "k8s_metrics.json"
        out.write_text(json.dumps(payload, indent=2))
        logger.info("NodeStatsPoller: saved %s (%d samples)", out, self.total_samples)

    def reset(self) -> None:
        self.total_samples = 0
        self.request_attempts = 0
        self.failed_samples = 0
        self.last_error = None
        self._cpu_samples.clear()
        self._mem_samples.clear()
        self._gc_samples.clear()
        self._gc_last.clear()
        self._stop.clear()

    def _collect_sample(self) -> None:
        self.request_attempts += 1
        snap = self._fetch_node_stats(
            self._target_host, error_callback=self._record_error
        )
        if not snap:
            return
        nodes = snap.get("nodes", {})
        if not nodes:
            self._record_error("/_nodes/stats response contained no nodes")
            return

        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.total_samples += 1
        for node_data in nodes.values():
            name = node_data.get("name", "unknown")
            cpu = node_data.get("os", {}).get("cpu", {}).get("percent")
            mem_bytes = node_data.get("os", {}).get("mem", {}).get("used_in_bytes")
            if cpu is not None:
                self._cpu_samples[name].append(float(cpu))
            if mem_bytes is not None:
                self._mem_samples[name].append(mem_bytes / 1024 / 1024)
            self._collect_gc_sample(name, node_data, ts)

    def _record_error(self, message: str) -> None:
        self.failed_samples += 1
        self.last_error = message

    def _collect_gc_sample(self, name: str, node_data: Dict, ts: str) -> None:
        jvm = node_data.get("jvm", {})
        mem = jvm.get("mem", {})
        collectors = jvm.get("gc", {}).get("collectors", {})
        young = collectors.get("young", {})
        old = collectors.get("old", {})
        curr = {
            "young_count": young.get("collection_count", 0),
            "young_ms": young.get("collection_time_in_millis", 0),
            "old_count": old.get("collection_count", 0),
            "old_ms": old.get("collection_time_in_millis", 0),
        }
        prev = self._gc_last.get(name, curr)
        self._gc_samples.append({
            "ts": ts,
            "node": name,
            "heap_used_pct": mem.get("heap_used_percent"),
            "heap_used_mb": round(mem.get("heap_used_in_bytes", 0) / 1048576, 1),
            "young_count_delta": curr["young_count"] - prev["young_count"],
            "young_ms_delta": curr["young_ms"] - prev["young_ms"],
            "old_count_delta": curr["old_count"] - prev["old_count"],
            "old_ms_delta": curr["old_ms"] - prev["old_ms"],
        })
        self._gc_last[name] = curr
