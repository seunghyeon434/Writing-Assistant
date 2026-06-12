import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

try:
    from ai_cache import AICache
except ImportError:
    from server.ai_cache import AICache


def _load_ai_env_files():
    server_dir = Path(__file__).resolve().parent
    project_dir = server_dir.parent
    for env_path in (
        server_dir / ".env",
        project_dir / ".env",
        project_dir / "WA_yunseo" / "server" / ".env",
    ):
        if env_path.exists():
            load_dotenv(env_path, override=False)


_load_ai_env_files()


class AIService:
    DEFAULT_MODELS = {
        "correction": "gpt-5-mini",
        "summary": "gpt-5-nano",
        "evaluation": "gpt-5-nano",
        "title": "gpt-5-nano",
        "tone": "gpt-5-nano",
    }

    PROMPT_VERSION = "2026-06-writing-assistant-ai-v4-context-markers"

    FEATURE_SPECS = {
        "correction": {
            "json_keys": ("corrected_text", "feedback", "corrections"),
            "instructions": (
                "You are a Korean writing tutor and editor. Correct spelling, spacing, grammar, "
                "punctuation, awkward wording, and words or expressions that do not fit the "
                "surrounding context while preserving meaning, paragraph order, and blank lines. "
                "Also identify each likely issue as a learning aid, including why a contextual "
                "word choice is inappropriate when that is the problem. When a sentence is an "
                "exchange, greeting, farewell, thanks, apology, or other ordinary social context, "
                "replace an emotionally contradictory or context-breaking word with a natural "
                "alternative instead of preserving the wrong word literally. For example, after "
                "a greeting or farewell, a negative response such as 싫어해요 should be treated "
                "as a likely contextual issue when the surrounding text implies a positive or "
                "neutral social response. Do not fix a contextual issue by simply deleting the "
                "problematic word. Replace the exact problematic word or short phrase with a "
                "clear non-empty alternative, and make corrections.original the smallest exact "
                "span that needs correction whenever possible. "
                "Return only valid JSON with keys corrected_text, feedback, and corrections. "
                "corrections must be an array of objects with original, suggestion, category, "
                "explanation, and confidence. Use concise Korean explanations. Use category "
                "values such as 맞춤법, 띄어쓰기, 문법, 문장부호, 표현, or 문맥. If there are no "
                "clear issues, return an empty corrections array."
            ),
            "task": (
                "다음 글을 교정하고, 오류 또는 오류 가능성이 있는 부분별로 교정안과 이유를 알려 주세요. "
                "맞춤법, 띄어쓰기, 문법, 문장부호뿐 아니라 앞뒤 문맥에 어울리지 않는 단어와 표현도 "
                "문맥에 맞게 수정하고 그 이유를 설명해 주세요. 특히 인사, 작별, 감사, 사과처럼 일반적인 "
                "대화 흐름에서 감정이나 응답 방향이 명백히 어긋나는 단어는 원문 그대로 보존하지 말고 "
                "문맥상 자연스러운 단어로 교정하세요. 예를 들어 인사나 작별의 긍정적/중립적 흐름에서 "
                "싫어해요처럼 부정 감정이 갑자기 나오면 문맥 오류로 보고 더 자연스러운 표현으로 바꾸세요. "
                "문맥 오류를 고칠 때 문제가 되는 단어를 삭제만 하지 말고, 반드시 비어 있지 않은 대체 단어로 "
                "바꾸세요. corrections.original에는 가능하면 문제가 되는 최소 단어 또는 짧은 구절만 넣으세요. "
                "의미를 함부로 바꾸지 말고 JSON 객체만 반환하세요."
            ),
        },
        "summary": {
            "json_keys": ("summary",),
            "instructions": (
                "You are a Korean writing assistant. Summarize the user's text in Korean. "
                "Preserve the writer's intent, avoid adding facts, and keep the result concise. "
                "Do not copy the source verbatim unless it is already a very short sentence. "
                "Return only valid JSON with key summary."
            ),
            "task": "다음 글의 핵심 내용을 2~4문장으로 요약해 주세요. JSON 객체만 반환하세요.",
        },
        "evaluation": {
            "json_keys": ("score", "feedback"),
            "instructions": (
                "You are a Korean writing coach. Evaluate clarity, coherence, grammar, "
                "readability, and persuasiveness. Return only valid JSON with keys score "
                "and feedback. score must be an integer from 0 to 100. feedback must be "
                "short, practical Korean advice."
            ),
            "task": "다음 글을 평가해 주세요. JSON 객체만 반환하세요.",
        },
        "title": {
            "json_keys": ("title",),
            "instructions": (
                "You are a Korean editor. Recommend one concise title for the user's text. "
                "Return only valid JSON with key title. The title should be natural, "
                "specific, and no longer than 40 Korean characters unless necessary."
            ),
            "task": "다음 글에 어울리는 제목 하나를 추천해 주세요. JSON 객체만 반환하세요.",
        },
        "tone": {
            "json_keys": ("converted_text", "feedback"),
            "instructions": (
                "You are a Korean rewriting assistant. Rewrite the user's text into the "
                "requested tone or style while preserving meaning, facts, paragraph order, "
                "and blank lines. Return only valid JSON with keys converted_text and feedback."
            ),
            "task": "다음 글을 요청한 문체/말투로 변환해 주세요. JSON 객체만 반환하세요.",
        },
    }

    def __init__(self):
        self._client = None
        cache_path = Path(__file__).resolve().parents[1] / ".logs" / "ai_response_cache.json"
        self.event_log_path = Path(__file__).resolve().parents[1] / ".logs" / "ai_events.jsonl"
        self.cache = AICache(cache_path, max_entries=self._env_int("OPENAI_CACHE_MAX_ENTRIES", 300))
        self._ensure_event_log_file()

    @property
    def client(self):
        if self._client is None:
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key or api_key.startswith("replace-"):
                raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def model_for(self, feature: str) -> str:
        env_name = f"OPENAI_{feature.upper()}_MODEL"
        return (
            os.getenv(env_name)
            or os.getenv("OPENAI_MODEL")
            or self.DEFAULT_MODELS.get(feature)
            or "gpt-5-nano"
        ).strip()

    def correct_text(self, text: str) -> dict[str, object]:
        source_text = self._require_text(text)
        data = self._run_json_feature("correction", source_text)
        if self._has_context_deletion(data, source_text):
            self._log_ai_event(
                "ai_context_deletion_retry",
                feature="correction",
                **self._text_ref(source_text),
            )
            retry_data = self._run_json_feature("correction", source_text, {"strict_context_replacement": True})
            if retry_data:
                data = retry_data
        corrected_text = str(data.get("corrected_text") or "").strip()
        if not corrected_text:
            raise RuntimeError("OpenAI correction response did not include corrected_text.")
        return {
            "corrected_text": corrected_text,
            "feedback": str(data.get("feedback") or "").strip(),
            "corrections": self._normalize_corrections(data.get("corrections"), source_text),
        }

    def summarize_text(self, text: str, style: str = "brief") -> dict[str, str]:
        data = self._run_json_feature("summary", self._require_text(text), {"summary_style": style or "brief"})
        summary = str(data.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("OpenAI summary response did not include summary.")
        return {"summary": summary}

    def evaluate_text(self, text: str) -> dict[str, object]:
        data = self._run_json_feature("evaluation", self._require_text(text))
        feedback = str(data.get("feedback") or "").strip()
        if not feedback:
            raise RuntimeError("OpenAI evaluation response did not include feedback.")
        return {"score": self._clamp_score(data.get("score")), "feedback": feedback}

    def recommend_title(self, text: str) -> dict[str, str]:
        data = self._run_json_feature("title", self._require_text(text))
        title = str(data.get("title") or "").strip().strip("\"' \n\t")
        if not title:
            raise RuntimeError("OpenAI title response did not include title.")
        return {"title": title}

    def convert_tone(self, text: str, tone: str = "") -> dict[str, str]:
        source_text = self._require_text(text)
        requested_tone = str(tone or "").strip() or "자연스럽고 읽기 쉬운 문체"
        data = self._run_json_feature("tone", source_text, {"tone": requested_tone})
        converted_text = str(data.get("converted_text") or "").strip()
        if not converted_text:
            raise RuntimeError("OpenAI tone response did not include converted_text.")
        return {
            "converted_text": converted_text,
            "feedback": str(data.get("feedback") or "").strip(),
        }

    def _run_json_feature(self, feature: str, source_text: str, extra: dict | None = None) -> dict:
        spec = self.FEATURE_SPECS[feature]
        model = self.model_for(feature)
        input_text = self._build_input(feature, spec, source_text, extra or {})
        cache_key = self._cache_key(feature, model, input_text)
        started_at = time.monotonic()
        self._log_ai_event(
            "ai_request_started",
            feature=feature,
            model=model,
            **self._text_ref(source_text),
        )

        if self._cache_enabled():
            cached = self.cache.get(cache_key)
            if cached is not None:
                self._log_ai_event(
                    "ai_cache_hit",
                    feature=feature,
                    model=model,
                    duration_ms=int((time.monotonic() - started_at) * 1000),
                    **self._text_ref(source_text),
                )
                return cached

        response = self._create_json_response(feature, spec, model, input_text, source_text)
        output_text = self._extract_response_text(response)
        self._raise_for_empty_or_incomplete_response(feature, response, output_text)
        data = self._parse_json_object(output_text)
        if not data:
            for candidate in self._candidate_response_texts(response):
                if candidate == output_text:
                    continue
                data = self._parse_json_object(candidate)
                if data:
                    break
        if not data:
            self._log_ai_event(
                "ai_json_parse_failed",
                feature=feature,
                model=model,
                output_len=len(output_text),
                output_preview=output_text[:160],
                **self._text_ref(source_text),
            )
            raise RuntimeError(f"OpenAI {feature} response was not valid JSON.")

        if self._cache_enabled():
            self.cache.set(cache_key, data)
        self._log_ai_event(
            "ai_request_completed",
            feature=feature,
            model=model,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            output_len=len(output_text),
            response_status=str(getattr(response, "status", "") or ""),
            **self._text_ref(source_text),
        )
        return data

    def _build_input(self, feature: str, spec: dict, source_text: str, extra: dict) -> str:
        trimmed_text = self._trim_input(source_text)
        lines = [spec["task"]]
        if feature == "correction" and extra.get("strict_context_replacement"):
            lines.append(
                "중요: 이전 응답처럼 문맥에 맞지 않는 단어를 삭제만 하면 안 됩니다. "
                "문맥 문제가 있으면 문제가 되는 최소 단어를 original로 잡고, suggestion에는 실제 대체 단어를 넣으세요. "
                "예: 싫어해요 -> 반가워요 또는 반갑습니다."
            )
        if feature == "tone":
            lines.append(f"요청 문체/말투: {extra.get('tone') or '자연스럽게'}")
        if feature == "summary":
            lines.append(self._summary_style_instruction(extra.get("summary_style")))
        lines.extend(["", "원문:", trimmed_text])
        return "\n".join(lines)

    def _summary_style_instruction(self, style: str) -> str:
        value = str(style or "brief").strip()
        if value == "bullet":
            return "요약 방식: 핵심 bullet. 핵심 내용을 3~5개의 짧은 bullet로 정리하세요."
        if value == "detailed":
            return "요약 방식: 자세히. 중요한 맥락과 흐름을 보존하며 5~8문장 정도로 자세히 요약하세요."
        return "요약 방식: 짧게. 핵심만 1~3문장으로 간결하게 요약하세요."

    def _json_schema_format(self, feature: str, spec: dict) -> dict:
        properties = {}
        for key in spec["json_keys"]:
            if key == "score":
                properties[key] = {"type": "integer"}
            elif key == "corrections":
                properties[key] = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "original": {"type": "string"},
                            "suggestion": {"type": "string"},
                            "category": {"type": "string"},
                            "explanation": {"type": "string"},
                            "confidence": {"type": "string"},
                        },
                        "required": ["original", "suggestion", "category", "explanation", "confidence"],
                        "additionalProperties": False,
                    },
                }
            else:
                properties[key] = {"type": "string"}
        return {
            "type": "json_schema",
            "name": f"writing_assistant_{feature}",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(spec["json_keys"]),
                "additionalProperties": False,
            },
        }

    def _create_json_response(self, feature: str, spec: dict, model: str, input_text: str, source_text: str):
        params = {
            "model": model,
            "instructions": spec["instructions"],
            "input": input_text,
            "max_output_tokens": self._max_output_tokens(feature),
            "reasoning": {"effort": "minimal"},
            "text": {"format": self._json_schema_format(feature, spec), "verbosity": "low"},
        }
        try:
            return self.client.responses.create(**params)
        except Exception as exc:
            self._log_ai_event(
                "ai_json_schema_request_failed",
                feature=feature,
                model=model,
                error_type=type(exc).__name__,
                error=str(exc)[:240],
                **self._text_ref(source_text),
            )
            params["text"] = {"format": {"type": "json_object"}, "verbosity": "low"}
            return self.client.responses.create(**params)

    def _extract_response_text(self, response) -> str:
        candidates = self._candidate_response_texts(response)
        return candidates[0] if candidates else ""

    def _candidate_response_texts(self, response) -> list[str]:
        candidates = []
        output_text = str(getattr(response, "output_text", "") or "")
        if output_text:
            candidates.append(output_text.strip())
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                parsed = getattr(content, "parsed", None)
                if parsed:
                    try:
                        candidates.append(json.dumps(parsed, ensure_ascii=False))
                    except Exception:
                        pass
                text = getattr(content, "text", None)
                if text:
                    candidates.append(str(text).strip())
        unique = []
        seen = set()
        for candidate in candidates:
            value = str(candidate or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            unique.append(value)
        return unique

    def _raise_for_empty_or_incomplete_response(self, feature: str, response, output_text: str):
        status = str(getattr(response, "status", "") or "")
        incomplete = getattr(response, "incomplete_details", None)
        error = getattr(response, "error", None)
        reason = str(getattr(incomplete, "reason", "") or "")
        error_message = str(getattr(error, "message", "") or "")
        if status == "incomplete" or reason:
            self._log_ai_event("ai_response_incomplete", feature=feature, status=status, reason=reason, output_len=len(output_text))
            raise RuntimeError(f"OpenAI response was incomplete: {reason or status}")
        if error_message:
            self._log_ai_event("ai_response_error", feature=feature, status=status, error=error_message[:240])
            raise RuntimeError(f"OpenAI response error: {error_message}")
        if not output_text:
            self._log_ai_event("ai_response_empty", feature=feature, status=status, output_items=len(getattr(response, "output", []) or []))
            raise RuntimeError("OpenAI returned an empty response.")

    def _normalize_corrections(self, value, source_text: str) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        corrections = []
        cursor = 0
        for item in value:
            if not isinstance(item, dict):
                continue
            original = str(item.get("original") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip()
            explanation = str(item.get("explanation") or "").strip()
            if not (original or suggestion or explanation):
                continue
            start, end = self._find_correction_span(source_text, original, cursor)
            if start is not None and end is not None:
                cursor = end
            category = str(item.get("category") or "").strip()
            confidence = str(item.get("confidence") or "").strip()
            corrections.append(
                {
                    "id": f"spell-{len(corrections) + 1:02d}",
                    "original": original,
                    "suggestion": suggestion,
                    "category": category,
                    "explanation": explanation,
                    "confidence": confidence,
                    "severity": self._correction_severity(category, confidence),
                    "source_start": start,
                    "source_end": end,
                    "anchor_text": original or suggestion,
                    "display_title": self._correction_display_title(category, original, suggestion),
                }
            )
        return self._filter_overlapping_corrections(corrections)

    def _has_context_deletion(self, data: dict, source_text: str) -> bool:
        if not isinstance(data, dict):
            return False
        corrected_text = str(data.get("corrected_text") or "")
        if not corrected_text:
            return False
        raw_corrections = [item for item in (data.get("corrections") or []) if isinstance(item, dict)]
        for item in raw_corrections:
            if not isinstance(item, dict):
                continue
            original = str(item.get("original") or "")
            suggestion = str(item.get("suggestion") or "")
            original_tokens = self._korean_word_tokens(original)
            suggestion_tokens = self._korean_word_tokens(suggestion)
            if len(original_tokens) <= 1:
                continue
            if len(suggestion_tokens) >= len(original_tokens):
                continue
            removed_tokens = [token for token in original_tokens if token not in suggestion_tokens]
            if not removed_tokens:
                continue
            if self._removed_tokens_have_explicit_replacements(removed_tokens, raw_corrections, corrected_text):
                continue
            if any(token in str(source_text or "") and token not in corrected_text for token in removed_tokens):
                return True
        return False

    def _filter_overlapping_corrections(self, corrections: list[dict[str, object]]) -> list[dict[str, object]]:
        if not corrections:
            return []
        filtered = []
        for item in corrections:
            if self._is_redundant_broad_deletion(item, corrections):
                self._log_ai_event(
                    "ai_correction_filtered_broad_deletion",
                    original=str(item.get("original") or "")[:80],
                    suggestion=str(item.get("suggestion") or "")[:80],
                    category=str(item.get("category") or "")[:40],
                )
                continue
            filtered.append(item)
        return filtered

    def _is_redundant_broad_deletion(self, item: dict, corrections: list[dict[str, object]]) -> bool:
        original = str(item.get("original") or "")
        suggestion = str(item.get("suggestion") or "")
        original_tokens = self._korean_word_tokens(original)
        suggestion_tokens = self._korean_word_tokens(suggestion)
        if len(original_tokens) <= 1 or len(suggestion_tokens) >= len(original_tokens):
            return False
        removed_tokens = [token for token in original_tokens if token not in suggestion_tokens]
        if not removed_tokens:
            return False
        for token in removed_tokens:
            for other in corrections:
                if other is item:
                    continue
                other_original = str(other.get("original") or "")
                other_suggestion = str(other.get("suggestion") or "")
                if other_original == token and other_suggestion and other_suggestion != token:
                    return True
        return False

    def _removed_tokens_have_explicit_replacements(self, removed_tokens: list[str], corrections: list[dict], corrected_text: str) -> bool:
        for token in removed_tokens:
            for other in corrections:
                other_original = str(other.get("original") or "")
                other_suggestion = str(other.get("suggestion") or "")
                if other_original != token or not other_suggestion or other_suggestion == token:
                    continue
                if other_suggestion in corrected_text:
                    return True
        return False

    @staticmethod
    def _korean_word_tokens(text: str) -> list[str]:
        return re.findall(r"[가-힣A-Za-z0-9]+", str(text or ""))

    def _find_correction_span(self, source_text: str, original: str, start_at: int) -> tuple[int | None, int | None]:
        source = str(source_text or "")
        needle = str(original or "").strip()
        if not source or not needle:
            return None, None
        start = source.find(needle, max(0, start_at))
        if start < 0:
            start = source.find(needle)
        if start < 0:
            return None, None
        return start, start + len(needle)

    def _correction_severity(self, category: str, confidence: str) -> str:
        text = f"{category} {confidence}".lower()
        if any(token in text for token in ("possible", "검토", "제안", "낮음")):
            return "info"
        if any(token in text for token in ("확실", "오류", "맞춤법", "띄어쓰기", "문법")):
            return "warn"
        return "neutral"

    def _correction_display_title(self, category: str, original: str, suggestion: str) -> str:
        label = category or "교정"
        if original and suggestion:
            return f"{label}: {original} -> {suggestion}"
        return label

    def _trim_input(self, text: str) -> str:
        value = str(text or "")
        max_chars = self._env_int("OPENAI_MAX_INPUT_CHARS", 6000)
        if max_chars <= 0 or len(value) <= max_chars:
            return value
        return value[:max_chars].rstrip() + "\n\n[입력이 길어 앞부분만 분석했습니다.]"

    def _cache_key(self, feature: str, model: str, input_text: str) -> str:
        payload = {"version": self.PROMPT_VERSION, "feature": feature, "model": model, "input": input_text}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _text_ref(self, text: str) -> dict:
        value = str(text or "")
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        return {"text_len": len(value), "text_lines": value.count("\n") + (1 if value else 0), "text_hash": digest[:16]}

    def _log_ai_event(self, event: str, **fields):
        payload = {"ts": datetime.now().isoformat(timespec="milliseconds"), "event": event, **fields}
        try:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _ensure_event_log_file(self):
        try:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
            self.event_log_path.touch(exist_ok=True)
        except Exception:
            pass

    def _cache_enabled(self) -> bool:
        return os.getenv("OPENAI_CACHE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}

    def _require_text(self, text: str) -> str:
        source_text = str(text or "")
        if not source_text.strip():
            raise RuntimeError("Text is required.")
        return source_text

    def _env_int(self, name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except Exception:
            return default

    def _max_output_tokens(self, feature: str) -> int:
        feature_name = str(feature or "").upper()
        defaults = {
            "CORRECTION": 1800,
            "TONE": 1600,
            "SUMMARY": 900,
            "EVALUATION": 900,
            "TITLE": 300,
        }
        feature_default = defaults.get(feature_name, 900)
        return self._env_int(
            f"OPENAI_{feature_name}_MAX_OUTPUT_TOKENS",
            self._env_int("OPENAI_MAX_OUTPUT_TOKENS", feature_default),
        )

    def _clamp_score(self, value) -> int:
        try:
            score = int(value)
        except Exception:
            score = 0
        return max(0, min(100, score))

    def _parse_json_object(self, text: str) -> dict:
        raw = str(text or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
        try:
            data, _end = json.JSONDecoder().raw_decode(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                return data if isinstance(data, dict) else {}
            except Exception:
                try:
                    data, _end = json.JSONDecoder().raw_decode(raw[start:])
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}
        return {}
