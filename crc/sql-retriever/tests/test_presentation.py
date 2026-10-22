"""실제 FastAPI 입구와 CLI가 동일 응답 계약을 전달하는지 확인함."""
import argparse
import json
from fastapi.testclient import TestClient
from infrastructure.settings import LocalAuthenticator
from presentation.api import create_app
from presentation.cli import execute
from domain.routing import FIXED_QUESTIONS


def make_auth(scope):
    """실제 개인정보가 없는 시험 토큰을 로컬 인증기에 주입함."""
    return LocalAuthenticator([{**scope, 'token_env': 'TEST_TOKEN'}], {'TEST_TOKEN': 'x' * 32})


def test_api_valid_and_unauthorized(system):
    """HTTP 입구에서 회원 범위가 body로 확대되지 않음을 확인함."""
    workflow, _, _, _, scope = system
    with TestClient(create_app(workflow, make_auth(scope))) as client:
        response = client.post('/search', json={'question': FIXED_QUESTIONS['Q-01']},
                               headers={'Authorization': 'Bearer ' + 'x' * 32})
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'success'
        assert client.post('/search', json={'question': '보유 카드 수'}).status_code == 403
        assert client.post('/search', json={'question': '보유 카드 수', 'auth_scope': scope}).status_code == 400


def test_api_rejects_large_body(system):
    """요청을 모두 메모리에 적재하기 전에 상한을 적용함."""
    workflow, _, _, _, scope = system
    with TestClient(create_app(workflow, make_auth(scope))) as client:
        response = client.post('/search', content='x' * 17000)
        assert response.status_code == 400


async def test_cli_json_and_exit_code(system, monkeypatch, capsys):
    """CLI 성공 결과를 표준 출력 JSON과 종료 코드로 확인함."""
    workflow, _, _, _, scope = system
    async def close():
        return None
    monkeypatch.setenv('TEST_CLIENT_TOKEN', 'x' * 32)
    args = argparse.Namespace(question=FIXED_QUESTIONS['Q-01'], config=None, env=None, token_env='TEST_CLIENT_TOKEN')
    code = await execute(args, lambda *_: (workflow, make_auth(scope), close))
    assert code == 0 and json.loads(capsys.readouterr().out)['status'] == 'success'


def test_duplicate_token_rejected():
    """동일 토큰의 회원 범위가 중복되면 임의 선택하지 않음."""
    entry = {'principal_id': 'fake', 'authorized_member_id': 'fake-a', 'token_env': 'TOKEN'}
    assert LocalAuthenticator([entry, entry], {'TOKEN': 'x' * 32}).authenticate('x' * 32) is None


def test_api_duplicate_keys_rejected(system):
    """중복 question 키의 모호한 해석을 차단함."""
    workflow, _, _, _, scope = system
    with TestClient(create_app(workflow, make_auth(scope))) as client:
        response = client.post('/search', content='{"question":"a","question":"b"}',
                               headers={'Authorization': 'Bearer ' + 'x' * 32})
        assert response.status_code == 400


def test_malformed_auth_entries_fail_closed():
    """잘못된 인증 설정이 예외나 임의 범위 선택으로 이어지지 않음을 확인함."""
    for entries in (None, 'wrong', [None], [{'token_env': []}]):
        assert LocalAuthenticator(entries, {}).authenticate('x' * 32) is None
