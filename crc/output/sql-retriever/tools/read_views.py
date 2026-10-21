#!/usr/bin/env python3
"""업무 데이터 행을 읽지 않고 PostgreSQL 뷰 구조와 계정 권한 확인.

Python 표준 라이브러리와 설치된 psql만 사용함. .env를 수정하지 않음.
사용 예: python3 tools/read_views.py --check-config
실제 조사: python3 tools/read_views.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
CRC_DIR = BASE_DIR.parents[1]
REQUIRED = ("RDB_TYPE", "RDB_HOST", "RDB_PORT", "RDB_DATABASE", "RDB_USER", "RDB_PASSWORD")
# 제공된 View SQL에서 cardlab을 관리 계정으로 명시함. 접속 전에 차단함.
KNOWN_ADMIN_ROLES = {"postgres", "cardlab"}
MAX_CONNECTION_FAILURES = 3


class SafeError(Exception):
    """비밀값과 원본 DB 오류를 포함하지 않는 오류."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_env(path: Path) -> dict[str, str]:
    """필요한 DB 키만 해석함. 쉘 실행과 환경변수 치환을 하지 않음."""
    if not path.is_file():
        raise SafeError("CONFIG_MISSING", "설정 파일이 없음")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match or match[1] not in REQUIRED:
            continue
        key, value = match.groups()
        if key in values:
            raise SafeError("CONFIG_DUPLICATE", f"DB 설정 키가 중복됨: {key}")
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.rfind(quote)
            if end == 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise SafeError("CONFIG_QUOTE", f"설정 따옴표 확인 필요: {key}")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if "\x00" in value or "\n" in value or "\r" in value:
            raise SafeError("CONFIG_INVALID", f"한 줄 값만 허용함: {key}")
        values[key] = value
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise SafeError("CONFIG_INCOMPLETE", "미입력 키: " + ", ".join(missing))
    if values["RDB_TYPE"].lower() not in {"postgresql", "postgres"}:
        raise SafeError("DB_TYPE_UNSUPPORTED", "이 스크립트는 PostgreSQL용임")
    if not values["RDB_PORT"].isdigit() or not 1 <= int(values["RDB_PORT"]) <= 65535:
        raise SafeError("CONFIG_PORT", "DB 포트는 1부터 65535까지의 정수여야 함")
    # libpq의 dbname 연결 문자열 해석으로 승인한 접속 대상이 바뀌는 경우 방지.
    if "=" in values["RDB_DATABASE"] or "://" in values["RDB_DATABASE"]:
        raise SafeError("CONFIG_DATABASE", "RDB_DATABASE에는 연결 문자열이 아닌 DB 이름만 입력 필요")
    return values


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


ROLE_GUARD = r"""
SELECT EXISTS (
  SELECT 1 FROM pg_catalog.pg_roles r
  WHERE (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls)
    AND (r.rolname = current_user OR pg_catalog.pg_has_role(current_user, r.oid, 'MEMBER'))
) AS unsafe_role
\gset
\if :unsafe_role
\echo __ADMIN_ROLE_REJECTED__
\quit
\endif
"""


def classify_error(stderr: str, returncode: int) -> SafeError:
    """stderr 원문을 출력하거나 저장하지 않음."""
    lower = stderr.lower()
    checks = (
        ("password authentication failed", "CONNECT_AUTH", "실행 계정의 비밀번호 확인 필요"),
        ("no pg_hba.conf entry", "CONNECT_POLICY", "DB 접속 허용 정책 확인 필요"),
        ("connection refused", "CONNECT_REFUSED", "DB 실행 상태와 호스트 및 포트 확인 필요"),
        ("could not translate host name", "CONNECT_HOST", "DB 호스트 이름 확인 필요"),
        ("operation not permitted", "CONNECT_NETWORK", "현재 실행 환경의 네트워크 접근 제한 확인 필요"),
        ("timeout expired", "CONNECT_TIMEOUT", "DB 접속 제한 시간 초과"),
    )
    for token, code, message in checks:
        if token in lower:
            return SafeError(code, message)
    state = re.search(r"(?:ERROR|FATAL):\s+([0-9A-Z]{5})\b", stderr)
    if state:
        return SafeError("SQLSTATE_" + state[1], "DB가 메타데이터 또는 접근 확인 요청을 거부함")
    if returncode == 2:
        return SafeError("CONNECT_FAILED", "DB 접속 실패. 접속 설정 확인 필요")
    return SafeError("DB_CHECK_FAILED", "DB 구조 확인 실패. 원본 오류와 접속 정보는 출력하지 않음")


