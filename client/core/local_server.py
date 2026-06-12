import os
import subprocess
import sys
import threading
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
        if getattr(sys, "frozen", False):
            self._start_embedded_server(server_dir)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self._is_running():
                    return
                time.sleep(0.15)
            raise RuntimeError("로그인 서버를 시작하지 못했습니다.")

        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self._load_server_env(env, server_dir)
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

    def _start_embedded_server(self, server_dir):
        if getattr(self, "_embedded_thread", None) and self._embedded_thread.is_alive():
            return
        env = os.environ
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self._load_server_env(env, server_dir)

        def run_server():
            try:
                import importlib.util
                import uvicorn

                server_path = server_dir / "main.py"
                if str(server_dir) not in sys.path:
                    sys.path.insert(0, str(server_dir))
                spec = importlib.util.spec_from_file_location("writing_assistant_embedded_server", server_path)
                if spec is None or spec.loader is None:
                    return
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                uvicorn.run(module.app, host="127.0.0.1", port=8765, log_level="warning")
            except Exception:
                return

        self._embedded_thread = threading.Thread(target=run_server, name="WritingAssistantServer", daemon=True)
        self._embedded_thread.start()

    def _is_running(self):
        try:
            response = requests.get(f"{self.base_url}/", timeout=0.5)
            return response.status_code < 500
        except Exception:
            return False

    def _load_server_env(self, env, server_dir):
        project_dir = server_dir.parent
        for env_path in (
            server_dir / ".env",
            project_dir / ".env",
            project_dir / "WA_yunseo" / "server" / ".env",
        ):
            if not env_path.exists():
                continue
            for key, value in self._read_env_file(env_path).items():
                env.setdefault(key, value)

    def _read_env_file(self, env_path):
        values = {}
        try:
            lines = env_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return values
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
        return values
