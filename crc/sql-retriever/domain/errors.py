"""외부 입력을 보관하지 않는 정책 오류를 정의함."""


class PolicyError(Exception):
    """공개 가능한 오류 코드만 보관하며 질문 원문을 보관하지 않음."""

    def __init__(self, code: str):
        """계층 밖에서 고정 메시지로 변환할 오류 코드를 보관함."""
        super().__init__(code)
        self.code = code
