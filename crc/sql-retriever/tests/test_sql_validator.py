"""안전하지 않은 SQL이 실행 포트에 도달하기 전 거부되는지를 검증함."""
import json
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from application.models import RetrieverError
from infrastructure.sql_validator import SqlValidator


RESOURCES = Path(__file__).resolve().parents[1] / 'resources'
CATALOG = json.loads((RESOURCES / 'view_catalog.json').read_text())
QUERIES = json.loads((RESOURCES / 'fixed_queries.json').read_text())


@pytest.fixture
def validator():
    """공개 View와 등록 SQL을 주입한 검증기를 반환함."""
    return SqlValidator(CATALOG, QUERIES)


def _plan(sql, view='app.customer_cards', grain='card', columns=None, **kwargs):
    if columns is None:
        try:
            columns = [node.alias_or_name for node in sqlglot.parse_one(sql, read='postgres').expressions]
        except (sqlglot.errors.SqlglotError, AttributeError):
            columns = ['card_ref']
    return {'sql': sql, 'view': view, 'grain': grain, 'selected_columns': columns,
            'period_start': None, 'period_end': None, 'route': 'nl2sql', **kwargs}


def _rejected(validator, plan, slots=None):
    with pytest.raises(RetrieverError) as caught:
        validator.validate(plan, slots or {})
    assert (caught.value.code, caught.value.stage) == ('SQL_REJECTED', 'S-R4')
    assert str(caught.value) == 'SQL_REJECTED'


@pytest.mark.parametrize('query_id', sorted(QUERIES))
def test_all_eight_registered_queries_pass_without_rewriting(validator, query_id):
    """정형 SQL도 동일한 검증기를 통과하며 바이트가 바뀌지 않음을 확인함."""
    query = QUERIES[query_id]
    plan = _plan(query['sql'], query['view'], query['grain'], route='fixed', query_id=query_id)
    slots = {'status_filter': 'ACTIVE'} if query_id == 'Q-08' else {}
    assert validator.validate(plan, slots) == query['sql']


@pytest.mark.parametrize('sql', [
    'SELECT * FROM app.customer_cards LIMIT 1',
    'SELECT card_ref FROM app.customer_cards',
    'SELECT card_ref FROM app.customer_cards LIMIT 0',
    'SELECT card_ref FROM app.customer_cards LIMIT 201',
    "SELECT card_ref FROM app.customer_cards LIMIT '1'",
    'SELECT card_ref FROM app.customer_cards LIMIT 1+1',
    'SELECT card_ref FROM app.customer_cards LIMIT 1 OFFSET 0',
    'SELECT card_ref FROM app.customer_cards FETCH FIRST 1 ROWS ONLY',
    'SELECT card_ref FROM app.customer_cards LIMIT 1; SELECT 1',
    'SELECT card_ref FROM app.customer_cards LIMIT 1;;',
    'SELECT card_ref INTO temporary x FROM app.customer_cards LIMIT 1',
    'SELECT card_ref FROM app.customer_cards LIMIT 1 FOR UPDATE',
    'WITH x AS (SELECT card_ref FROM app.customer_cards) SELECT card_ref FROM x LIMIT 1',
    'SELECT card_ref FROM (SELECT card_ref FROM app.customer_cards) x LIMIT 1',
    'SELECT card_ref FROM app.customer_cards JOIN app.customer_profile ON TRUE LIMIT 1',
    'SELECT card_ref FROM app.customer_cards, app.customer_profile LIMIT 1',
    'SELECT card_ref FROM app.customer_cards UNION SELECT card_ref FROM app.customer_cards LIMIT 1',
    'SELECT card_ref FROM app_internal.customer_cards LIMIT 1',
    'SELECT card_ref FROM pg_catalog.pg_roles LIMIT 1',
    'SELECT card_ref FROM app.cards LIMIT 1',
    'SELECT card_ref FROM customer_cards LIMIT 1',
    'SELECT card_id FROM app.customer_cards LIMIT 1',
    'SELECT member_id FROM app.customer_cards LIMIT 1',
    'SELECT pg_sleep(1) AS x FROM app.customer_cards LIMIT 1',
    "SELECT set_config('app.member_id','fake',true) AS x FROM app.customer_cards LIMIT 1",
    "SELECT current_setting('app.member_id') AS x FROM app.customer_cards LIMIT 1",
    'SELECT (SELECT 1) AS x FROM app.customer_cards LIMIT 1',
    'SELECT CASE WHEN TRUE THEN card_ref END AS x FROM app.customer_cards LIMIT 1',
    'SELECT COUNT(*) OVER () AS x FROM app.customer_cards LIMIT 1',
    'SELECT card_ref FROM app.customer_cards WHERE card_ref LIKE \'A%\' LIMIT 1',
    'SELECT card_ref FROM app.customer_cards WHERE card_ref = $1 LIMIT 1',
    'SELECT card_ref FROM app.customer_cards WHERE card_ref = :name LIMIT 1',
    'SELECT card_ref FROM app.customer_cards WHERE card_ref = %(name)s LIMIT 1',
    "SELECT 'now'::date AS x FROM app.customer_cards LIMIT 1",
    "SELECT '2026-02-31'::date AS x FROM app.customer_cards LIMIT 1",
    'SELECT annual_fee::numeric AS x FROM app.customer_cards LIMIT 1',
    "SELECT 'fake'::regclass AS x FROM app.customer_cards LIMIT 1",
    'SELECT card_ref FROM app.customer_cards TABLESAMPLE BERNOULLI(1) LIMIT 1',
    'SELECT card_ref FROM app.customer_cards WHERE TRUE OR pg_sleep(1) IS NULL LIMIT 1',
    'SELECT card_ref FROM app.customer_cards ORDER BY pg_sleep(1) LIMIT 1',
    'SELECT COUNT(pg_sleep(1)) AS x FROM app.customer_cards LIMIT 1',
    'SELECT SUM(card_ref) AS x FROM app.customer_cards LIMIT 1',
    'SELECT card_ref FROM app.customer_cards WHERE annual_fee = TRUE LIMIT 1',
    "SELECT NVL(annual_fee,0) AS x FROM app.customer_cards LIMIT 1",
    "SELECT IFNULL(annual_fee,0) AS x FROM app.customer_cards LIMIT 1",
    'SELECT pg_catalog.sum(annual_fee) AS x FROM app.customer_cards LIMIT 1',
    "SELECT TIMESTAMP_TRUNC(issue_date, MONTH) AS x FROM app.customer_cards LIMIT 1",
    "SELECT pg_catalog.card_ref FROM app.customer_cards LIMIT 1",
])
def test_unsafe_structures_and_functions_are_rejected(validator, sql):
    """금지 구문과 파서가 별칭으로 정규화하는 함수도 원문까지 확인해 차단함."""
    _rejected(validator, _plan(sql))


