from collections import defaultdict
from threading import Lock


class InMemoryMetrics:
    """轻量内存指标。

    支持 unlabeled metric(老路径,`inc("name") / set_gauge("name", v)`)和
    labeled metric(`inc_labeled("name", {k: v}) / set_gauge_labeled(...)`)。
    `render_prometheus()` 按 metric name 合并 `# TYPE`,labeled 系列在同一
    name 下展开多行,符合 Prometheus exposition spec。
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = defaultdict(float)
        # labeled:name → {tuple(sorted label items)) → value}
        self._labeled_counters: dict[str, dict[tuple, float]] = defaultdict(dict)
        self._labeled_gauges: dict[str, dict[tuple, float]] = defaultdict(dict)

    def inc(self, key: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[key] += value

    def set_gauge(self, key: str, value: float) -> None:
        with self._lock:
            self._gauges[key] = value

    def inc_labeled(
        self, name: str, labels: dict[str, str], value: float = 1.0
    ) -> None:
        """按 name + labels 累加 counter。labels 进 series key,name 共享 TYPE。"""
        label_key = self._normalize_labels(labels)
        with self._lock:
            self._labeled_counters[name].setdefault(label_key, 0.0)
            self._labeled_counters[name][label_key] += value

    def set_gauge_labeled(
        self, name: str, labels: dict[str, str], value: float
    ) -> None:
        label_key = self._normalize_labels(labels)
        with self._lock:
            self._labeled_gauges[name][label_key] = value

    @staticmethod
    def _normalize_labels(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((k, str(v)) for k, v in labels.items()))

    @staticmethod
    def _format_labels(label_key: tuple[tuple[str, str], ...]) -> str:
        if not label_key:
            return ""
        parts = ",".join(f'{k}="{v}"' for k, v in label_key)
        return "{" + parts + "}"

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, value in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")
            for name, value in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {value}")
            for name in sorted(self._labeled_counters):
                lines.append(f"# TYPE {name} counter")
                for label_key, value in sorted(self._labeled_counters[name].items()):
                    lines.append(f"{name}{self._format_labels(label_key)} {value}")
            for name in sorted(self._labeled_gauges):
                lines.append(f"# TYPE {name} gauge")
                for label_key, value in sorted(self._labeled_gauges[name].items()):
                    lines.append(f"{name}{self._format_labels(label_key)} {value}")
        return "\n".join(lines) + "\n"


metrics = InMemoryMetrics()
