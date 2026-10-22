"""LangChain을 통해 단계 순서를 실행하며 추적 데이터에서 질문을 분리함."""
from langchain_core.runnables import RunnableLambda
from langsmith import tracing_context
from application.models import Record
from application.ports import WorkflowPort
from application.services import RetrieverService


class LangChainWorkflow(WorkflowPort):
    """업무 서비스를 주입받아 LCEL 순차 실행을 제공함.

    Runnable 입출력은 None만 사용하여 자동 추적에 회원과 질문이 들어가지 않도록 함.
    """

    def __init__(self, service: RetrieverService):
        """업무 서비스를 주입받음."""
        self.service = service

    async def run(self, question: object, auth_scope: Record | None) -> tuple[Record, int]:
        """독립 요청 상태로 여섯 단계를 한 번씩 실행하고 설계 응답을 반환함."""
        context = self.service.initial()
        incoming = [question]
        del question

        def stage(number):
            async def execute(_):
                value = incoming.pop() if number == 1 else None
                try:
                    await self.service.execute_stage(number, context, value, auth_scope if number == 1 else None)
                finally:
                    value = None
                return None
            return RunnableLambda(execute, name=f'S-R{number}')

        pipeline = stage(1) | stage(2) | stage(3) | stage(4) | stage(5)
        with tracing_context(enabled=False):
            await pipeline.ainvoke(None, config={'callbacks': []})
        return self.service.finish(context)