class Reader:
    def __init__(self, config: dict[str, str], allow_admin_inspection: bool = False):
        self.allow_admin_inspection = allow_admin_inspection
        if config["RDB_USER"].lower() in KNOWN_ADMIN_ROLES and not allow_admin_inspection:
            raise SafeError("ADMIN_ACCOUNT_BLOCKED", "알려진 관리 계정임. 비관리자 NL2SQL 실행 계정 필요")
        self.psql = shutil.which("psql")
        if not self.psql:
            raise SafeError("PSQL_MISSING", "psql 실행 파일이 없음. 설치 전 사용자 승인 필요")
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
        self.env.update({
            "PGHOST": config["RDB_HOST"], "PGPORT": config["RDB_PORT"],
            "PGDATABASE": config["RDB_DATABASE"], "PGUSER": config["RDB_USER"],
            "PGPASSWORD": config["RDB_PASSWORD"], "PGCONNECT_TIMEOUT": "5",
            "PGAPPNAME": "sql_retriever_catalog_readonly", "LC_ALL": "C",
            "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=5000 "
                         "-c lock_timeout=1000 -c search_path=pg_catalog",
        })

    def query(self, sql: str) -> str:
        # 새 연결마다 읽기 전용 설정과 관리자 역할 검사를 반복함.
        request = (
            "\\set VERBOSITY sqlstate\n"
            "SET default_transaction_read_only = on;\n"
            "BEGIN READ ONLY;\n" + ("" if self.allow_admin_inspection else ROLE_GUARD) + "\n" + sql + "\nROLLBACK;\n"
        )
        try:
            result = subprocess.run(
                [self.psql, "-X", "-q", "-t", "-A", "-w", "-v", "ON_ERROR_STOP=1"],
                input=request, text=True, capture_output=True, env=self.env, timeout=15,
            )
        except subprocess.TimeoutExpired:
            raise SafeError("DB_PROCESS_TIMEOUT", "DB 구조 확인 제한 시간 초과") from None
        if "__ADMIN_ROLE_REJECTED__" in result.stdout:
            raise SafeError("ADMIN_PRIVILEGE_BLOCKED", "관리 권한 또는 관리 역할 멤버십 확인됨. 다른 실행 계정 필요")
        if result.returncode:
            raise classify_error(result.stderr, result.returncode)
        return result.stdout.strip()

    def json_query(self, sql: str):
        try:
            return json.loads(self.query(sql))
        except json.JSONDecodeError:
            raise SafeError("CATALOG_FORMAT", "DB 메타데이터 응답 형식 확인 필요") from None


RELATION_FILTER = "n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'"
VIEW_FILTER = (
    "c.relkind IN ('v', 'm') AND " + RELATION_FILTER +
    " AND pg_catalog.has_schema_privilege(current_user, n.oid, 'USAGE')"
    " AND pg_catalog.has_table_privilege(current_user, c.oid, 'SELECT')"
)


def source_metadata(path: Path) -> dict:
    if not path.is_file():
        raise SafeError("VIEW_SQL_MISSING", "지정한 View SQL 파일이 없음")
    source = path.read_text(encoding="utf-8-sig")
    # 소스에 계정 생성문이 있으므로 전체 SQL과 비밀번호 리터럴을 산출물에 복사하지 않음.
    pattern = r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)"
    views = sorted({f"{schema}.{name}" for schema, name in re.findall(pattern, source, re.I)})
    variables = sorted(set(re.findall(r"current_setting\(\s*'([^']+)'", source, re.I)))
    return {"path": str(path.resolve()), "declared_views": views, "session_variables": variables}


