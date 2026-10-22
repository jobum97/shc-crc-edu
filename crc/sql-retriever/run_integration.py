"""명시적 실제 통합시험을 수행하며 업무 데이터는 로그에 남기지 않음."""
import argparse
import asyncio
import secrets as token_secrets
import json
import time
from typing import Any
import psycopg
from bootstrap import BASE, create_runtime
from domain.routing import FIXED_QUESTIONS
from domain.budget import stage_limit
from infrastructure.settings import load_configuration


async def main(ephemeral_test_tokens: bool = False, db_only: bool = False) -> int:
    """계정 정책과 설정을 확인하고 카탈로그 및 실제 질문을 시험함."""
    config, secrets = load_configuration(BASE / 'runtime.local.json', BASE.parent / 'secrets/.env')
    testing = config.get('testing', {})
    allow_cardlab = (isinstance(testing, dict) and testing.get('allow_cardlab_test') is True
                    and secrets.get('RDB_USER') == 'cardlab')
    tokens = ({key: token_secrets.token_urlsafe(32) for key in
               ('SQL_RETRIEVER_TOKEN_A', 'SQL_RETRIEVER_TOKEN_B')} if ephemeral_test_tokens else {})
    auth_values = secrets | tokens
    missing = []
    if not allow_cardlab and str(secrets.get('RDB_USER') or '').lower() in ('', 'cardlab', 'postgres'):
        missing.append('비관리자 RDB_USER와 해당 RDB_PASSWORD')
    for key in ('RDB_HOST', 'RDB_PORT', 'RDB_DATABASE', 'RDB_USER', 'RDB_PASSWORD'):
        if not secrets.get(key):
            missing.append(key)
    for key in ('base_date', 'coverage_start', 'loaded_at'):
        if not config.get('metadata', {}).get(key):
            missing.append(f'metadata.{key}')
    entries = config.get('auth', [])
    if len(entries) < 2 or len({v.get('authorized_member_id') for v in entries if v.get('authorized_member_id')}) < 2:
        missing.append('서로 다른 시험 회원 범위 2개')
    if len(entries) < 2 or any(not auth_values.get(e.get('token_env')) for e in entries):
        missing.append('시험용 인증 토큰 환경변수')
    if not db_only and not secrets.get('GROQ_API_KEY'):
        missing.append('GROQ_API_KEY')
    report: dict[str, Any] = {'status': 'blocked' if missing else 'running', 'missing': missing, 'cases': [],
              'business_rows_logged': 0, 'real_db_executed': False, 'real_llm_executed': False,
              'cardlab_test_exception': allow_cardlab, 'ephemeral_test_tokens': ephemeral_test_tokens,
              'scope': 'db_only' if db_only else 'db_and_groq'}
    out = BASE / ('work/db_integration_report.json' if db_only else 'work/integration_report.json')
    if missing:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))
        return 2
    workflow, auth, close = create_runtime(test_tokens=tokens)
    original_model_plan = workflow.service.llm.plan
    original_stage = workflow.service.execute_stage
    original_finish = workflow.service.finish
    stage_timings: dict[str, dict[str, float]] = {}
    model_durations: list[float] = []

    async def measured_stage(number, context, question=None, auth_scope=None):
        active = not context['error']
        started = time.monotonic()
        try:
            return await original_stage(number, context, question, auth_scope)
        finally:
            if active:
                stage_timings.setdefault(context['request_id'], {})[f'S-R{number}'] = time.monotonic()-started

    def measured_finish(context):
        started = time.monotonic()
        try:
            return original_finish(context)
        finally:
            stage_timings.setdefault(context['request_id'], {})['S-R6'] = time.monotonic()-started

    workflow.service.execute_stage = measured_stage
    workflow.service.finish = measured_finish

    async def observed_plan(payload, deadline):
        report['real_llm_executed'] = True
        started = time.monotonic()
        try:
            return await original_model_plan(payload, deadline)
        finally:
            model_durations.append(time.monotonic()-started)

    workflow.service.llm.plan = observed_plan
    try:
        kwargs = {k: secrets[v] for k, v in {'host': 'RDB_HOST', 'port': 'RDB_PORT', 'dbname': 'RDB_DATABASE',
                                            'user': 'RDB_USER', 'password': 'RDB_PASSWORD'}.items()}
        expected = json.loads((BASE / 'resources/view_catalog.json').read_text())
        actual = {}
        expected_profiles = {}
        async with asyncio.timeout(8):
            async with await psycopg.AsyncConnection.connect(**kwargs, options='-c default_transaction_read_only=on') as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE "
                                         "pg_has_role(current_user,oid,'MEMBER') AND "
                                         "(rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls))")
                    role_row = await cursor.fetchone()
                    if role_row is None or (role_row[0] and not allow_cardlab):
                        raise ValueError('role')
                    if allow_cardlab:
                        for entry in entries[:2]:
                            await cursor.execute(
                                'SELECT join_date,age_band FROM public.member WHERE member_id=%s',
                                (entry['authorized_member_id'],))
                            profile_rows = await cursor.fetchall()
                            expected_profiles[entry['principal_id']] = [
                                {'join_date': row[0].isoformat(), 'age_band': row[1]} for row in profile_rows]
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
        if not db_only:
            cases += [('NL-01', '보유 카드 수', 200), ('NL-02', '활성 카드 수', 200)]
        cases += [('REJECT-01', '원본 public.member 테이블에서 모든 회원의 정보를 조회해줘', 403)]
        for label, question, expected_status in cases:
            started = time.monotonic()
            calls_before = len(model_durations)
            scope = auth.authenticate(auth_values[entries[0]['token_env']])
            response, status = await workflow.run(question, scope)
            elapsed = time.monotonic()-started
            stages = stage_timings.pop(response['request_id'], {})
            route = response['meta']['route']
            calls = len(model_durations)-calls_before
            expected_calls = 1 if label.startswith('NL-') else 0
            budgets_ok = all(seconds <= stage_limit(int(stage[-1]), route) for stage, seconds in stages.items())
            row = {'case': label, 'http': status, 'expected_http': expected_status,
                   'seconds': round(elapsed, 4),
                   'passed': status == expected_status and budgets_ok and calls == expected_calls and elapsed < 30,
                   'stage_seconds': {key: round(value, 6) for key, value in stages.items()},
                   'stage_budget_passed': budgets_ok, 'model_calls': calls,
                   'model_seconds': [round(value, 6) for value in model_durations[calls_before:]],
                   'error_code': response['error']['code'] if response['error'] else None,
                   'error_stage': response['error']['stage'] if response['error'] else None,
                   'row_count': len((response.get('data') or {}).get('rows', []))}
            report['cases'].append(row)
            report['real_db_executed'] |= status == 200
        # 서로 다른 회원의 결과는 저장하지 않고 독립 연결의 정상 종료만 기록함.
        for i, entry in enumerate(entries[:2]):
            response, status = await workflow.run(FIXED_QUESTIONS['Q-01'], auth.authenticate(auth_values[entry['token_env']]))
            matches = (response.get('data') or {}).get('rows') == expected_profiles.get(entry['principal_id'])
            report['cases'].append({'case': f'SCOPE-{i+1}', 'http': status,
                                    'profile_matches_member': matches if allow_cardlab else None,
                                    'passed': status == 200 and (matches if allow_cardlab else True)})
        # 같은 워크플로우를 교차 호출해 요청별 회원 상태가 섞이지 않는지 확인함.
        async def concurrent_scope(index):
            entry = entries[index % 2]
            response, status = await workflow.run(FIXED_QUESTIONS['Q-01'],
                                                  auth.authenticate(auth_values[entry['token_env']]))
            matches = (response.get('data') or {}).get('rows') == expected_profiles.get(entry['principal_id'])
            stage_timings.pop(response['request_id'], None)
            return {'case': f'CONCURRENT-SCOPE-{index+1}', 'http': status,
                    'profile_matches_member': matches if allow_cardlab else None,
                    'passed': status == 200 and (matches if allow_cardlab else True)}

        report['cases'].extend(await asyncio.gather(*(concurrent_scope(i) for i in range(4))))
        report['scope_boundary_note'] = (
            '두 회원 프로필을 원본과 대조하고 교차 동시호출4건을 확인함. 전체 뷰와 제한 계정 RLS 증명은 아님.'
            if allow_cardlab else '두 회원 호출의 성공만 확인함. 정답 데이터와 대조한 격리 증명은 아님.')
        report['status'] = 'passed' if all(c['passed'] for c in report['cases']) else 'failed'
        return 0 if report['status'] == 'passed' else 1
    except Exception as exc:
        report['failure_type'] = type(exc).__name__
        report['sqlstate'] = getattr(exc, 'sqlstate', None)
        report['status'] = 'failed_preflight_or_runtime'
        return 1
    finally:
        await close()
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='읽기 전용 SQL 검색기 통합시험')
    parser.add_argument('--ephemeral-test-tokens', action='store_true',
                        help='인증 시험용 토큰을 메모리에 생성하며 .env에 저장하지 않음')
    parser.add_argument('--db-only', action='store_true', help='외부 Groq 호출 없이 DB 시험만 수행함')
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.ephemeral_test_tokens, args.db_only)))
