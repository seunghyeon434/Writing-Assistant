import os
from openai import OpenAI


class AIService:
    def __init__(self):
        """api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
        self.client = OpenAI(api_key=api_key)"""

    def correct_text(self, text: str) -> str:
        return text + "\n\n(교정 완료 - 테스트 모드)"
        prompt = f"""
다음 한국어 문장을 자동 교정해 주세요.

요구사항:
1. 맞춤법과 띄어쓰기를 교정
2. 어색한 표현을 자연스럽게 수정
3. 원래 의미는 유지
4. 설명 없이 수정된 결과만 출력

입력:
{text}
"""

        response = self.client.responses.create(
            model="gpt-5.4",
            input=prompt
        )
        return response.output_text.strip()