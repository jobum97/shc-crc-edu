"""질문 하나를 받아 설계 응답 JSON을 표준 출력으로 반환함."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


async def execute(args, runtime_factory=None) -> int:
    """조립된 실행기로 조회하며 오류도 설계 응답으로 출력함."""
    if runtime_factory is None:
        from bootstrap import create_runtime
        runtime_factory = create_runtime
    workflow, authenticator, close = runtime_factory(args.config, args.env)
    try:
        question = args.question if args.question is not None else sys.stdin.read(4097)
        scope = authenticator.authenticate(os.environ.get(args.token_env))
        response, status = await workflow.run(question, scope)
        question = None
        print(json.dumps(response, ensure_ascii=False, allow_nan=False))
        return 0 if status == 200 else 1
    finally:
        await close()


def main() -> int:
    """질문과 설정 경로를 해석하고 비밀값을 명령 인자로 받지 않음."""
    parser = argparse.ArgumentParser(description='질문을 읽기 전용 SQL로 조회함')
    parser.add_argument('question', nargs='?', help='생략하면 표준 입력에서 읽음')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--env', type=Path)
    parser.add_argument('--token-env', default='SQL_RETRIEVER_CLIENT_TOKEN')
    return asyncio.run(execute(parser.parse_args()))
