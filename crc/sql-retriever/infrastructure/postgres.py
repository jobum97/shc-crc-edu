"""회원 세션을 격리한 PostgreSQL 읽기 전용 조회를 수행함."""
import asyncio
import hashlib
import hmac
import json
import math
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

import psycopg

from application.models import Record, RetrieverError
from application.ports import RepositoryPort

_ROLE_CHECK = """
SELECT EXISTS (
 SELECT 1 FROM pg_catalog.pg_roles r
 WHERE pg_catalog.pg_has_role(current_user, r.oid, 'MEMBER')
 AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls
      OR r.rolname IN ('pg_read_all_data', 'pg_write_all_data',
                      'pg_execute_server_program', 'pg_read_server_files', 'pg_write_server_files'))
)
"""
_RAW_ACCESS_CHECK = """
SELECT EXISTS (
 SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
 AND n.nspname NOT LIKE 'pg_toast%'
 AND NOT (n.nspname='public' AND c.relname='lab_metadata')
 AND (pg_catalog.has_table_privilege(current_user, c.oid, 'SELECT')
      OR pg_catalog.has_any_column_privilege(current_user, c.oid, 'SELECT'))
)
"""
_TYPES = {
    16: "boolean", 20: "bigint", 21: "smallint", 23: "integer", 25: "text",
    700: "real", 701: "double precision", 1042: "character", 1043: "character varying",
    1082: "date", 1114: "timestamp without time zone", 1184: "timestamp with time zone", 1700: "numeric",
}
_MAX_BYTES = 1024 * 1024  # 직렬화된 결과의 최대 바이트임. 드라이버는 최대 한 행을 추가로 보유함.


