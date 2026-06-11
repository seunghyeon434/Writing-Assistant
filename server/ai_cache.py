import json
import threading
from collections import OrderedDict
from pathlib import Path


class AICache:
    def __init__(self, path: Path, max_entries: int = 300):
        self.path = path
        self.max_entries = max(0, int(max_entries or 0))
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._loaded = False

    def get(self, key: str) -> dict | None:
        if self.max_entries <= 0:
            return None
        with self._lock:
            self._load()
            value = self._entries.get(key)
            if value is None:
                return None
            self._entries.move_to_end(key)
            return dict(value)

    def set(self, key: str, value: dict):
        if self.max_entries <= 0 or not isinstance(value, dict):
            return
        with self._lock:
            self._load()
            self._entries[key] = dict(value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            self._save()

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, dict):
                self._entries[key] = value
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
