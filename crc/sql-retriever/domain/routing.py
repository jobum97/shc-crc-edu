"""확인된 질문 조건을 추출하고 완전히 같은 조회만 고정 SQL로 연결함."""
import calendar
import re
from datetime import date, timedelta

from .errors import PolicyError

FIXED_QUESTIONS = {
    "Q-01": "가입일과 연령대",
    "Q-02": "전체 보유 카드와 상품 안내 연회비",
    "Q-03": "최근 6개월 카드별 월별 승인 사용액과 거래 건수",
    "Q-04": "최신 완료월 연체 현황",
    "Q-05": "최근 6개월 카드별 월별 가맹점별 승인 사용액과 거래 건수",
    "Q-06": "최근 12개 완료월 연체 이력",
    "Q-07": "최근 90일 일별 승인 사용액과 거래 건수",
    "Q-08": "활성 카드별 최근 6개월 자료상 연회비와 승인 사용액 및 거래 건수",
}
_ALIASES = {
    "Q-01": ["회원 가입일과 연령대", "가입일 및 연령대"],
    "Q-02": ["전체 보유 카드 목록과 상품 안내 연회비", "보유 카드 목록과 상품 안내 연회비"],
    "Q-03": ["최근 6개 달 카드별 월별 승인금액과 거래건수"],
    "Q-04": ["최신 완료월 연체금액과 연체일수 및 12개월 연체횟수"],
    "Q-05": ["최근 6개 달 카드별 월별 가맹점별 승인금액과 거래건수"],
    "Q-06": ["최근 12개월 완료월별 연체 이력"],
    "Q-07": ["최근 90일 회원 일별 승인금액과 거래건수"],
    "Q-08": ["최근 6개월 활성 카드별 자료상 연회비와 승인 사용액 및 거래 건수"],
}
_FIXED_METRICS = {
    "Q-01": ["join_date", "age_band"], "Q-02": ["annual_fee"],
    "Q-03": ["approved_amount", "transaction_count"],
    "Q-04": ["overdue_amount", "overdue_days", "overdue_count_12m"],
    "Q-05": ["approved_amount", "transaction_count"],
    "Q-06": ["overdue_amount", "overdue_days", "overdue_count_12m"],
    "Q-07": ["approved_amount", "transaction_count"],
    "Q-08": ["listed_annual_fee", "approved_amount_6m", "transaction_count_6m"],
}
VIEW_GRAINS = {
    "app.customer_profile": "profile", "app.customer_cards": "card",
    "app.monthly_usage": "card_month", "app.customer_delinquency": "month",
    "app.merchant_usage": "merchant_month", "app.customer_delinquency_history": "month",
    "app.customer_daily_usage": "day", "app.card_fee_usage_6m": "card",
}


def _month_shift(value: date, count: int) -> date:
    number = value.year * 12 + value.month - 1 + count
    year, month = divmod(number, 12)
    return date(year, month + 1, 1)


def view_period(view: str, base_date: date, coverage_start: date) -> tuple[date | None, date | None]:
    """DB 논리뷰의 최대 조회 창을 반환하며 카드별 발급일은 개별 행에서 확인함."""
    if view in {"app.customer_profile", "app.customer_cards", "app.customer_delinquency"}:
        return None, None
    if view == "app.customer_delinquency_history":
        last = date(base_date.year, base_date.month, calendar.monthrange(base_date.year, base_date.month)[1])
        end = base_date if base_date == last else base_date.replace(day=1) - timedelta(days=1)
        return _month_shift(end, -11), end
    if view == "app.customer_daily_usage":
        return max(base_date - timedelta(days=89), coverage_start), base_date
    if view in {"app.monthly_usage", "app.merchant_usage", "app.card_fee_usage_6m"}:
        coverage = coverage_start.replace(day=1) if view == "app.monthly_usage" else coverage_start
        return max(_month_shift(base_date, -5), coverage), base_date
    raise PolicyError("UNSUPPORTED_REQUEST")