def _json_value(value: Any) -> Any:
    """큰 수의 정밀도와 날짜 의미를 보존하고 JSON 불가능한 타입을 거부함."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > 9007199254740991 else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")


class PostgresRepository(RepositoryPort):
    """연결 설정과 카탈로그를 받아 읽기 전용으로 조회하며 연결을 요청끼리 공유하지 않음.

    연결 풀 최적화는 하지 않음. 새 연결과 트랜잭션 지역 설정으로 회원 세션 오염을 방지함.
    기본값은 제한 계정만 허용함. 명시적으로 승인한 로컬 시험만 cardlab 권한 예외를 허용함.
    """

    def __init__(self, connection_kwargs: Record, catalog: Record, connect_factory: Callable | None = None,
                 *, allow_cardlab_test: bool = False):
        """연결 생성 함수를 주입받고 동시에 최대 8개 요청의 연결을 허용함."""
        self.connection_kwargs = dict(connection_kwargs)
        self.catalog = catalog
        self.allow_cardlab_test = allow_cardlab_test is True
        self.connect_factory = connect_factory or psycopg.AsyncConnection.connect
        self._slots = asyncio.Semaphore(8)

    async def execute(self, context: Record) -> Record:
        """검증 SQL을 변경 없이 실행하고 타입, 기간, 완전성을 함께 반환함.

        방법: 읽기 전용 연결에서 권한 확인 후 트랜잭션 지역 변수 3개를 설정함.
        예외: 관리자, 원본 테이블 권한, SQL 변경, DB 실패와 크기 초과는 RetrieverError로 반환함.
        부수효과: 메타데이터 검사와 읽기 전용 SELECT만 수행하며 종료 시 연결을 폐기함.
        """
        account = self.connection_kwargs.get("user", "")
        # 사용자 승인 예외는 정확한 cardlab 계정과 실제 불리언 True 조합에만 적용함.
        cardlab_test = self.allow_cardlab_test and account == "cardlab"
        if account.lower() in {"cardlab", "postgres"} and not cardlab_test:
            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
        sql = context["validated_sql"]
        expected = hashlib.sha256(sql.encode()).hexdigest()
        if not hmac.compare_digest(expected, context["sql_fingerprint"]):
            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
        limit_match = re.search(r"\bLIMIT\s+(\d+)\s*;?\s*$", sql, re.IGNORECASE)
        if not limit_match or not 1 <= int(limit_match[1]) <= 200:
            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
        limit = int(limit_match[1])
        connection = None
        acquired = False
        stop = min(context["deadline_at"] - 1, time.monotonic() + 8)
        if stop - time.monotonic() <= 1:
            raise RetrieverError("TIME_BUDGET_EXCEEDED", "S-R5")
        try:
            # 전체 8초 중 마지막 1초는 취소, 롤백, 연결 폐기에 예약함.
            async with asyncio.timeout_at(stop - 1):
                async with asyncio.timeout(1):
                    await self._slots.acquire()
                    acquired = True
                kwargs = dict(self.connection_kwargs)
                kwargs.update(autocommit=True, connect_timeout=1,
                              options="-c default_transaction_read_only=on -c search_path=pg_catalog")
                async with asyncio.timeout(1):
                    connection = await self.connect_factory(**kwargs)
                async with asyncio.timeout(1):
                    await connection.execute("BEGIN READ ONLY")
                    for query in (_ROLE_CHECK, _RAW_ACCESS_CHECK):
                        cursor = await connection.execute(query)
                        row = await cursor.fetchone()
                        if row is None or type(row[0]) is not bool:
                            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
                        if row[0] and not cardlab_test:
                            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
                    statement_ms = min(3000, int((stop - time.monotonic() - 2) * 1000))
                    if statement_ms <= 0:
                        raise RetrieverError("TIME_BUDGET_EXCEEDED", "S-R5")
                    await connection.execute(
                        "SELECT pg_catalog.set_config('app.member_id', %s, true), "
                        "pg_catalog.set_config('app.base_date', %s, true), "
                        "pg_catalog.set_config('app.coverage_start', %s, true), "
                        "pg_catalog.set_config('statement_timeout', %s, true), "
                        "pg_catalog.set_config('lock_timeout', '500', true)",
                        (context["member_scope"], context["base_date"].isoformat(),
                         context["coverage_start"].isoformat(), str(statement_ms)),
                    )
                # 서버 측 커서로 결과 전체를 한 번에 메모리로 가져오지 않음.
                async with connection.cursor(name="retriever_rows") as cursor:
                    async with asyncio.timeout(3):
                        # params=None을 유지해 모델 SQL의 % 문자를 드라이버 바인딩으로 해석하지 않게 함.
                        await cursor.execute(sql)
                        # DECLARE는 지연 실행되므로 첫 행 계산까지 SQL 실행 3초에 포함함.
                        batch = await cursor.fetchmany(1)
                    async with asyncio.timeout(1):
                        descriptions = cursor.description or []
                        names = [column.name for column in descriptions]
                        if names != context["plan"]["selected_columns"] or len(set(names)) != len(names):
                            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5")
                        view_columns = {c["name"]: c["type"]
                                        for c in self.catalog.get(context["plan"].get("view"), {}).get("columns", [])}
                        columns = [dict(name=c.name,
                                        db_type=(view_columns[c.name] if c.type_code == 1042 and
                                                 view_columns.get(c.name, "").startswith("character")
                                                 else _TYPES.get(c.type_code, f"oid:{c.type_code}")),
                                        unit=None, currency=None) for c in descriptions]
                        rows: list[Record] = []
                        size = len(json.dumps(columns, ensure_ascii=False).encode())
                        while batch:
                            row = {name: _json_value(value) for name, value in zip(names, batch[0], strict=True)}
                            size += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode()) + 1
                            if size > _MAX_BYTES or len(rows) >= limit:
                                raise RetrieverError("RESULT_TOO_LARGE", "S-R5")
                            rows.append(row)
                            batch = await cursor.fetchmany(1)
                plan = context["plan"]
                result = dict(rows=rows, columns=columns, grain=plan["grain"],
                              period={"start": plan["period_start"], "end": plan["period_end"]}
                              if plan.get("period_start") is not None else None,
                              completeness="limit_reached_more_unknown" if len(rows) == limit else "complete")
                if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > _MAX_BYTES:
                    raise RetrieverError("RESULT_TOO_LARGE", "S-R5")
                return result
        except RetrieverError:
            raise
        except (TimeoutError, psycopg.errors.QueryCanceled):
            raise RetrieverError("TIME_BUDGET_EXCEEDED", "S-R5") from None
        except Exception:
            raise RetrieverError("DB_EXECUTION_FAILED", "S-R5") from None
        finally:
            try:
                if connection is not None:
                    try:
                        async with asyncio.timeout(min(1, max(0.001, stop - time.monotonic()))):
                            await connection.execute("ROLLBACK")
                            await connection.close()
                    except asyncio.CancelledError:
                        connection.pgconn.finish()
                        raise
                    except Exception:
                        # 정리가 실패해도 소켓을 즉시 닫아 오염 연결의 재사용을 차단함.
                        connection.pgconn.finish()
            finally:
                if acquired:
                    self._slots.release()
