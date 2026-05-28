from client.core.ai_client import AIClient


class TextAnalyzer:
    TEMP_SPELLING_FEEDBACK = "수정됨"
    TEMP_RESULT_MARKERS = {
        "spelling": " [교정 기능 사용 됨]",
        "summary": " [요약 기능 사용 됨]",
        "tone": " [문체 기능 사용 됨]",
    }

    def __init__(self):
        self.ai = AIClient()

    def analyze_spelling(self, text):
        result = self.check_spelling(text)
        return self.format_spell_check(result)

    def analyze_summary(self, text):
        result = self.summarize(text)
        return self.format_summary(result)

    def analyze_evaluation(self, text):
        return "100점"

    def analyze_title_recommendation(self, text):
        return "Writing Assistant"

    def analyze_tone_change(self, text, tone):
        return self._append_tone_marker(text, tone)

    def check_spelling(self, text):
        prompt = f"교정\n{text}"
        return self.ai.request(prompt)

    def summarize(self, text):
        prompt = f"요약\n{text}"
        return self.ai.request(prompt)

    def format_spell_check(self, result):
        issues = ""
        corrected = ""

        if isinstance(result, dict):
            issues = result.get("issues", "")
            corrected = result.get("corrected", "")
        else:
            corrected = str(result)

        sections = ["교정 결과:"]
        if not issues.strip():
            issues = self.TEMP_SPELLING_FEEDBACK
        if issues.strip():
            sections.extend(["", issues.strip()])

        sections.extend(["", "교정 결과:", "", self._append_temp_marker(corrected, "spelling")])
        return "\n".join(sections).rstrip()

    def format_summary(self, result):
        summary_text = self._append_temp_marker(result, "summary")
        return f"요약 결과:\n\n{summary_text}"

    def _append_temp_marker(self, text, feature_name):
        value = str(text or "").strip()
        marker = self.TEMP_RESULT_MARKERS[feature_name]
        if value.endswith(marker.strip()):
            return value
        return f"{value}{marker}".strip()

    def _append_tone_marker(self, text, tone):
        value = str(text or "").strip()
        tone_name = str(tone or "").strip() or "\ubb38\uccb4"
        marker = f" [{tone_name} \ubb38\uccb4 \uc0ac\uc6a9\ub428]"
        if value.endswith(marker.strip()):
            return value
        return f"{value}{marker}".strip()
