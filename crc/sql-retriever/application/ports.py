"""업무 계층이 필요로 하는 외부 기술의 계약을 정의함."""
from abc import abstractmethod
from typing import Protocol
from .models import Record


class LanguageModelPort(Protocol):
    """정제된 입력을 조회 계획으로 바꾸는 계약임."""

    @abstractmethod
    async def plan(self, payload: Record, deadline: float) -> Record:
        """식별자 없는 입력을 받아 계획을 반환함.

        예외: 호출/응답 실패 시 RetrieverError 발생함.
        부수효과: 외부 모델 호출 최대 1회이며 결과 DB 행은 전송하지 않음.
        """
        ...


class RepositoryPort(Protocol):
    """검증된 SQL을 읽기 전용으로 실행하는 계약임."""

    @abstractmethod
    async def execute(self, context: Record) -> Record:
        """검증 SQL과 인증 회원 및 날짜를 받아 QueryResult를 반환함.

        예외: 시간/DB/크기 오류 시 RetrieverError 발생함.
        부수효과: 읽기 전용 조회이며 세션 값은 트랜잭션 안에서만 유지함.
        """
        ...


class SqlValidatorPort(Protocol):
    """계획과 SQL 구조를 대조하는 계약임."""

    @abstractmethod
    def validate(self, plan: Record, slots: Record) -> str:
        """계획과 확인한 슬롯을 받아 변경하지 않은 검증 SQL을 반환함.

        예외: 허용되지 않거나 계획과 다른 SQL은 RetrieverError 발생함.
        부수효과: 없음. DB와 모델을 호출하지 않음.
        """
        ...


class WorkflowPort(Protocol):
    """S-R1부터 S-R6까지 단계 실행을 제공하는 계약임."""

    @abstractmethod
    async def run(self, question: object, auth_scope: Record | None) -> tuple[Record, int]:
        """인증 어댑터가 확인한 범위와 질문으로 응답과 HTTP 상태를 반환함.

        오류는 설계 응답으로 변환함. 부수효과는 주입한 단계 포트에 따름.
        """
        ...
