"""Structured, per-run event traces for benchmark orchestration."""
import contextlib
import contextvars
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()
_CURRENT_TRACE = contextvars.ContextVar("automation_run_trace", default=None)


class RunTrace:
    """Thread-safe JSON Lines trace owned by a job or sweep."""

    def __init__(self, results_dir: Path, **context: Any):
        self.path = Path(results_dir) / "automation.log"
        self.context = context
        self._token = None

    def __enter__(self):
        self._token = _CURRENT_TRACE.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            _CURRENT_TRACE.reset(self._token)
            self._token = None
        return False

    def event(self, event: str, **details: Any) -> None:
        """Append one event. Trace I/O errors must never fail a benchmark."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **self.context,
                "event": event,
                **details,
            }
            with _WRITE_LOCK:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, default=str, sort_keys=True) + "\n")
        except Exception as e:
            logger.warning("Could not append automation trace in %s: %s", self.path.parent, e)

    @contextlib.contextmanager
    def span(self, name: str, **details: Any):
        """Record a step's start, duration, and success or failure."""
        started = time.monotonic()
        self.event("step_started", step=name, **details)
        try:
            yield
        except Exception as e:
            self.event("step_failed", step=name,
                       duration_seconds=round(time.monotonic() - started, 3),
                       error=str(e), **details)
            raise
        else:
            self.event("step_finished", step=name,
                       duration_seconds=round(time.monotonic() - started, 3), **details)


class _TraceLogHandler(logging.Handler):
    """Copy warnings/errors in an active run scope into its automation trace."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__ and record.getMessage().startswith("Could not append automation trace"):
            return
        trace = _CURRENT_TRACE.get()
        if trace is not None:
            trace.event("log", logger=record.name, level=record.levelname,
                        message=record.getMessage())


_HANDLER = _TraceLogHandler()
_HANDLER.setLevel(logging.WARNING)
logging.getLogger().addHandler(_HANDLER)


def trace_event(results_dir: Path, event: str, **details: Any) -> None:
    """Write to the active run trace, or to a standalone trace at results_dir."""
    path = Path(results_dir)
    trace = _CURRENT_TRACE.get()
    if trace is not None and trace.path.parent == path:
        trace.event(event, **details)
    else:
        RunTrace(path).event(event, **details)
