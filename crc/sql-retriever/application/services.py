"""기술 포트를 주입받아 여섯 단계의 업무 규칙을 수행함."""
import asyncio
import hashlib
import json
import time
import uuid
from datetime import date, datetime, timezone
from .models import Record, RetrieverError
from .ports import LanguageModelPort, RepositoryPort, SqlValidatorPort
from domain.budget import can_start, stage_limit
from domain.privacy import clean_question
from domain.routing import route_question
from domain.plan_policy import validate_plan
from domain.errors import PolicyError


class RetrieverService:
    """단계 업무를 수행하며 모델, SQL 검사, 조회 구현은 주입받음.

    SDK 생성과 HTTP 입출력은 담당하지 않음.
    """

    def __init__(self, config: Record, catalog: Record, queries: Record, errors: Record,
                 llm: LanguageModelPort, repository: RepositoryPort, validator: SqlValidatorPort,
                 clock=time.monotonic):
        """신뢰 설정과 포트를 보관하고 시험용 단조 시계를 주입받음."""
        self.config, self.catalog, self.queries, self.errors = config, catalog, queries, errors
        self.llm, self.repository, self.validator, self.clock = llm, repository, validator, clock

    def initial(self) -> Record:
        """실패 응답에도 필요한 최소 상태를 먼저 생성함."""
        started = self.clock()
        return {'request_id': str(uuid.uuid4()), 'started_at': started,
                'deadline_at': started + 30, 'error': None}

    async def execute_stage(self, number: int, context: Record, question=None, auth_scope=None) -> None:
        """단계 예산을 검사하고 업무 오류를 S-R6로 보낼 상태로 변환함."""
        if context['error']:
            return
        stage = f'S-R{number}'
        route = context.get('plan', {}).get('route')
        if not can_start(number, self.clock() - context['started_at'], route):
            context['error'] = {'code': 'TIME_BUDGET_EXCEEDED', 'stage': stage}
            return
        timeout = min(stage_limit(number, route), context['deadline_at'] - self.clock() - 1)
        entered = self.clock()
        try:
            async with asyncio.timeout(max(0, timeout)):
                if number == 1:
                    self._accept(context, question, auth_scope)
                elif number == 2:
                    await self._plan(context)
                elif number == 3:
                    validate_plan(context['plan'], context['question_slots'], self.catalog,
                                  context['base_date'], context['coverage_start'])
                    context['plan_check'] = True
                elif number == 4:
                    sql = await asyncio.to_thread(self.validator.validate, context['plan'], context['question_slots'])
                    context['validated_sql'] = sql
                    context['sql_fingerprint'] = hashlib.sha256(sql.encode()).hexdigest()
                elif number == 5:
                    context['result'] = await self.repository.execute(context)
            # 동기 규칙 실행도 상한을 넘으면 성공으로 다음 단계에 전달하지 않음.
            resolved_route = context.get('plan', {}).get('route', route)
            if (self.clock() >= context['deadline_at'] - 1
                    or self.clock() - entered > stage_limit(number, resolved_route)):
                raise RetrieverError('TIME_BUDGET_EXCEEDED', stage)
        except RetrieverError as exc:
            context['error'] = {'code': exc.code, 'stage': exc.stage}
        except PolicyError as exc:
            context['error'] = {'code': exc.code, 'stage': stage}
        except TimeoutError:
            context['error'] = {'code': 'TIME_BUDGET_EXCEEDED', 'stage': stage}
        except Exception:
            code = {1: 'CONFIG_UNAVAILABLE', 2: 'INVALID_MODEL_OUTPUT', 3: 'UNSUPPORTED_REQUEST',
                    4: 'SQL_REJECTED', 5: 'DB_EXECUTION_FAILED'}[number]
            context['error'] = {'code': code, 'stage': stage}

    def _accept(self, context: Record, question: object, auth_scope: Record | None) -> None:
        """인증 결과와 설정의 유효성을 확인하고 정제 질문만 상태에 남김."""
        if not isinstance(question, str) or not question.strip() or len(question.encode()) > 4096:
            raise RetrieverError('INVALID_REQUEST', 'S-R1')
        if not auth_scope or not all(auth_scope.get(k) for k in ('principal_id', 'authorized_member_id')):
            raise RetrieverError('UNAUTHORIZED_SCOPE', 'S-R1')
        try:
            if self.config.get('execution_ready') is False:
                raise ValueError
            meta = self.config['metadata']
            base = date.fromisoformat(meta['base_date'])
            coverage = date.fromisoformat(meta['coverage_start'])
            loaded = datetime.fromisoformat(meta['loaded_at'])
            age = (datetime.now(timezone.utc) - loaded).total_seconds()
            if coverage > base or not 0 <= age <= 86400:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RetrieverError('CONFIG_UNAVAILABLE', 'S-R1') from None
        context.update(member_scope=auth_scope['authorized_member_id'], base_date=base, coverage_start=coverage,
                       question_clean=clean_question(question), question_slots={}, schema_catalog=self.catalog,
                       query_catalog=self.queries, policy=self.config.get('policy', {}))

    async def _plan(self, context: Record) -> None:
        """정확한 정형 매칭을 우선하며 모델을 최대 한 번 호출함."""
        slots, plan = route_question(context['question_clean'], context['base_date'],
                                    context['coverage_start'], self.queries)
        context['question_slots'] = slots
        route = 'fixed' if plan is not None else 'nl2sql'
        if not can_start(2, self.clock() - context['started_at'], route):
            raise RetrieverError('TIME_BUDGET_EXCEEDED', 'S-R2')
        if plan is None:
            payload = {key: context[key] for key in ('question_clean', 'question_slots', 'schema_catalog')}
            payload.update(base_date=context['base_date'].isoformat(), coverage_start=context['coverage_start'].isoformat(),
                           sql_policy=context['policy'])
            plan = await self.llm.plan(payload, min(context['deadline_at'] - 13, self.clock() + 15))
            plan.update(route='nl2sql', query_id=None)
        context['plan'] = plan

    def finish(self, context: Record) -> tuple[Record, int]:
        """S-R6에서 성공 또는 고정 오류 응답을 만들고 민감한 상태를 제외함."""
        plan = context.get('plan') or {}
        result = context.get('result')
        warnings = []
        if result:
            if result.get('completeness') == 'limit_reached_more_unknown':
                warnings.append('행 상한에 도달하여 추가 결과 여부를 확인할 수 없습니다.')
            if not result.get('rows'):
                warnings.append('조회 결과가 없습니다. 거래나 연체가 없다고 단정할 수 없습니다.')
            if plan.get('view') == 'app.card_fee_usage_6m':
                warnings.append('연회비는 상품 안내값입니다. 실제 기간과 상품 효력은 각 행을 확인하세요.')
            if plan.get('view') == 'app.merchant_usage':
                warnings.append('실제 관측 시작은 행의 period_start와 제공 시작일 중 늦은 날짜입니다.')
            if plan.get('view') == 'app.monthly_usage':
                warnings.append('월별 조회는 제공 시작월의 월초를 포함할 수 있습니다. 다른 View 합계와 단순 비교하지 마세요.')
        error: Record | None = context['error']
        if error:
            definition = self.errors[error['code']] if error['code'] in self.errors else self.errors['RESPONSE_FAILED']
            error = {'code': error['code'] if error['code'] in self.errors else 'RESPONSE_FAILED',
                     'message': definition['message'], 'stage': error['stage']}
            status = definition['http']
        else:
            status = 200
        response = {'request_id': context['request_id'], 'status': 'error' if error else 'success',
                    'data': None if error else result, 'error': error,
                    'meta': {'base_date': str(context['base_date']) if 'base_date' in context else None,
                             'coverage_start': str(context['coverage_start']) if 'coverage_start' in context else None,
                             'route': plan.get('route'), 'query_id': plan.get('query_id'), 'warnings': warnings}}
        try:
            json.dumps(response, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            response.update(status='error', data=None, error={'code': 'RESPONSE_FAILED', 'stage': 'S-R6',
                            'message': self.errors['RESPONSE_FAILED']['message']})
            status = 500
        return response, status