def inspect_catalog(reader: Reader, source: dict) -> dict:
    role = reader.json_query("""
SELECT pg_catalog.json_build_object(
 'name', r.rolname, 'rolsuper', r.rolsuper, 'rolcreaterole', r.rolcreaterole,
 'rolcreatedb', r.rolcreatedb, 'rolbypassrls', r.rolbypassrls,
 'default_transaction_read_only', current_setting('default_transaction_read_only'),
 'transaction_read_only', current_setting('transaction_read_only'),
 'server_version', current_setting('server_version'))
FROM pg_catalog.pg_roles r WHERE r.rolname = current_user;
""")
    counted = reader.json_query(f"""
SELECT pg_catalog.count(*) FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE {VIEW_FILTER};
""")
    views = reader.json_query(f"""
SELECT coalesce(pg_catalog.json_agg(v ORDER BY v.schema, v.name), '[]'::json)
FROM (
 SELECT n.nspname AS schema, c.relname AS name,
   pg_catalog.pg_get_userbyid(c.relowner) AS owner, c.relkind AS kind,
   c.reloptions AS options, pg_catalog.obj_description(c.oid, 'pg_class') AS description,
   (SELECT coalesce(pg_catalog.json_agg(pg_catalog.json_build_object(
      'name', a.attname, 'type', pg_catalog.format_type(a.atttypid, a.atttypmod),
      'position', a.attnum, 'not_null', a.attnotnull,
      'description', pg_catalog.col_description(c.oid, a.attnum)) ORDER BY a.attnum), '[]'::json)
    FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE {VIEW_FILTER}
) v;
""")
    for view in views:
        identifier = sql_identifier(view["schema"]) + "." + sql_identifier(view["name"])
        try:
            reader.query(f"SELECT * FROM {identifier} LIMIT 0;")
            view["limit_zero_access"] = True
        except SafeError as exc:
            if exc.code in {"ADMIN_ACCOUNT_BLOCKED", "ADMIN_PRIVILEGE_BLOCKED"} or exc.code.startswith("CONNECT_"):
                raise
            view["limit_zero_access"] = False
            view["access_error_code"] = exc.code
    tables = reader.json_query(f"""
SELECT coalesce(pg_catalog.json_agg(t ORDER BY t.schema, t.name), '[]'::json) FROM (
 SELECT n.nspname AS schema, c.relname AS name, c.relrowsecurity AS rls_enabled,
   c.relforcerowsecurity AS rls_forced, pg_catalog.pg_get_userbyid(c.relowner) AS owner,
   pg_catalog.has_table_privilege(current_user, c.oid, 'SELECT') AS table_select,
   pg_catalog.has_any_column_privilege(current_user, c.oid, 'SELECT') AS any_column_select
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p', 'f') AND {RELATION_FILTER}
 AND pg_catalog.has_schema_privilege(current_user, n.oid, 'USAGE')
) t;
""")
    owner_roles = reader.json_query(f"""
SELECT coalesce(pg_catalog.json_agg(o ORDER BY o.name), '[]'::json) FROM (
 SELECT r.rolname AS name, r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolcanlogin
 FROM pg_catalog.pg_roles r WHERE r.oid IN (
   SELECT c.relowner FROM pg_catalog.pg_class c
   JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE {VIEW_FILTER}
 )
) o;
""")
    policies = reader.json_query(f"""
SELECT coalesce(pg_catalog.json_agg(p ORDER BY p.schema, p.relation, p.name), '[]'::json) FROM (
 SELECT n.nspname AS schema, c.relname AS relation, pol.polname AS name,
   pol.polcmd AS command, pol.polpermissive AS permissive,
   (SELECT pg_catalog.json_agg(CASE WHEN role_id = 0 THEN 'PUBLIC'
            ELSE pg_catalog.pg_get_userbyid(role_id) END)
    FROM unnest(pol.polroles) AS role_id) AS roles,
   pg_catalog.pg_get_expr(pol.polqual, pol.polrelid) AS using_expression
 FROM pg_catalog.pg_policy pol JOIN pg_catalog.pg_class c ON c.oid = pol.polrelid
 JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace WHERE {RELATION_FILTER}
) p;
""")
    actual = {v["schema"] + "." + v["name"] for v in views}
    successful = sum(v["limit_zero_access"] for v in views)
    risks = []
    if any(role[key] for key in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolbypassrls")):
        risks.append({"code": "ADMIN_INSPECTION_ACCOUNT", "role": role["name"],
                      "user_authorized_exception": reader.allow_admin_inspection})
    for table in tables:
        if table["table_select"] or table["any_column_select"]:
            risks.append({"code": "BASE_TABLE_SELECT", "relation": table["schema"] + "." + table["name"]})
    for view in views:
        if view["schema"] == "app_internal":
            risks.append({"code": "INTERNAL_VIEW_SELECT", "relation": "app_internal." + view["name"]})
    if counted != len(views) or successful != counted:
        risks.append({"code": "VIEW_COUNT_OR_ACCESS_MISMATCH"})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "status": "verified" if successful == counted == len(views) else "incomplete",
        "role": role, "source": source, "views": views, "base_tables": tables, "risks": risks,
        "view_owner_roles": owner_roles, "rls_policies": policies,
        "comparison": {"source_only": sorted(set(source["declared_views"]) - actual),
                       "database_only": sorted(actual - set(source["declared_views"]))},
        "verification": {"database_selectable_view_count": counted, "catalog_view_count": len(views),
                         "limit_zero_success_count": successful, "business_data_rows_read": 0,
                         "read_only": True, "source_sql_executed": False,
                         "rls_isolation_runtime_tested": False,
                         "admin_inspection_authorized": reader.allow_admin_inspection},
    }


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=CRC_DIR / "secrets/.env")
    parser.add_argument("--view-sql", type=Path, default=CRC_DIR / "rdb/init/04_views_rls.sql")
    parser.add_argument("--out", type=Path, default=BASE_DIR / "work/db_catalog.json")
    parser.add_argument("--check-config", action="store_true", help="설정만 검사하며 DB에 접속하지 않음")
    parser.add_argument("--user", help="실행 시 적용할 DB 계정. .env는 수정하지 않음")
    parser.add_argument("--allow-admin-inspection", action="store_true",
                        help="사용자가 명시한 관리자 조사 예외. 읽기 전용과 데이터 행 조회 금지는 유지")
    args = parser.parse_args()
    attempts_file = args.out.parent / "connection_attempts.json"
    failures = 0
    try:
        config = load_env(args.env)
        source = source_metadata(args.view_sql)
        if args.user:
            config["RDB_USER"] = args.user
        reader = Reader(config, args.allow_admin_inspection)
        if args.check_config:
            print(json.dumps({"status": "configuration_checked", "db_connected": False,
                              "required_keys_present": True, "source_view_count": len(source["declared_views"])}))
            return 0
        if attempts_file.is_file():
            failures = json.loads(attempts_file.read_text()).get("consecutive_failures", 0)
        if failures >= MAX_CONNECTION_FAILURES:
            raise SafeError("CONNECT_ATTEMPTS_EXHAUSTED", "DB 접속이 3회 실패하여 중단함. 사용자 확인 필요")
        catalog = inspect_catalog(reader, source)
        write_json(args.out, catalog)
        write_json(attempts_file, {"consecutive_failures": 0, "last_status": "success"})
        print(json.dumps({"status": catalog["status"], "verification": catalog["verification"],
                          "comparison": catalog["comparison"], "risks": catalog["risks"]}, ensure_ascii=False))
        return 0 if catalog["status"] == "verified" else 1
    except SafeError as exc:
        if exc.code.startswith("CONNECT_") and exc.code not in {"CONNECT_ATTEMPTS_EXHAUSTED", "CONNECT_NETWORK"}:
            failures += 1
            write_json(attempts_file, {"consecutive_failures": failures, "last_error_code": exc.code})
        print(json.dumps({"status": "stopped", "code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 2
    except Exception:
        # traceback에 설정값이 포함될 수 있으므로 예상 밖 오류도 원문을 출력하지 않음.
        print(json.dumps({"status": "stopped", "code": "UNEXPECTED_ERROR", "message": "스크립트 오류 확인 필요"}, ensure_ascii=False))
        return 3


if __name__ == "__main__":
    sys.exit(main())
