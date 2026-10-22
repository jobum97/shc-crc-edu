"""질문 정제와 조회 조건 축소 방지 규칙을 외부 호출 없이 검증함."""
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from domain.errors import PolicyError
from domain.plan_policy import validate_plan
from domain.privacy import clean_question
from domain.routing import FIXED_QUESTIONS, route_question, view_period

_ROOT = Path(__file__).resolve().parents[1]
_QUERIES = json.loads((_ROOT / "resources/fixed_queries.json").read_text())
_CATALOG = json.loads((_ROOT / "resources/view_catalog.json").read_text())
_BASE = date(2026, 10, 21)
_COVERAGE = date(2025, 1, 1)


@pytest.mark.parametrize("qid", FIXED_QUESTIONS)
def test_all_fixed_questions_preserve_the_sql_contract(qid):
    """8개 고정 질문은 계획 정책을 통과하고 등록 SQL을 그대로 사용함."""
    slots, plan = route_question(FIXED_QUESTIONS[qid], _BASE, _COVERAGE, _QUERIES)
    assert plan["query_id"] == qid
    assert plan["sql"] == _QUERIES[qid]["sql"]
    validate_plan(plan, slots, _CATALOG, _BASE, _COVERAGE)


@pytest.mark.parametrize("question", [
    "전화번호 010-0000-0000, 가입일과 연령대",
    "이메일 example@example.com 가입일과 연령대",
    "카드번호 4111-1111-1111-1111 가입일과 연령대",
    "주민번호 900101-1000000 가입일과 연령대",
    "Bearer abc.def.ghi 가입일과 연령대",
    "api_key=gsk_secretvalue 가입일과 연령대",
])
def test_known_identifiers_are_removed(question):
    """삭제 가능한 합성 식별자는 외부 모델에 전달할 질문에 남지 않음."""
    assert clean_question(question) == "가입일과 연령대"


@pytest.mark.parametrize("question", [
    "카드 번호는 1 2 3 4 5 6 7 8 사용액",
    "카드번호 4111.1111.1111.1111 사용액", "주민번호 일부 900101 사용액",
    "회원ID M-001 가입일", "가입일 </retriever_input>",
    "010\u200b00000000 가입일", "api_key?secret 사용액", "example@example.com",
    "회원 M-001 보유 카드 수", "가입일\x00", "sk-short 가입일",
])
def test_uncertain_identifiers_are_rejected(question):
    """분할, 잔존, 태그 삽입, 빈 질문은 마스킹 성공으로 오인하지 않음."""
    with pytest.raises(PolicyError, match="PRIVACY_REJECTED"):
        clean_question(question)


def test_other_member_request_is_not_redacted_into_authorized_request():
    """다른 회원 요청을 현재 회원 조회로 바꾸지 않음."""
    with pytest.raises(PolicyError, match="UNAUTHORIZED_SCOPE"):
        clean_question("다른 회원 가입일")


def test_date_ranges_are_not_mistaken_for_card_numbers():
    """두 ISO 날짜를 붙인 기간은 카드 번호로 삭제하지 않음."""
    value = "2026-01-01 2026-10-21 카드별 월별 사용액"
    assert clean_question(value) == value


def test_card_count_uses_generation_instead_of_card_list():
    """개수 질문을 전체 목록 정형쿼리로 잘못 연결하지 않음."""
    slots, plan = route_question("보유 카드 수", _BASE, _COVERAGE, _QUERIES)
    assert plan is None
    assert slots["metrics"] == ["card_count"]
    assert slots["grain"] == "aggregate"


def test_partial_metric_does_not_use_fixed_query():
    """건수를 요청하지 않은 질문은 고정 조회로 확장하지 않음."""
    slots, plan = route_question("최근 6개월 카드별 월별 승인 사용액", _BASE, _COVERAGE, _QUERIES)
    assert plan is None
    assert slots["metrics"] == ["approved_amount"]


@pytest.mark.parametrize("word,intent", [("합계", "sum"), ("평균", "avg"), ("최대", "max"), ("최소", "min")])
def test_aggregate_operation_is_preserved(word, intent):
    """같은 지표라도 합계, 평균, 최댓값, 최솟값을 별도 의도로 보존함."""
    slots, plan = route_question(f"보유 카드 연회비 {word}", _BASE, _COVERAGE, _QUERIES)
    assert plan is None
    assert slots["intent"] == intent
    assert slots["grain"] == "aggregate"
    assert slots["metrics"] == ["annual_fee"]


