"""재적재된 교육용 DB에서 Q-08과 회원 범위를 확인한다."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
RDB = HERE.parents[2] / "rdb"
Q08 = (HERE / "Q-08.sql").read_text(encoding="utf-8")


def query(sql: str) -> list[list[str]]:
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T", "rdb", "psql", "-qAt",
            "-F", "|", "-v", "ON_ERROR_STOP=1", "-U", "cardlab", "-d", "cardlab",
        ],
        input=sql,
        text=True,
        capture_output=True,
        cwd=RDB,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return [line.split("|") for line in result.stdout.splitlines() if line]


def scoped(member: str, coverage: str, statement: str, role: str = "cardlab") -> list[list[str]]:
    assert member in {"M-6074", "M-6001"}
    assert coverage in {"2025-08-01", "2026-08-15", "2026-09-01"}
    assert role in {"cardlab", "sql_retriever_user"}
    prefix = "SET ROLE sql_retriever_user;\n" if role == "sql_retriever_user" else ""
    return query(
        prefix
        + "BEGIN READ ONLY;\n"
        + f"SET LOCAL app.member_id='{member}';\n"
        + "SET LOCAL app.base_date='2026-08-31';\n"
        + f"SET LOCAL app.coverage_start='{coverage}';\n"
        + statement
        + "\nCOMMIT;\n"
    )


def raw_approved(member: str, start: str) -> list[list[str]]:
    assert member in {"M-6074", "M-6001"}
    assert start in {"2026-03-01", "2026-08-15"}
    return query(
        "SELECT c.card_id, coalesce(sum(t.amount),0), count(t.txn_id) "
        "FROM public.card c LEFT JOIN public.card_txn t "
        "ON t.card_id=c.card_id AND t.approval_code='APPROVED' "
        f"AND t.txn_date BETWEEN DATE '{start}' AND DATE '2026-08-31' "
        f"WHERE c.member_id='{member}' GROUP BY c.card_id ORDER BY c.card_id;\n"
    )


def main() -> None:
    catalog = query(
        "SELECT pg_get_userbyid(c.relowner), c.reloptions::text "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='app' AND c.relname='card_fee_usage_6m';\n"
    )
    assert len(catalog) == 1 and catalog[0][0] == "app_reader"
    assert "security_barrier=true" in catalog[0][1]

    no_scope = query("SELECT count(*) FROM app.card_fee_usage_6m;\n")
    assert no_scope == [["0"]], no_scope
    restricted_no_scope = query(
        "SET ROLE sql_retriever_user; "
        "SELECT count(*) FROM app.card_fee_usage_6m;\n"
    )
    assert restricted_no_scope == [["0"]], restricted_no_scope

    statement = (
        "SELECT card_ref, listed_annual_fee, product_effective_date, "
        "is_product_effective_on_base_date, usage_period_start, usage_period_end, "
        "approved_amount_6m, transaction_count_6m, covered_month_count, "
        "active_month_count FROM app.card_fee_usage_6m ORDER BY card_ref;"
    )
    baseline = scoped("M-6074", "2025-08-01", statement)
    assert len(baseline) == 2, baseline
    assert [row[1] for row in baseline] == ["32000", "52000"]
    assert [row[6] for row in baseline] == ["273405", "262573"]
    assert all(row[2] == "2027-01-15" and row[3] == "f" for row in baseline)
    assert all(row[4:6] == ["2026-03-01", "2026-08-31"] for row in baseline)
    assert all(row[8] == "6" for row in baseline)
    baseline_raw = raw_approved("M-6074", "2026-03-01")
    assert all(row[6:8] == expected[1:3]
               for row, expected in zip(baseline, baseline_raw))

    admin_q08 = scoped("M-6074", "2025-08-01", Q08, "cardlab")
    restricted = scoped("M-6074", "2025-08-01", Q08, "sql_retriever_user")
    assert len(restricted) == 2, restricted
    assert admin_q08 == restricted
    assert [row[0] for row in restricted] == ["CARD_002", "CARD_001"]

    narrow = scoped("M-6074", "2026-08-15", statement)
    raw = raw_approved("M-6074", "2026-08-15")
    assert len(narrow) == len(raw) == 2
    for actual, expected in zip(narrow, raw):
        assert actual[4:6] == ["2026-08-15", "2026-08-31"]
        assert actual[6:8] == expected[1:3], (actual, expected)
        assert actual[8] == "1"

    unavailable = scoped("M-6074", "2026-09-01", statement)
    assert len(unavailable) == 2
    assert all(row[4:] == ["", "", "", "", "", ""] for row in unavailable)

    other_member = scoped("M-6001", "2025-08-01", statement)
    other_raw = raw_approved("M-6001", "2026-03-01")
    assert len(other_member) == 2
    assert [row[6] for row in other_member] != [row[6] for row in baseline]
    assert all(row[6:8] == expected[1:3]
               for row, expected in zip(other_member, other_raw))

    privileges = query(
        "SELECT has_table_privilege('sql_retriever_user', 'app.card_fee_usage_6m', 'SELECT'), "
        "has_table_privilege('sql_retriever_user', 'public.card_txn', 'SELECT'), "
        "has_schema_privilege('sql_retriever_user', 'private', 'USAGE');\n"
    )
    assert privileges == [["t", "f", "f"]], privileges

    result = {
        "status": "passed",
        "checks": [
            "view_owner_and_security_barrier", "default_deny_without_scope_both_roles",
            "two_cards_and_future_effective_date", "q08_under_cardlab_and_restricted_role",
            "midmonth_coverage_matches_raw_approved_transactions",
            "unavailable_period_is_null", "both_members_match_independent_raw_aggregates",
            "restricted_role_has_view_only",
        ],
        "sample_member": "M-6074",
        "sample_rows": baseline,
        "q08_row_count": len(restricted),
        "midmonth_rows": narrow,
    }
    path = HERE / "db_validation.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "checks": len(result["checks"]),
                      "q08_rows": len(restricted)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
