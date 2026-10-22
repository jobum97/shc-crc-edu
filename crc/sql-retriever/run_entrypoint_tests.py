"""저장된 인증 설정으로 실제 CLI 프로세스와 HTTP 서버의 연동을 검사함."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from bootstrap import BASE
from infrastructure.settings import load_configuration


def summarize(label, response, actual, expected, elapsed):
    """비밀값과 결과 행을 제외한 응답 계약 및 성공 여부만 반환함."""
    contract = isinstance(response, dict) and set(response) == {'request_id', 'status', 'data', 'error', 'meta'}
    success = expected in (0, 200)
    passed = contract and actual == expected and response['status'] == ('success' if success else 'error')
    if label.endswith('-NL'):
        passed = passed and response.get('meta', {}).get('route') == 'nl2sql'
    return {'case': label, 'passed': bool(passed), 'actual_code': actual, 'expected_code': expected,
            'seconds': round(elapsed, 4), 'contract_valid': contract,
            'error_code': (response.get('error') or {}).get('code') if contract else None,
            'route': response['meta'].get('route') if contract else None,
            'row_count': len((response.get('data') or {}).get('rows', [])) if contract else 0}


def main() -> int:
    """두 저장 토큰의 CLI와 API 성공, 모델 연동, 인증 거부를 실제로 검증함.

    부수효과: 로컬 서버를 잠시 실행하고 DB와 승인된 Groq API를 호출함.
    종료: 서버를 닫고 결과 행이나 비밀값이 없는 시험 보고서만 저장함.
    """
    _, values = load_configuration(BASE / 'runtime.local.json', BASE.parent / 'secrets/.env')
    report: dict[str, Any] = {'status': 'running', 'token_source': 'saved_env', 'cases': [], 'business_rows_logged': 0}
    server = None
    listener = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        tokens = [values.get(key) for key in ('SQL_RETRIEVER_TOKEN_A', 'SQL_RETRIEVER_TOKEN_B')]
        if not all(isinstance(token, str) and len(token) >= 32 for token in tokens) or tokens[0] == tokens[1]:
            raise ValueError('token configuration')
        python = sys.executable
        cli_cases = [('CLI-A', '가입일과 연령대', tokens[0], 0),
                     ('CLI-B', '가입일과 연령대', tokens[1], 0),
                     ('CLI-NL', '보유 카드 수', tokens[0], 0),
                     ('CLI-NO-AUTH', '가입일과 연령대', None, 1)]
        for label, question, token, expected in cli_cases:
            child_env = dict(os.environ)
            child_env.pop('SQL_RETRIEVER_CLIENT_TOKEN', None)
            if token is not None:
                child_env['SQL_RETRIEVER_CLIENT_TOKEN'] = token
            started = time.monotonic()
            result = subprocess.run([python, str(BASE / 'run_cli.py')], input=question, text=True,
                                    capture_output=True, cwd=BASE, env=child_env, timeout=35)
            response = json.loads(result.stdout)
            report['cases'].append(summarize(label, response, result.returncode, expected,
                                             time.monotonic() - started))
        # 임의 빈 포트의 소켓을 자식에게 전달하여 고정 포트 충돌을 피함.
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        server = subprocess.Popen([python, '-m', 'uvicorn', 'serve_api:app', '--fd', str(listener.fileno()),
                                   '--no-access-log', '--log-level', 'error'], cwd=BASE,
                                  pass_fds=(listener.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f'http://127.0.0.1:{port}'
        ready_by = time.monotonic() + 10
        while True:
            if server.poll() is not None:
                raise RuntimeError('server startup failed')
            try:
                with opener.open(url + '/openapi.json', timeout=1) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError):
                if time.monotonic() >= ready_by:
                    raise TimeoutError('server readiness') from None
                time.sleep(0.1)
        api_cases = [('API-A', '가입일과 연령대', tokens[0], 200),
                     ('API-B', '가입일과 연령대', tokens[1], 200),
                     ('API-NL', '활성 카드 수', tokens[1], 200),
                     ('API-NO-AUTH', '가입일과 연령대', None, 403)]
        for label, question, token, expected in api_cases:
            headers = {'Content-Type': 'application/json'}
            if token is not None:
                headers['Authorization'] = 'Bearer ' + token
            request = urllib.request.Request(url + '/search',
                                             data=json.dumps({'question': question}).encode(), headers=headers)
            started = time.monotonic()
            try:
                http = opener.open(request, timeout=35)
            except urllib.error.HTTPError as exc:
                http = exc
            with http:
                response = json.load(http)
                report['cases'].append(summarize(label, response, http.code, expected, time.monotonic()-started))
        report['status'] = 'passed' if all(case['passed'] for case in report['cases']) else 'failed'
        return 0 if report['status'] == 'passed' else 1
    except Exception as exc:
        report['status'] = 'failed'
        report['failure_type'] = type(exc).__name__
        return 1
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        if listener is not None:
            listener.close()
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        (BASE / 'work/entrypoint_integration_report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
