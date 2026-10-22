"""모델 계획이 질문에서 확인한 조건과 공개 뷰 범위를 지키는지 검사함."""
import calendar
import re
from datetime import date

from .errors import PolicyError
from .routing import VIEW_GRAINS, view_period

_DOMAINS = {
    "profile": {"app.customer_profile"}, "cards": {"app.customer_cards"},
    "delinquency": {"app.customer_delinquency", "app.customer_delinquency_history"},
    "usage": {"app.monthly_usage", "app.merchant_usage", "app.customer_daily_usage", "app.card_fee_usage_6m"},
}


def validate_plan(plan: dict, slots: dict, catalog: dict, base_date: date, coverage_start: date) -> None:
    """상태, 기간, 지표, 단위를 검증하고 위반 시 PolicyError를 발생시킴.

    SQL 문법 및 선택 순서의 실제 일치는 별도 SQL 검증기가 확인함.
    이 함수가 통과해도 SQL 실행을 허가한 것은 아님.
    """
    pairs = {"ready": "OK", "unsupported": "UNSUPPORTED", "ambiguous": "AMBIGUOUS"}
    status = plan.get("status")
    if status not in pairs or plan.get("reason_code") != pairs[status]:
        raise PolicyError("INVALID_MODEL_OUTPUT")
    if status != "ready":
        if any(plan.get(key) is not None for key in ("view", "sql", "grain", "period_start", "period_end")):
            raise PolicyError("INVALID_MODEL_OUTPUT")
        if plan.get("selected_columns") != []:
            raise PolicyError("INVALID_MODEL_OUTPUT")
        raise PolicyError("UNSUPPORTED_REQUEST" if status == "unsupported" else "AMBIGUOUS_REQUEST")
    if slots.get("ambiguous"):
        raise PolicyError("AMBIGUOUS_REQUEST")
    view = plan.get("view")
    if view not in catalog or view not in VIEW_GRAINS:
        raise PolicyError("UNSUPPORTED_REQUEST")
    if slots.get("domain") not in _DOMAINS or view not in _DOMAINS[slots["domain"]]:
        raise PolicyError("UNSUPPORTED_REQUEST")
    if not isinstance(plan.get("sql"), str) or not plan["sql"].strip():
        raise PolicyError("INVALID_MODEL_OUTPUT")
    grain = plan.get("grain")
    if grain not in {VIEW_GRAINS[view], "aggregate"} or grain != slots.get("grain"):
        raise PolicyError("UNSUPPORTED_REQUEST")
    selected = plan.get("selected_columns")
    if (not isinstance(selected, list) or not selected or
            any(not isinstance(column, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", column) for column in selected) or
            len(set(selected)) != len(selected)):
        raise PolicyError("INVALID_MODEL_OUTPUT")
    available = {column["name"] for column in catalog[view]["columns"]}
    aggregating = slots.get("intent") in {"sum", "avg", "min", "max", "count"}
    if grain != "aggregate" and not aggregating and not set(selected) <= available:
        raise PolicyError("UNSUPPORTED_REQUEST")
    metrics = set(slots.get("metrics", []))
    if not (metrics - {"card_count"}) <= available:
        raise PolicyError("UNSUPPORTED_REQUEST")
    if grain != "aggregate" and not aggregating and not metrics <= set(selected):
        raise PolicyError("UNSUPPORTED_REQUEST")
    if "card_count" in metrics and (view != "app.customer_cards" or selected != ["card_count"]):
        raise PolicyError("UNSUPPORTED_REQUEST")
    for item in slots.get("filters", []):
        if item.get("column") not in available:
            raise PolicyError("UNSUPPORTED_REQUEST")
    if slots.get("status_filter") is not None and "current_status" not in available:
        raise PolicyError("UNSUPPORTED_REQUEST")
    start_text, end_text = plan.get("period_start"), plan.get("period_end")
    if (start_text is None) != (end_text is None):
        raise PolicyError("INVALID_MODEL_OUTPUT")
    lower, upper = view_period(view, base_date, coverage_start)
    if lower is None:
        if start_text is not None or slots.get("period_start") is not None:
            raise PolicyError("UNSUPPORTED_REQUEST")
    else:
        if slots.get("period_start") is None or slots.get("period_end") is None:
            raise PolicyError("AMBIGUOUS_REQUEST")
        if start_text is None:
            raise PolicyError("UNSUPPORTED_REQUEST")
        if not isinstance(start_text, str) or not isinstance(end_text, str):
            raise PolicyError("INVALID_MODEL_OUTPUT")
        if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in (start_text, end_text)):
            raise PolicyError("INVALID_MODEL_OUTPUT")
        try:
            start, end = date.fromisoformat(start_text), date.fromisoformat(end_text)
        except ValueError:
            raise PolicyError("INVALID_MODEL_OUTPUT") from None
        if start > end or start < lower or upper is None or end > upper:
            raise PolicyError("UNSUPPORTED_REQUEST")
        # 이미 월 단위로 집계된 뷰에 월 문자열만 걸러 일 단위 기간인 것처럼 표시하지 않음.
        if view in {"app.monthly_usage", "app.merchant_usage"}:
            is_month_end = end.day == calendar.monthrange(end.year, end.month)[1]
            if (start.day != 1 and start != lower) or (not is_month_end and end != upper):
                raise PolicyError("UNSUPPORTED_REQUEST")
        # 집계된 6개월 뷰는 날짜 필터를 추가해도 이미 합산된 금액을 다시 나눌 수 없음.
        if view == "app.card_fee_usage_6m" and (start, end) != (lower, upper):
            raise PolicyError("UNSUPPORTED_REQUEST")
    for key in ("period_start", "period_end"):
        if slots.get(key) is not None and plan.get(key) != slots[key]:
            raise PolicyError("UNSUPPORTED_REQUEST")
    if view == "app.card_fee_usage_6m":
        required = {"usage_period_start", "usage_period_end", "product_effective_date", "is_product_effective_on_base_date"}
        if grain == "aggregate" or not required <= set(selected):
            raise PolicyError("UNSUPPORTED_REQUEST")
