"""단계 흐름과 오류 종료, 시간 계산 및 비밀정보 경계를 확인함."""
import pytest
from domain.budget import can_start
from domain.routing import FIXED_QUESTIONS


@pytest.mark.parametrize('stage,elapsed,expected', [(1, 0, True), (2, 1, True), (2, 12, False),
                                                  (3, 17, True), (5, 21, True), (5, 27, False)])
def test_budget_examples(stage, elapsed, expected):
    """PPT25쪽의 두 예산 검사 예와 같은 결과임을 확인함."""
    assert can_start(stage, elapsed, None) is expected


@pytest.mark.parametrize('qid', [f'Q-{n:02d}' for n in range(1, 9)])
async def test_fixed_flow_never_calls_model(system, qid):
    """여덟 정형 경로 모두 공통 SQL 검증 후 DB만 한 번 호출함."""
    workflow, _, model, repo, scope = system
    response, status = await workflow.run(FIXED_QUESTIONS[qid], scope)
    assert status == 200, response
    assert response['meta']['query_id'] == qid
    assert repo.calls == 1 and not model.calls


async def test_nl2sql_once(system):
    """정형에 없는 명확한 집계 질문은 모델을 한 번만 사용함."""
    workflow, _, model, repo, scope = system
    response, status = await workflow.run('보유 카드 수', scope)
    assert status == 200, response
    assert len(model.calls) == repo.calls == 1
    assert scope['authorized_member_id'] not in str(model.calls)
    assert 'member_scope' not in model.calls[0]


@pytest.mark.parametrize('question,code', [(None, 'INVALID_REQUEST'), ('', 'INVALID_REQUEST'),
                                         ('가' * 1400, 'INVALID_REQUEST')])
async def test_invalid_input_stops_before_external(system, question, code):
    """잘못된 입력은 모델과 DB에 전달되지 않음."""
    workflow, _, model, repo, scope = system
    response, status = await workflow.run(question, scope)
    assert status == 400 and response['error']['code'] == code
    assert repo.calls == 0 and not model.calls


async def test_missing_auth(system):
    """클라이언트 질문만으로 회원 권한이 생기지 않음."""
    workflow, _, model, repo, _ = system
    response, status = await workflow.run('보유 카드 수', None)
    assert status == 403 and response['error']['stage'] == 'S-R1'
    assert not model.calls and repo.calls == 0


async def test_stale_config(system):
    """날짜 스냅샷이 오래되면 현재 날짜로 대체하지 않음."""
    workflow, service, model, repo, scope = system
    service.config['metadata']['loaded_at'] = '2000-01-01T00:00:00+00:00'
    response, status = await workflow.run('보유 카드 수', scope)
    assert status == 503 and response['error']['code'] == 'CONFIG_UNAVAILABLE'
    assert not model.calls and repo.calls == 0


@pytest.mark.parametrize('code,http', [('UPSTREAM_TIMEOUT', 504), ('UPSTREAM_UNAVAILABLE', 503),
                                     ('INVALID_MODEL_OUTPUT', 502)])
async def test_model_failure_no_db_or_retry(system, code, http):
    """제공자 실패는 정확한 상태로 반환하고 재호출하지 않음."""
    workflow, _, model, repo, scope = system
    model.failure = code
    response, status = await workflow.run('보유 카드 수', scope)
    assert status == http and response['error']['code'] == code
    assert len(model.calls) == 1 and repo.calls == 0


@pytest.mark.parametrize('code', ['DB_EXECUTION_FAILED', 'RESULT_TOO_LARGE'])
async def test_db_failure_discards_result(system, code):
    """실패한 DB 결과를 성공으로 포장하지 않음."""
    workflow, _, model, repo, scope = system
    repo.failure = code
    response, status = await workflow.run(FIXED_QUESTIONS['Q-01'], scope)
    assert status in (503, 422) and response['data'] is None and response['error']['code'] == code
    assert repo.calls == 1 and not model.calls


async def test_response_serialization_failure(system):
    """직렬화할 수 없는 결과도 고정 S-R6 오류로 종료함."""
    _, service, _, _, _ = system
    context = service.initial()
    context['result'] = {'rows': [object()]}
    response, status = service.finish(context)
    assert status == 500 and response['error']['code'] == 'RESPONSE_FAILED'
