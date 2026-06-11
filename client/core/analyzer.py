from client.core.ai_client import AIClient


class TextAnalyzer:
    TEMP_SPELLING_FEEDBACK = "교정 이유가 아직 없습니다."
    DEFAULT_SPELLING_FEEDBACK = "맞춤법, 띄어쓰기, 문장 흐름을 기준으로 확인했습니다."

    def __init__(self):
        self.ai = AIClient()
        self.last_spelling_result = {}
        self.last_spelling_feedback = ""
        self.last_spelling_corrections = []
        self.last_evaluation_feedback = ""
        self.last_tone_feedback = ""

    def analyze_spelling(self, text):
        result = self.ai.correct_spelling(text)
        self.last_spelling_result = result
        self.last_spelling_corrections = self._normalize_corrections(result.get("corrections"))
        self.last_spelling_feedback = self._spelling_feedback_summary(result)
        return self.format_spell_check(result)

    def analyze_summary(self, text):
        result = self.ai.summarize(text)
        return self.format_summary(result)

    def analyze_evaluation(self, text):
        result = self.ai.evaluate(text)
        self.last_evaluation_feedback = result.get("feedback", "")
        score = max(0, min(100, int(result.get("score") or 0)))
        return f"{score}점"

    def analyze_title_recommendation(self, text):
        return self.ai.recommend_title(text)

    def analyze_tone_change(self, text, tone):
        result = self.ai.convert_tone(text, tone)
        self.last_tone_feedback = result.get("feedback", "")
        return str(result.get("converted_text") or "").strip()

    def format_spell_check(self, result):
        if not isinstance(result, dict):
            raise RuntimeError("맞춤법 검사 결과 형식이 올바르지 않습니다.")
        corrected = str(result.get("corrected") or "").strip()
        if not corrected:
            raise RuntimeError("맞춤법 검사 결과에 교정문이 없습니다.")
        corrections = self._normalize_corrections(result.get("corrections"))
        feedback = str(result.get("issues") or "").strip()
        sections = ["맞춤법 검사 결과:", ""]
        if feedback:
            sections.extend(["전체 의견:", feedback, ""])
        if corrections:
            sections.append("교정 정보:")
            for index, item in enumerate(corrections, start=1):
                original = item["original"] or "(원문 없음)"
                suggestion = item["suggestion"] or "(제안 없음)"
                category = item["category"] or "검사"
                explanation = item["explanation"] or "이유 설명이 제공되지 않았습니다."
                confidence = f" / 확신도 {item['confidence']}" if item["confidence"] else ""
                sections.extend(
                    [
                        f"{index}. [{category}{confidence}] {original} -> {suggestion}",
                        f"   이유: {explanation}",
                    ]
                )
            sections.append("")
        else:
            sections.extend(["교정 정보:", "뚜렷한 오류 정보가 발견되지 않았습니다.", ""])
        sections.extend(["교정문:", "", corrected])
        return "\n".join(sections).rstrip()

    def format_summary(self, result):
        summary_text = str(result or "").strip()
        return f"요약 결과:\n\n{summary_text}"

    def _spelling_feedback_summary(self, result):
        corrections = self._normalize_corrections(result.get("corrections") if isinstance(result, dict) else [])
        feedback = str(result.get("issues") or "").strip() if isinstance(result, dict) else ""
        if corrections:
            return f"{len(corrections)}개의 교정 정보가 있습니다. {feedback}".strip()
        return feedback or self.DEFAULT_SPELLING_FEEDBACK

    @staticmethod
    def _normalize_corrections(value):
        if not isinstance(value, list):
            return []
        normalized = []
        for item in value:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "original": str(item.get("original") or "").strip(),
                    "suggestion": str(item.get("suggestion") or "").strip(),
                    "category": str(item.get("category") or "").strip(),
                    "explanation": str(item.get("explanation") or "").strip(),
                    "confidence": str(item.get("confidence") or "").strip(),
                    "id": str(item.get("id") or "").strip(),
                    "severity": str(item.get("severity") or "").strip(),
                    "anchor_text": str(item.get("anchor_text") or "").strip(),
                    "display_title": str(item.get("display_title") or "").strip(),
                    "source_start": item.get("source_start"),
                    "source_end": item.get("source_end"),
                }
            )
        return normalized
