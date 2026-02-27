from collections import defaultdict
from threading import Lock


class InMemoryMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = defaultdict(float)

    def inc(self, key: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[key] += value

    def set_gauge(self, key: str, value: float) -> None:
        with self._lock:
            self._gauges[key] = value

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for key, value in sorted(self._counters.items()):
                lines.append(f"# TYPE {key} counter")
                lines.append(f"{key} {value}")
            for key, value in sorted(self._gauges.items()):
                lines.append(f"# TYPE {key} gauge")
                lines.append(f"{key} {value}")
        return "\n".join(lines) + "\n"


metrics = InMemoryMetrics()