@pytest.mark.parametrize('expression', [
    'COUNT(*)', 'SUM(annual_fee)', 'AVG(annual_fee)', 'MIN(annual_fee)', 'MAX(annual_fee)',
    'COALESCE(SUM(annual_fee),0)', 'ROUND(AVG(annual_fee),2)',
])
def test_allowed_aggregate_function_types(validator, expression):
    """집계 함수의 허용 타입과 명시 출력 별칭을 확인함."""
    sql = f'SELECT {expression} AS amount FROM app.customer_cards LIMIT 1'
    assert validator.validate(_plan(sql, grain='aggregate'), {}) == sql


@pytest.mark.parametrize('expression', [
    "DATE_TRUNC('month',issue_date)", "TO_CHAR(issue_date,'YYYY-MM')",
    "DATE '2026-10-01'", "'12.5'::numeric", 'annual_fee+1',
    'annual_fee-1', 'annual_fee*2', 'annual_fee/2', 'COALESCE(annual_fee,0)',
])
def test_safe_projection_expressions(validator, expression):
    """허용 날짜 및 숫자 상수와 연산자의 타입을 검사함."""
    sql = f'SELECT {expression} AS value FROM app.customer_cards LIMIT 10'
    assert validator.validate(_plan(sql), {}) == sql


def test_output_order_and_view_must_match_plan(validator):
    """JSON 주장과 실제 조회 대상 또는 컬럼 순서가 다르면 거부함."""
    sql = 'SELECT card_ref, brand FROM app.customer_cards LIMIT 20'
    _rejected(validator, _plan(sql, columns=['brand', 'card_ref']))
    _rejected(validator, _plan(sql, view='app.customer_profile'))


def test_grain_requires_matching_group_keys(validator):
    """카드별 질문에 전체 합계를 주거나 GROUP BY 밖 컬럼을 섞지 못함."""
    sql = 'SELECT SUM(annual_fee) AS amount FROM app.customer_cards LIMIT 1'
    _rejected(validator, _plan(sql, grain='card'))
    grouped = 'SELECT card_ref, SUM(annual_fee) AS amount FROM app.customer_cards GROUP BY card_ref LIMIT 20'
    assert validator.validate(_plan(grouped), {}) == grouped
    wrong = 'SELECT brand, SUM(annual_fee) AS amount FROM app.customer_cards GROUP BY card_ref LIMIT 20'
    _rejected(validator, _plan(wrong))
    wrong = 'SELECT SUM(annual_fee)+annual_fee AS amount FROM app.customer_cards LIMIT 20'
    _rejected(validator, _plan(wrong, grain='aggregate'))


@pytest.mark.parametrize('where', [
    "current_status='ACTIVE' OR annual_fee>0",
    "NOT (current_status='ACTIVE')", 'annual_fee>0',
])
def test_status_cannot_be_bypassed_with_or_or_not(validator, where):
    """활성 상태 조건은 모든 OR 분기에서 성립해야 함."""
    sql = f'SELECT card_ref FROM app.customer_cards WHERE {where} LIMIT 20'
    _rejected(validator, _plan(sql), {'status_filter': 'ACTIVE'})


