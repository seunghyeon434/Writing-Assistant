import os
import subprocess
import sys
import time
from pathlib import Path

import requests


class LocalServer:
    def __init__(self, base_url="http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")
        self.process = None

    def ensure_running(self, timeout=8.0):
        if self._is_running():
            return

        server_dir = Path(__file__).resolve().parents[2] / "server"
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8765"],
            cwd=str(server_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_running():
                return
            time.sleep(0.15)
        raise RuntimeError("로그인 서버를 시작하지 못했습니다.")

    def stop(self):
        if self.process is None:
            return
        try:
            self.process.terminate()
        except Exception:
            pass
        self.process = None

    def _is_running(self):
        try:
            response = requests.get(f"{self.base_url}/", timeout=0.5)
            return response.status_code < 500
        except Exception:
            return False
