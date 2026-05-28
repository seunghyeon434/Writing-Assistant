class AIClient:
    def request(self, prompt):
        prompt_text = str(prompt or "")
        if prompt_text.startswith("??\n") or prompt_text.startswith("??? ??\n"):
            return self.fake_spell_check(prompt_text)
        if prompt_text.startswith("??\n"):
            return self.fake_summary(prompt_text)
        return prompt_text.split("\n", 1)[1] if "\n" in prompt_text else prompt_text

    def fake_spell_check(self, text):
        source_text = text.split("\n", 1)[1] if "\n" in text else text
        return {
            "issues": "",
            "corrected": source_text,
        }

    def fake_summary(self, text):
        source_text = text.split("\n", 1)[1] if "\n" in text else text
        return source_text.strip()
