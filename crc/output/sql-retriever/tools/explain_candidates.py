#!/usr/bin/env python3
"""직접 작성한 고정 조회 7개의 실행 계획만 확인함. 실제 조회 실행 없음."""

import argparse
from datetime import datetime, timezone

from read_views import BASE_DIR, CRC_DIR, Reader, SafeError, load_env, write_json


CANDIDATES = [
    {"id": "Q-01", "description": "회원 가입일과 연령대 조회", "result_grain": "요청 회원 1명", "view": "app.customer_profile",
     "sql": "SELECT join_date, age_band FROM app.customer_profile LIMIT 1;"},
    {"id": "Q-02", "description": "보유 카드와 연회비 조회", "result_grain": "카드 1개", "view": "app.customer_cards",
     "sql": "SELECT card_ref, product_id, product_name, brand, issue_date, current_status, product_effective_date, is_product_effective_on_base_date, annual_fee FROM app.customer_cards ORDER BY card_ref LIMIT 200;"},
    {"id": "Q-03", "description": "최근 6개월 카드별 사용액과 건수 조회", "result_grain": "카드 1개와 월 1개", "view": "app.monthly_usage",
     "sql": "SELECT card_ref, month, period_start, period_end, approved_amount, transaction_count FROM app.monthly_usage ORDER BY month DESC, card_ref LIMIT 200;"},
    {"id": "Q-04", "description": "기준일 이전 완료월의 최신 연체 현황 조회", "result_grain": "요청 회원의 최신 완료월 1개", "view": "app.customer_delinquency",
     "sql": "SELECT base_month, as_of_date, overdue_amount, overdue_days, overdue_count_12m FROM app.customer_delinquency ORDER BY base_month DESC LIMIT 1;"},
    {"id": "Q-05", "description": "최근 6개월 가맹점과 업종별 사용액 조회", "result_grain": "카드 1개, 월 1개, 가맹점 1개", "view": "app.merchant_usage",
     "sql": "SELECT card_ref, month, period_start, period_end, merchant_name, category, first_approved_date, last_approved_date, approved_amount, transaction_count FROM app.merchant_usage ORDER BY month DESC, approved_amount DESC, card_ref, merchant_name, category LIMIT 200;"},
    {"id": "Q-06", "description": "최근 12개 완료월의 연체 이력 조회", "result_grain": "요청 회원과 완료월 1개", "view": "app.customer_delinquency_history",
     "sql": "SELECT base_month, as_of_date, overdue_amount, overdue_days, overdue_count_12m FROM app.customer_delinquency_history ORDER BY base_month DESC LIMIT 12;"},
    {"id": "Q-07", "description": "최근 최대 90일의 일별 사용액 조회", "result_grain": "요청 회원과 날짜 1개", "view": "app.customer_daily_usage",
     "sql": "SELECT usage_date, approved_amount, transaction_count FROM app.customer_daily_usage ORDER BY usage_date DESC LIMIT 90;"},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user")
    parser.add_argument("--allow-admin-inspection", action="store_true")
    args = parser.parse_args()
    config = load_env(CRC_DIR / "secrets/.env")
    if args.user:
        config["RDB_USER"] = args.user
    reader = Reader(config, args.allow_admin_inspection)
    # 검증용 가상 범위임. 실제 회원 ID나 DB 데이터에서 읽은 기준일이 아님.
    scope = (
        "SET LOCAL app.member_id = '__catalog_probe__';\n"
        "SET LOCAL app.base_date = '2026-10-15';\n"
        "SET LOCAL app.coverage_start = '2026-05-01';\n"
    )
    results = []
    for candidate in CANDIDATES:
        result = dict(candidate, decision_status="proposed")
        try:
            result["explain_plan"] = reader.json_query(scope + "EXPLAIN (FORMAT JSON, ANALYZE FALSE) " + candidate["sql"])
            result["explain_status"] = "passed"
        except SafeError as exc:
            result["explain_status"] = "failed"
            result["error_code"] = exc.code
        results.append(result)
    passed = sum(item["explain_status"] == "passed" for item in results)
    write_json(BASE_DIR / "work/fixed_query_candidates.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(), "inspection_role": config["RDB_USER"],
        "admin_inspection_authorized": args.allow_admin_inspection,
        "business_data_rows_read": 0, "explain_analyze": False,
        "scope_values_are_synthetic": True, "actual_runtime_cost_verified": False,
        "user_selection_pending": True, "passed": passed, "total": len(results), "queries": results,
    })
    print(f"EXPLAIN: {passed}/{len(results)} 통과. 실제 조회 실행 없음. 사용자 선택 대기 중.")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SafeError as exc:
        print(f"중단: {exc.code} / {exc}")
        raise SystemExit(2)