def _normalize(question: str) -> str:
    value = re.sub(r"^(?:나의|내)\s+", "", question.strip())
    value = re.sub(r"\s*(?:보여\s*줘|보여\s*주세요|알려\s*줘|알려\s*주세요|조회해\s*줘|조회해\s*주세요)[.!?]*$", "", value)
    return re.sub(r"\s+", "", value).rstrip(".!?")


def _slots() -> dict:
    return {"domain": None, "metrics": [], "period_start": None, "period_end": None,
            "grain": None, "filters": [], "ambiguous": False, "intent": None, "status_filter": None}


def _domain(view: str) -> str:
    if view == "app.customer_profile":
        return "profile"
    if "delinquency" in view:
        return "delinquency"
    return "cards" if view == "app.customer_cards" else "usage"


def route_question(question: str, base_date: date, coverage_start: date, queries: dict) -> tuple[dict, dict | None]:
    """완전 일치한 질문의 계획 또는 확인된 슬롯과 None을 반환함.

    문장 일부가 맞는다는 이유로 필터나 지표를 버리지 않음. 기간 없는 Q-04는 null 기간을 유지함.
    동등한 조회가 여럿이거나 해석 불가하면 AMBIGUOUS_REQUEST를 발생시킴.
    """
    slots = _slots()
    normalized = _normalize(question)
    matches = [qid for qid, phrase in FIXED_QUESTIONS.items()
               if normalized in {_normalize(p) for p in [phrase, *_ALIASES[qid]]}]
    if len(matches) > 1:
        raise PolicyError("AMBIGUOUS_REQUEST")
    if matches:
        qid = matches[0]
        entry = queries[qid]
        start, end = view_period(entry["view"], base_date, coverage_start)
        slots.update(domain=_domain(entry["view"]), metrics=_FIXED_METRICS[qid].copy(),
                     grain=entry["grain"], intent=qid, period_start=start.isoformat() if start else None,
                     period_end=end.isoformat() if end else None)
        if qid == "Q-08":
            slots["status_filter"] = "ACTIVE"
            slots["filters"] = [{"column": "current_status", "operator": "=", "value": "ACTIVE"}]
        columns = re.split(r"\bFROM\b", entry["sql"], flags=re.I)[0]
        columns = re.sub(r"^\s*SELECT\s+", "", columns, flags=re.I)
        plan = {"status": "ready", "reason_code": "OK", "view": entry["view"], "sql": entry["sql"],
                "grain": entry["grain"], "period_start": slots["period_start"], "period_end": slots["period_end"],
                "selected_columns": [c.strip() for c in columns.split(",")], "route": "fixed", "query_id": qid}
        return slots, plan
    if re.search(r"청구|혜택|손익|해지|추천|예측|취소\s*거래|거절\s*거래|원본|전체\s*기간|평생", question):
        raise PolicyError("UNSUPPORTED_REQUEST")
    if re.search(r"지난번|언젠가|어떤가|비교|이전보다|그\s*카드|특정\s*카드", question):
        raise PolicyError("AMBIGUOUS_REQUEST")
    slots["intent"] = "lookup"
    if "연체" in question:
        slots["domain"] = "delinquency"
        slots["grain"] = "month"
        for token, column in [("금액", "overdue_amount"), ("일수", "overdue_days"), ("횟수", "overdue_count_12m")]:
            if token in question:
                slots["metrics"].append(column)
    elif "가입일" in question or "연령대" in question:
        slots["domain"], slots["grain"] = "profile", "profile"
        slots["metrics"] = [column for token, column in [("가입일", "join_date"), ("연령대", "age_band")]
                            if token in question]
    elif re.search(r"사용액|이용액|승인액|승인금액|거래\s*건수|사용\s*금액", question):
        slots["domain"] = "usage"
        if re.search(r"사용액|이용액|승인액|승인금액|사용\s*금액", question):
            slots["metrics"].append("approved_amount")
        if re.search(r"거래\s*건수", question):
            slots["metrics"].append("transaction_count")
        if "가맹점별" in normalized:
            slots["grain"] = "merchant_month" if "월별" in normalized else None
        elif "일별" in normalized or "날짜별" in normalized:
            slots["grain"] = "day"
        elif "카드별" in normalized and "월별" in normalized:
            slots["grain"] = "card_month"
        elif "카드별" in normalized:
            slots["grain"] = "card"
        if "연회비" in question:
            slots["metrics"] = [m + "_6m" for m in slots["metrics"]] + ["listed_annual_fee"]
    elif "카드" in question:
        slots["domain"] = "cards"
        if re.search(r"카드\s*(?:수|개수)|몇\s*(?:개|장)", question):
            slots["grain"], slots["intent"], slots["metrics"] = "aggregate", "count", ["card_count"]
        elif "목록" in question or "카드별" in normalized:
            slots["grain"] = "card"
        if "연회비" in question:
            slots["metrics"].append("annual_fee")
    if re.search(r"비활성|해지된", question):
        raise PolicyError("UNSUPPORTED_REQUEST")
    if "활성" in question:
        slots["status_filter"] = "ACTIVE"
        slots["filters"] = [{"column": "current_status", "operator": "=", "value": "ACTIVE"}]
    # 기간의 '최대 6개월'과 금액의 '최대'를 구별하여 집계 방식을 보존함.
    aggregate_text = re.sub(r"최대\s*\d+\s*(?:개월|개\s*달|일|개\s*완료월)", "", question)
    operations = [intent for pattern, intent in [(r"합계|총액|총합", "sum"), (r"평균", "avg"),
                                                 (r"최대|최댓값", "max"), (r"최소|최솟값", "min")]
                  if re.search(pattern, aggregate_text)]
    if len(operations) > 1 or (operations and slots["intent"] == "count"):
        raise PolicyError("AMBIGUOUS_REQUEST")
    if operations:
        if not re.search(r"카드별|월별|가맹점별|일별|날짜별", normalized):
            slots["grain"] = "aggregate"
        slots["intent"] = operations[0]
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", question)
    if dates:
        if len(dates) != 2:
            raise PolicyError("AMBIGUOUS_REQUEST")
        try:
            start, end = (date.fromisoformat(v) for v in dates)
        except ValueError:
            raise PolicyError("UNSUPPORTED_REQUEST") from None
        if start > end or end > base_date:
            raise PolicyError("UNSUPPORTED_REQUEST")
        slots.update(period_start=start.isoformat(), period_end=end.isoformat())
    else:
        months = re.search(r"최근\s*(\d+)\s*(?:개월|개\s*달|개\s*완료월)", question)
        days = re.search(r"최근\s*(\d+)\s*일", question)
        if months and days:
            raise PolicyError("AMBIGUOUS_REQUEST")
        if months:
            count = int(months.group(1))
            if not 1 <= count <= (12 if slots["domain"] == "delinquency" else 6):
                raise PolicyError("UNSUPPORTED_REQUEST")
            if slots["domain"] == "delinquency":
                _, end = view_period("app.customer_delinquency_history", base_date, coverage_start)
            else:
                end = base_date
            assert end is not None
            start = _month_shift(end, -(count - 1))
            slots.update(period_start=start.isoformat(), period_end=end.isoformat())
        elif days:
            count = int(days.group(1))
            if not 1 <= count <= 90:
                raise PolicyError("UNSUPPORTED_REQUEST")
            slots.update(period_start=(base_date - timedelta(days=count - 1)).isoformat(), period_end=base_date.isoformat())
        elif re.search(r"어제|오늘|지난|이번|작년|올해|\d+\s*(?:월|일|년|개월)", question):
            raise PolicyError("AMBIGUOUS_REQUEST")
    if slots["domain"] is None or slots["grain"] is None:
        raise PolicyError("AMBIGUOUS_REQUEST")
    return slots, None
