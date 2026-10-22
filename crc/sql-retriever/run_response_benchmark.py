"""합성 최대 크기 결과로 S-R6 응답 작성과 JSON 직렬화 시간을 재현 측정함."""
import json
import platform
import statistics
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from application.services import RetrieverService

BASE = Path(__file__).resolve().parent
_SAMPLE_COUNT = 5
_STAGE_BUDGET_SECONDS = 1.0


class _UnusedPorts:
    """외부 포트를 호출하면 즉시 실패하여 벤치마크의 외부 연결을 금지함."""

    async def plan(self, payload: dict[str, Any], deadline: float) -> dict[str, Any]:
        """이 측정에는 모델 호출이 없으므로 실행 시 실패함."""
        raise AssertionError("benchmark must not call model")

    async def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """이 측정에는 DB 호출이 없으므로 실행 시 실패함."""
        raise AssertionError("benchmark must not call repository")

    def validate(self, plan: dict[str, Any], slots: dict[str, Any]) -> str:
        """이 측정은 S-R6만 실행하므로 SQL 검사를 호출하지 않음."""
        raise AssertionError("benchmark must not call SQL validator")


def _encode(value: Any) -> bytes:
    """DB 결과 제한과 같은 직렬화 규칙으로 UTF-8 바이트를 계산함."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _synthetic_result(row_count: int, target_bytes: int) -> dict[str, Any]:
    """한글 중심의 합성 값으로 지정 바이트에 정확히 맞춘 결과를 생성함."""
    result: dict[str, Any] = {
        "rows": [{"product_name": ""} for _ in range(row_count)],
        "columns": [{"name": "product_name", "db_type": "text", "unit": None, "currency": None}],
        "grain": "card",
        "period": None,
        "completeness": "limit_reached_more_unknown",
    }
    remaining = target_bytes - len(_encode(result))
    if remaining < 0:
        raise ValueError("benchmark target is smaller than metadata")
    per_row, extra = divmod(remaining, row_count)
    for index, row in enumerate(result["rows"]):
        length = per_row + (index < extra)
        # 한글 UTF-8 3바이트와 ASCII 나머지로 목표 크기를 오차 없이 맞춤.
        row["product_name"] = "가" * (length // 3) + "x" * (length % 3)
    if len(_encode(result)) != target_bytes:
        raise AssertionError("synthetic result size mismatch")
    return result


def main() -> int:
    """200행과 1MiB 직전 결과의 응답 작성 및 직렬화를 5회 측정함.

    방법: DB와 모델 대역을 주입한 실제 서비스의 finish만 직접 호출함.
    부수효과: 행 내용 없이 크기, 시간, 성공 여부를 work/response_benchmark.json에 기록함.
    반환값: 모든 표본의 응답 계약과 1초 예산 확인 시 0, 하나라도 실패하면 1임.
    """
    policy = json.loads((BASE / "resources/sql_policy.json").read_text())
    errors = json.loads((BASE / "resources/errors.json").read_text())
    limit = policy["max_result_bytes"]
    row_count = policy["output_row_limit"]
    result = _synthetic_result(row_count, limit - 1)
    ports = _UnusedPorts()
    service = RetrieverService({}, {}, {}, errors, ports, ports, ports)
    context: dict[str, Any] = {
        "request_id": "00000000-0000-4000-8000-000000000000",
        "error": None,
        "base_date": date(2026, 6, 15),
        "coverage_start": date(2026, 1, 1),
        "plan": {"route": "nl2sql", "query_id": None, "view": "app.customer_cards"},
        "result": result,
    }
    samples = []
    # 준비 및 검증 시간은 측정에서 제외함. finish 안의 JSON 검증은 실제 구현 그대로 포함함.
    for index in range(_SAMPLE_COUNT):
        started = time.perf_counter()
        response, status = service.finish(context)
        finished = time.perf_counter()
        body = _encode(response)
        serialized = time.perf_counter()
        decoded = json.loads(body)
        contract_valid = (
            status == 200 and decoded["status"] == "success" and decoded["error"] is None
            and decoded["data"] == result and len(decoded["data"]["rows"]) == row_count
            and bool(decoded["meta"]["warnings"])
        )
        elapsed = serialized - started
        samples.append({
            "sample": index + 1,
            "finish_seconds": round(finished - started, 8),
            "additional_json_encode_seconds": round(serialized - finished, 8),
            "combined_seconds": round(elapsed, 8),
            "response_body_bytes": len(body),
            "http_status": status,
            "contract_valid": contract_valid,
            "within_one_second": elapsed < _STAGE_BUDGET_SECONDS,
            "passed": contract_valid and elapsed < _STAGE_BUDGET_SECONDS,
        })
    report = {
        "status": "passed" if all(sample["passed"] for sample in samples) else "failed",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "measurement": "actual RetrieverService.finish plus additional JSON UTF-8 encoding",
        "samples_count": _SAMPLE_COUNT,
        "s_r6_budget_seconds": _STAGE_BUDGET_SECONDS,
        "synthetic_row_count": row_count,
        "query_result_bytes": len(_encode(result)),
        "query_result_limit_bytes": limit,
        "headroom_bytes": limit - len(_encode(result)),
        "max_finish_seconds": max(sample["finish_seconds"] for sample in samples),
        "max_combined_seconds": max(sample["combined_seconds"] for sample in samples),
        "median_combined_seconds": statistics.median(sample["combined_seconds"] for sample in samples),
        "response_body_exceeds_query_result_limit": any(sample["response_body_bytes"] > limit for sample in samples),
        "size_policy": {
            "limit_applies_to": "QueryResult JSON UTF-8 bytes; response wrapper is outside that limit",
            "response_wrapper_separate_size_limit": False,
            "encoding": "json.dumps ensure_ascii=False, allow_nan=False, default separators",
        },
        "scope": {
            "db_calls": 0,
            "model_calls": 0,
            "env_files_read": 0,
            "data_rows_logged": 0,
            "source": "synthetic data only",
            "includes_transport_or_http_server": False,
            "worst_case_guarantee": False,
            "limitation": "Local five-sample measurement only; not a production latency or worst-case guarantee",
            "deadline_enforcement": "finish is synchronous; measurement does not establish runtime preemption",
        },
        "samples": samples,
    }
    output = BASE / "work/response_benchmark.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "samples": _SAMPLE_COUNT,
                      "query_result_bytes": report["query_result_bytes"],
                      "max_combined_seconds": report["max_combined_seconds"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
