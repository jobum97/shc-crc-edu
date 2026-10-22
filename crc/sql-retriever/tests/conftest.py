"""외부 연결 없이 승인 계약을 검증하는 시험 조립체를 제공함."""
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from application.models import RetrieverError
from application.services import RetrieverService
from infrastructure.sql_validator import SqlValidator
from infrastructure.workflow import LangChainWorkflow

BASE = Path(__file__).resolve().parents[1]


def read_resource(name):
    """승인 명세에서 추출한 시험 기준을 읽음."""
    return json.loads((BASE / 'resources' / name).read_text())


class FakeModel:
    """호출 수를 기록하고 합성 카드 집계 계획을 반환함."""

    def __init__(self):
        """호출 기록과 선택적 오류를 초기화함."""
        self.calls = []
        self.failure = None

    async def plan(self, payload, deadline):
        """정제 페이로드만 기록하며 실제 네트워크를 사용하지 않음."""
        self.calls.append(payload)
        if self.failure:
            raise RetrieverError(self.failure, 'S-R2')
        return {'status': 'ready', 'reason_code': 'OK', 'view': 'app.customer_cards',
                'sql': 'SELECT COUNT(*) AS card_count FROM app.customer_cards LIMIT 1;',
                'grain': 'aggregate', 'period_start': None, 'period_end': None,
                'selected_columns': ['card_count']}


class FakeRepository:
    """실행 호출 수와 공개 SQL 입력만 검증하는 가짜 저장소임."""

    def __init__(self):
        """실행 횟수와 오류를 초기화함."""
        self.calls = 0
        self.failure = None

    async def execute(self, context):
        """안전한 합성 결과를 반환하며 DB에 연결하지 않음."""
        self.calls += 1
        if self.failure:
            raise RetrieverError(self.failure, 'S-R5')
        return {'rows': [], 'columns': [], 'grain': context['plan']['grain'],
                'period': None, 'completeness': 'complete'}


@pytest.fixture
def system():
    """실제 업무 규칙과 SQL 검사기에 가짜 외부 포트만 주입함."""
    catalog, queries = read_resource('view_catalog.json'), read_resource('fixed_queries.json')
    config = {'metadata': {'base_date': '2026-06-15', 'coverage_start': '2026-01-01',
                          'loaded_at': datetime.now(timezone.utc).isoformat()},
              'policy': read_resource('sql_policy.json')}
    model, repository = FakeModel(), FakeRepository()
    service = RetrieverService(config, catalog, queries, read_resource('errors.json'), model,
                               repository, SqlValidator(catalog, queries))
    scope = {'principal_id': 'synthetic-agent', 'authorized_member_id': 'synthetic-member'}
    return LangChainWorkflow(service), service, model, repository, scope
