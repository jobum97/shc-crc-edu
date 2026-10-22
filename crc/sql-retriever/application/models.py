"""단계 사이의 오류 계약과 JSON 호환 데이터 타입을 정의함."""
from typing import Any

Record = dict[str, Any]


class RetrieverError(Exception):
    """설계 오류 코드와 발생 단계만 전달하며 원본 오류는 보관하지 않음."""

    def __init__(self, code: str, stage: str):
        """고정 메시지로 변환할 오류 코드와 단계만 보관함."""
        super().__init__(code)
        self.code = code
        self.stage = stage