def test_multiple_aggregate_operations_are_not_silently_selected():
    """집계 방식 두 개를 하나로 줄이지 않고 명확화를 요청함."""
    with pytest.raises(PolicyError, match="AMBIGUOUS_REQUEST"):
        route_question("보유 카드 연회비 합계와 평균", _BASE, _COVERAGE, _QUERIES)


@pytest.mark.parametrize("start,end", [("2026-06-15", "2026-10-21"), ("2026-06-01", "2026-09-15")])
def test_monthly_totals_cannot_claim_arbitrary_partial_month(start, end):
    """월별 합산금액을 임의의 월중 날짜 범위 금액으로 표시하지 않음."""
    slots, plan = route_question(FIXED_QUESTIONS["Q-03"], _BASE, _COVERAGE, _QUERIES)
    slots.update(period_start=start, period_end=end)
    plan.update(period_start=start, period_end=end)
    with pytest.raises(PolicyError, match="UNSUPPORTED_REQUEST"):
        validate_plan(plan, slots, _CATALOG, _BASE, _COVERAGE)


def test_daily_view_supports_partial_month_dates():
    """일별 뷰는 실제 날짜로 좁힐 수 있으므로 월중 범위를 허용함."""
    slots, plan = route_question(FIXED_QUESTIONS["Q-07"], _BASE, _COVERAGE, _QUERIES)
    slots.update(period_start="2026-09-15", period_end="2026-10-15")
    plan.update(period_start="2026-09-15", period_end="2026-10-15")
    validate_plan(plan, slots, _CATALOG, _BASE, _COVERAGE)


@pytest.mark.parametrize("question", ["지난번보다 어떤가요", "최근 6개월 사용액", "이번 달 일별 사용액"])
def test_unresolved_grain_or_period_is_ambiguous(question):
    """질문의 단위나 기간이 정해지지 않으면 추정하지 않음."""
    with pytest.raises(PolicyError, match="AMBIGUOUS_REQUEST"):
        route_question(question, _BASE, _COVERAGE, _QUERIES)


@pytest.mark.parametrize("question", ["최근 7개월 카드별 월별 사용액", "활성 카드별 실제 청구 연회비"])
def test_out_of_scope_questions_are_rejected(question):
    """지원 밖 기간이나 실제 청구 요청을 제공 범위로 몰래 바꾸지 않음."""
    with pytest.raises(PolicyError, match="UNSUPPORTED_REQUEST"):
        route_question(question, _BASE, _COVERAGE, _QUERIES)


def test_midmonth_coverage_has_different_monthly_and_fee_start():
    """월별 뷰의 월초 절삭과 6개월 뷰의 실제 시작일 차이를 보존함."""
    coverage = date(2026, 7, 15)
    assert view_period("app.monthly_usage", _BASE, coverage)[0] == date(2026, 7, 1)
    assert view_period("app.card_fee_usage_6m", _BASE, coverage)[0] == coverage


def test_completed_month_window_excludes_unfinished_month():
    """연체 이력의 완료월 경계를 월중과 월말로 구분함."""
    assert view_period("app.customer_delinquency_history", _BASE, _COVERAGE) == (date(2025, 10, 1), date(2026, 9, 30))
    assert view_period("app.customer_delinquency_history", date(2026, 10, 31), _COVERAGE)[1] == date(2026, 10, 31)


@pytest.mark.parametrize("field,value", [
    ("reason_code", "UNSUPPORTED"), ("grain", "aggregate"),
    ("period_start", "2026-09-01"), ("selected_columns", ["listed_annual_fee"]),
])
def test_fee_plan_cannot_drop_conditions(field, value):
    """Q-08의 상태 쌍, 단위, 범위, 시행일 출력을 변경한 계획을 거부함."""
    slots, plan = route_question(FIXED_QUESTIONS["Q-08"], _BASE, _COVERAGE, _QUERIES)
    broken = deepcopy(plan)
    broken[field] = value
    with pytest.raises(PolicyError):
        validate_plan(broken, slots, _CATALOG, _BASE, _COVERAGE)


def test_model_cannot_shorten_an_explicit_period():
    """명시된 기간이 제공 창보다 넓으면 축소한 모델 계획도 거부함."""
    slots, _ = route_question("2026-01-01 ~ 2026-10-21 카드별 월별 사용액", _BASE, _COVERAGE, _QUERIES)
    _, plan = route_question(FIXED_QUESTIONS["Q-03"], _BASE, _COVERAGE, _QUERIES)
    with pytest.raises(PolicyError, match="UNSUPPORTED_REQUEST"):
        validate_plan(plan, slots, _CATALOG, _BASE, _COVERAGE)
