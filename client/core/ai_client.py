class AIClient:
    def request(self, prompt):
        if "맞춤법 검사" in prompt:
            return self.fake_spell_check(prompt)
        if "요약" in prompt:
            return self.fake_summary(prompt)
        return "테스트 응답입니다."

    def fake_spell_check(self, text):
        source_text = text.split("\n", 1)[1] if "\n" in text else text
        corrected = source_text.replace("않되", "안 되").replace("됬", "됐")
        return {
            "issues": "",
            "corrected": corrected,
        }

    def fake_summary(self, text):
        source_text = text.split("\n", 1)[1] if "\n" in text else text
        return source_text.strip()
