"""Groq 단일 호출과 응답 구조 검증을 담당함."""
import asyncio
import json
import time
from typing import Any

from application.models import Record, RetrieverError
from application.ports import LanguageModelPort


def _matches_schema(value: Any, schema: Record) -> bool:
    """이 프로젝트의 제한된 JSON Schema 타입, 필수 키, 열거값을 검사함."""
    types = schema.get("type", [])
    types = [types] if isinstance(types, str) else types
    matches = {
        "null": value is None,
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
        "boolean": isinstance(value, bool),
        "integer": type(value) is int,
        "number": type(value) in (int, float),
    }
    if types and not any(matches.get(t, False) for t in types):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])) <= value.keys():
            return False
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            return False
        return all(_matches_schema(v, properties[k]) for k, v in value.items() if k in properties)
    if isinstance(value, list):
        return all(_matches_schema(v, schema.get("items", {})) for v in value)
    return True


def _unique_object(pairs: list[tuple[str, Any]]) -> Record:
    """중복 JSON 키의 앞뒤 값 해석 차이를 차단함."""
    result: Record = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


class GroqGateway(LanguageModelPort):
    """주입된 AsyncGroq로 정제 입력만 보내며 결과 DB 행과 식별자를 보내지 않음."""

    def __init__(self, client: Any, system_prompt: str, response_format: Record, settings: Record):
        """SDK, 프롬프트, 구조, 설정을 주입받으며 네트워크 호출은 하지 않음."""
        self.client = client
        self.system_prompt = system_prompt
        self.response_format = response_format
        self.settings = settings

    async def plan(self, payload: Record, deadline: float) -> Record:
        """절대 종료시각 안에서 최대 15초 동안 한 번 호출하여 검증한 계획을 반환함.

        예외: 시간 초과, 제공자 실패, 잘린 출력은 고정 RetrieverError로 변환함.
        부수효과: 외부 Groq 호출 1회이며 입력과 모델 추론을 기록하지 않음.
        """
        allowed = {"question_clean", "question_slots", "base_date", "coverage_start", "schema_catalog", "sql_policy"}
        if payload.keys() - allowed:
            raise RetrieverError("INVALID_MODEL_OUTPUT", "S-R2")
        budget = min(15.0, deadline - time.monotonic())
        if budget <= 0:
            raise RetrieverError("UPSTREAM_TIMEOUT", "S-R2")
        # XML 종료 태그를 질문으로 주입해도 JSON 데이터 영역을 벗어나지 않게 함.
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        for char, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
            encoded = encoded.replace(char, escape)
        try:
            async with asyncio.timeout(budget):
                response = await self.client.with_options(max_retries=0, timeout=budget).chat.completions.create(
                    model=self.settings.get("model", "openai/gpt-oss-120b"),
                    temperature=0.1,
                    top_p=1,
                    max_completion_tokens=2048,
                    reasoning_effort="low",
                    include_reasoning=False,
                    stream=False,
                    n=1,
                    response_format=self.response_format,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": f"<retriever_input>\n{encoded}\n</retriever_input>"},
                    ],
                )
            if len(response.choices) != 1 or response.choices[0].finish_reason != "stop":
                raise RetrieverError("INVALID_MODEL_OUTPUT", "S-R2")
            content = response.choices[0].message.content
            if not isinstance(content, str) or len(content.encode()) > 65536:
                raise RetrieverError("INVALID_MODEL_OUTPUT", "S-R2")
            result = json.loads(content, object_pairs_hook=_unique_object)
            if not _matches_schema(result, self.response_format["json_schema"]["schema"]):
                raise RetrieverError("INVALID_MODEL_OUTPUT", "S-R2")
            return result
        except RetrieverError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            raise RetrieverError("UPSTREAM_TIMEOUT", "S-R2") from None
        except (ValueError, TypeError, AttributeError, IndexError, KeyError):
            raise RetrieverError("INVALID_MODEL_OUTPUT", "S-R2") from None
        except Exception as exc:
            code = "UPSTREAM_TIMEOUT" if type(exc).__name__ == "APITimeoutError" else "UPSTREAM_UNAVAILABLE"
            raise RetrieverError(code, "S-R2") from None
