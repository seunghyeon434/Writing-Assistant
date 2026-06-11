import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import requests


_LOG_DIR = Path(__file__).resolve().parents[2] / ".logs"
_CLIENT_AI_LOG_PATH = _LOG_DIR / "client_ai_events.jsonl"


class AIClient:
    def __init__(self, base_url="http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")
        self._ensure_log_file()

    def correct_spelling(self, text):
        data = self._post("/correct-public", {"text": text})
        corrected = data.get("corrected_text")
        if not corrected:
            raise RuntimeError("맞춤법 검사 응답에 교정문이 없습니다.")
        return {
            "issues": data.get("spelling_feedback") or "",
            "corrected": corrected,
            "corrections": data.get("corrections") or [],
        }

    def summarize(self, text):
        data = self._post("/summary-public", {"text": text})
        if not data.get("summary"):
            raise RuntimeError("요약 응답이 비어 있습니다.")
        return data["summary"]

    def evaluate(self, text):
        data = self._post("/evaluation-public", {"text": text})
        return {
            "score": int(data.get("score") or 0),
            "feedback": data.get("feedback") or "",
        }

    def recommend_title(self, text):
        data = self._post("/title-public", {"text": text})
        if not data.get("title"):
            raise RuntimeError("제목 추천 응답이 비어 있습니다.")
        return data["title"]

    def convert_tone(self, text, tone):
        data = self._post("/tone-public", {"text": text, "tone": tone or ""})
        if not data.get("converted_text"):
            raise RuntimeError("문체 변환 응답이 비어 있습니다.")
        return {
            "converted_text": data["converted_text"],
            "feedback": data.get("feedback") or "",
        }

    def request(self, prompt):
        return self.correct_spelling(prompt)

    def _post(self, path, payload):
        started_at = time.monotonic()
        text = str((payload or {}).get("text") or "")
        self._log_event(
            "client_ai_request_started",
            path=path,
            text_len=len(text),
            text_hash=self._text_hash(text),
        )
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=90,
            )
            data = self._handle_response(response)
            self._log_event(
                "client_ai_request_completed",
                path=path,
                status_code=response.status_code,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                text_len=len(text),
                text_hash=self._text_hash(text),
            )
            return data
        except Exception as exc:
            self._log_event(
                "client_ai_request_failed",
                path=path,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_type=type(exc).__name__,
                error=str(exc)[:240],
                text_len=len(text),
                text_hash=self._text_hash(text),
            )
            raise

    @staticmethod
    def _handle_response(response):
        try:
            data = response.json()
        except Exception:
            data = {"detail": response.text}
        if response.status_code >= 400:
            raise RuntimeError(data.get("detail", "AI request failed."))
        return data

    def _ensure_log_file(self):
        try:
            _CLIENT_AI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _CLIENT_AI_LOG_PATH.touch(exist_ok=True)
        except Exception:
            pass

    def _log_event(self, event, **fields):
        payload = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            **fields,
        }
        try:
            _CLIENT_AI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _CLIENT_AI_LOG_PATH.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def _text_hash(text):
        if not text:
            return ""
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