def test_status_present_in_both_or_branches_is_guaranteed(validator):
    """OR를 일괄 금지하지 않고 양쪽에 있는 상태 조건을 인정함."""
    sql = "SELECT card_ref FROM app.customer_cards WHERE (current_status='ACTIVE' AND current_status='ACTIVE') OR current_status='ACTIVE' LIMIT 20"
    assert validator.validate(_plan(sql), {'status_filter': 'ACTIVE'}) == sql


def test_explicit_filters_cannot_disappear(validator):
    """확인된 질문 조건이 SQL에 없으면 거부함."""
    sql = 'SELECT card_ref FROM app.customer_cards WHERE annual_fee>=100 LIMIT 20'
    slots = {'filters': [{'column': 'annual_fee', 'operator': '>=', 'value': 100}]}
    assert validator.validate(_plan(sql), slots) == sql
    slots['filters'][0]['value'] = 101
    _rejected(validator, _plan(sql), slots)


def test_period_bounds_must_be_present_in_sql(validator):
    """계획이 주장한 날짜 범위가 실제 필터와 일치해야 함."""
    base = 'SELECT usage_date, approved_amount FROM app.customer_daily_usage'
    where = " WHERE usage_date BETWEEN DATE '2026-10-01' AND DATE '2026-10-21'"
    plan = _plan(base + where + ' LIMIT 90', 'app.customer_daily_usage', 'day',
                 period_start='2026-10-01', period_end='2026-10-21')
    assert validator.validate(plan, {}) == plan['sql']
    plan['sql'] = base + ' LIMIT 90'
    _rejected(validator, plan)
    plan['sql'] = base + where + ' OR approved_amount>0 LIMIT 90'
    _rejected(validator, plan)


def test_fixed_query_requires_exact_registered_bytes(validator):
    """정형 SQL 경로에서 공백을 포함한 바이트 변경을 탐지함."""
    query = QUERIES['Q-02']
    plan = _plan(query['sql'] + ' ', route='fixed', query_id='Q-02')
    _rejected(validator, plan)
    plan = _plan(query['sql'], route='fixed')
    _rejected(validator, plan)


def test_byte_limit_and_error_content(validator):
    """UTF-8 크기 상한을 적용하고 SQL이나 원인 예외를 외부에 노출하지 않음."""
    sql = "SELECT card_ref FROM app.customer_cards WHERE card_ref='" + '가' * 6000 + "' LIMIT 1"
    _rejected(validator, _plan(sql))


def test_metric_alias_cannot_disguise_another_calculation(validator):
    """카드 수 이름의 연회비 합계와 연회비 이름의 상수를 차단함."""
    sql = 'SELECT SUM(annual_fee) AS card_count FROM app.customer_cards LIMIT 1'
    _rejected(validator, _plan(sql, grain='aggregate'), {'metrics': ['card_count'], 'intent': 'count'})
    sql = 'SELECT 0 AS annual_fee FROM app.customer_cards LIMIT 20'
    _rejected(validator, _plan(sql), {'metrics': ['annual_fee']})
    sql = 'SELECT AVG(annual_fee) AS fee FROM app.customer_cards LIMIT 1'
    _rejected(validator, _plan(sql, grain='aggregate'), {'metrics': ['annual_fee'], 'intent': 'sum'})


def test_period_cannot_be_silently_shortened(validator):
    """계획과 같은 필터 뒤에 더 좁은 필터를 추가하는 우회를 차단함."""
    sql = "SELECT usage_date FROM app.customer_daily_usage WHERE usage_date >= '2026-10-01' AND usage_date <= '2026-10-21' AND usage_date >= '2026-10-10' LIMIT 90"
    plan = _plan(sql, 'app.customer_daily_usage', 'day', period_start='2026-10-01', period_end='2026-10-21')
    _rejected(validator, plan)


@pytest.mark.parametrize('where', [
    "current_status='ACTIVE' AND annual_fee>100",
    "current_status='ACTIVE' AND FALSE",
    "current_status='ACTIVE' AND annual_fee IS NOT NULL",
    "current_status='ACTIVE' AND (annual_fee>100 OR annual_fee<10)",
])
def test_unrequested_filters_are_rejected(validator, where):
    """활성 카드 수 요청에 추가 필터를 섞어 다른 수치를 반환하지 못하게 함."""
    sql = f'SELECT COUNT(*) AS card_count FROM app.customer_cards WHERE {where} LIMIT 1'
    slots = {'status_filter': 'ACTIVE', 'metrics': ['card_count'], 'intent': 'count'}
    _rejected(validator, _plan(sql, grain='aggregate'), slots)


def test_hidden_having_and_filters_without_slots_are_rejected(validator):
    """확인한 조건이 없으면 WHERE와 HAVING의 임의 제한을 거부함."""
    _rejected(validator, _plan('SELECT card_ref FROM app.customer_cards WHERE annual_fee>0 LIMIT 20'))
    sql = 'SELECT COUNT(*) AS card_count FROM app.customer_cards HAVING COUNT(*)>100 LIMIT 1'
    _rejected(validator, _plan(sql, grain='aggregate'), {'metrics': ['card_count'], 'intent': 'count'})
