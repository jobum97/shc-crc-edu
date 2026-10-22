"""동기 응답 HTTP 요청을 업무 워크플로우로 전달함."""
import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_app(workflow=None, authenticator=None, close=None) -> FastAPI:
    """주입된 업무 실행기를 쓰며 없는 경우에만 bootstrap에서 조립함."""
    @asynccontextmanager
    async def lifespan(app):
        nonlocal workflow, authenticator, close
        if workflow is None:
            from bootstrap import create_runtime
            workflow, authenticator, close = create_runtime()
        yield
        if close:
            await close()

    app = FastAPI(title='SQL 검색기', lifespan=lifespan)

    @app.post('/search')
    async def search(request: Request):
        """본문 크기를 제한하고 인증 결과를 설계 응답으로 변환함."""
        body = bytearray()
        invalid = False
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 16384:
                        invalid = True
                        break
                    body.extend(chunk)
        except TimeoutError:
            invalid = True
        question = None
        if not invalid:
            try:
                def unique_pairs(pairs):
                    result = {}
                    for key, item in pairs:
                        if key in result:
                            raise ValueError('duplicate key')
                        result[key] = item
                    return result
                value = json.loads(body, object_pairs_hook=unique_pairs)
                if isinstance(value, dict) and set(value) == {'question'}:
                    question = value.pop('question')
            except (ValueError, UnicodeError):
                pass
        body.clear()
        header = request.headers.get('authorization', '')
        token = header[7:] if header.startswith('Bearer ') else None
        scope = authenticator.authenticate(token)
        del header, token
        response, status = await workflow.run(question, scope)
        question = None
        return JSONResponse(response, status_code=status)

    return app
