"""명시적 실제 통합시험을 수행하며 업무 데이터는 로그에 남기지 않음."""
import asyncio
import json
import time
from typing import Any
import psycopg
from bootstrap import BASE, create_runtime
from domain.routing import FIXED_QUESTIONS
from infrastructure.settings import load_configuration


async def main() -> int:
    """제한 계정과 설정을 확인하고 카탈로그 및 실제 질문을 시험함."""
    config, secrets = load_configuration(BASE / 'runtime.local.json', BASE.parent / 'secrets/.env')
    missing = []
    if secrets.get('RDB_USER') in (None, '', 'cardlab', 'postgres'):
        missing.append('비관리자 RDB_USER와 해당 RDB_PASSWORD')
    for key in ('base_date', 'coverage_start', 'loaded_at'):
        if not config.get('metadata', {}).get(key):
            missing.append(f'metadata.{key}')
    entries = config.get('auth', [])
    if len(entries) < 2 or len({v.get('authorized_member_id') for v in entries if v.get('authorized_member_id')}) < 2:
        missing.append('서로 다른 시험 회원 범위 2개')
    if len(entries) < 2 or any(not secrets.get(e.get('token_env')) for e in entries):
        missing.append('시험용 인증 토큰 환경변수')
    if not secrets.get('GROQ_API_KEY'):
        missing.append('GROQ_API_KEY')
    report: dict[str, Any] = {'status': 'blocked' if missing else 'running', 'missing': missing, 'cases': [],
              'business_rows_logged': 0, 'real_db_executed': False, 'real_llm_executed': False}
    out = BASE / 'work/integration_report.json'
    if missing:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))
        return 2
    workflow, auth, close = create_runtime()
    try:
        kwargs = {k: secrets[v] for k, v in {'host': 'RDB_HOST', 'port': 'RDB_PORT', 'dbname': 'RDB_DATABASE',
                                            'user': 'RDB_USER', 'password': 'RDB_PASSWORD'}.items()}
        expected = json.loads((BASE / 'resources/view_catalog.json').read_text())
        actual = {}
        async with asyncio.timeout(8):
            async with await psycopg.AsyncConnection.connect(**kwargs, options='-c default_transaction_read_only=on') as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE "
                                         "pg_has_role(current_user,oid,'MEMBER') AND "
                                         "(rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls))")
                    role_row = await cursor.fetchone()
                    if role_row is None or role_row[0]:
                        raise ValueError('role')
                    for view in expected:
                        await cursor.execute("SELECT a.attname,format_type(a.atttypid,a.atttypmod) "
                                             "FROM pg_attribute a WHERE a.attrelid=to_regclass(%s) "
                                             "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum", (view,))
                        actual[view] = [{'name': n, 'type': t} for n, t in await cursor.fetchall()]
                        # 조회 이름은 승인 리소스에서만 가져오며 사용자 문자열을 삽입하지 않음.
                        await cursor.execute(f'SELECT * FROM {view} LIMIT 0')
            if any(actual[v] != expected[v]['columns'] for v in expected):
                report['status'] = 'catalog_drift'
                return 2
        (BASE / 'work/live_db_catalog.json').write_text(json.dumps(actual, ensure_ascii=False, indent=2) + '\n')
        report['catalog_match'] = True
        cases = [(qid, q, 200) for qid, q in FIXED_QUESTIONS.items()]
        cases += [('NL-01', '보유 카드 수', 200),
                  ('REJECT-01', '원본 public.member 테이블에서 모든 회원의 정보를 조회해줘', 422)]
        for label, question, expected_status in cases:
            started = time.monotonic()
            scope = auth.authenticate(secrets[entries[0]['token_env']])
            response, status = await workflow.run(question, scope)
            row = {'case': label, 'http': status, 'expected_http': expected_status,
                   'seconds': round(time.monotonic() - started, 4), 'passed': status == expected_status,
                   'error_code': response['error']['code'] if response['error'] else None,
                   'row_count': len((response.get('data') or {}).get('rows', []))}
            report['cases'].append(row)
            report['real_db_executed'] |= status == 200
            report['real_llm_executed'] |= response['meta']['route'] == 'nl2sql'
        # 서로 다른 회원의 결과는 저장하지 않고 독립 연결의 정상 종료만 기록함.
        for i, entry in enumerate(entries[:2]):
            response, status = await workflow.run(FIXED_QUESTIONS['Q-01'], auth.authenticate(secrets[entry['token_env']]))
            report['cases'].append({'case': f'SCOPE-{i+1}', 'http': status, 'passed': status == 200})
        report['scope_boundary_note'] = '두 회원 호출의 성공만 확인함. 정답 데이터와 대조한 격리 증명은 아님.'
        report['status'] = 'passed' if all(c['passed'] for c in report['cases']) else 'failed'
        return 0 if report['status'] == 'passed' else 1
    except Exception:
        report['status'] = 'failed_preflight_or_runtime'
        return 1
    finally:
        await close()
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
