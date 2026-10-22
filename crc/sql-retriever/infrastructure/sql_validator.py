"""SQL 구문 트리와 조회 계획을 대조하여 허용한 읽기 문장만 반환함."""
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import NoReturn

import sqlglot
from sqlglot import exp

from application.models import RetrieverError
from application.ports import SqlValidatorPort


_NUMERIC_TYPES = {'integer', 'bigint', 'numeric', 'smallint', 'decimal', 'real', 'double precision'}
_AGGREGATES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
_ALLOWED_NODES = {
    exp.Select, exp.From, exp.Table, exp.Identifier, exp.TableAlias, exp.Column,
    exp.Alias, exp.Literal, exp.Null, exp.Boolean, exp.Paren, exp.Where,
    exp.And, exp.Or, exp.Not, *_COMPARISONS, exp.Is, exp.In, exp.Between,
    exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Neg, exp.Group, exp.Having,
    exp.Order, exp.Ordered, exp.Limit, exp.Star, exp.Cast, exp.DataType,
    exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Coalesce, exp.Round,
    exp.TimestampTrunc, exp.TimeToStr, exp.Var,
}
_BASE_GRAINS = {
    'customer_profile': 'profile', 'customer_cards': 'card',
    'card_fee_usage_6m': 'card', 'monthly_usage': 'card_month',
    'merchant_usage': 'merchant_month', 'customer_delinquency': 'month',
    'customer_delinquency_history': 'month', 'customer_daily_usage': 'day',
}
_GROUP_KEYS = {
    'profile': {'join_date', 'age_band'}, 'card': {'card_ref'},
    'card_month': {'card_ref', 'month'},
    'merchant_month': {'card_ref', 'month', 'merchant_name', 'category'},
    'month': {'month'}, 'day': {'usage_date'}, 'aggregate': set(),
}


def _reject() -> NoReturn:
    raise RetrieverError('SQL_REJECTED', 'S-R4')


def _name(identifier: exp.Expression | None) -> str:
    if not isinstance(identifier, exp.Identifier):
        _reject()
    return identifier.name if identifier.args.get('quoted') else identifier.name.lower()


def _literal(node: exp.Expression) -> object:
    """계획 비교에 사용할 검증 가능한 상수만 추출함."""
    if isinstance(node, exp.Cast):
        return _literal(node.this)
    if isinstance(node, exp.Literal):
        if node.is_string:
            return node.this
        return Decimal(node.this)
    if isinstance(node, exp.Neg):
        value = _literal(node.this)
        if isinstance(value, Decimal):
            return -value
    if isinstance(node, exp.Boolean):
        return node.this
    if isinstance(node, exp.Null):
        return None
    _reject()


class SqlValidator(SqlValidatorPort):
    """주입한 공개 View와 정형쿼리만 신뢰하며 DB나 모델을 호출하지 않음."""

    def __init__(self, catalog: dict, queries: dict):
        """검증에 필요한 읽기 전용 카탈로그와 등록 SQL을 주입받음."""
        self.catalog = catalog
        self.queries = queries

    def validate(self, plan: dict, slots: dict) -> str:
        """계획과 질문 조건에 맞는 SQL 원문을 반환함.

        SQL 문법과 함수 인자형, 허용 View, 출력 순서와 필수 조건을 함께 검사함.
        예외: 구조가 불명확하거나 조건이 다르면 SQL_REJECTED를 발생시킴.
        부수효과: 없음. SQL이나 파서 오류를 로그와 오류 메시지에 넣지 않음.
        """
        try:
            return self._validate(plan, slots)
        except RetrieverError:
            raise
        except (sqlglot.errors.SqlglotError, ValueError, TypeError, KeyError,
                AttributeError, RecursionError, InvalidOperation):
            raise RetrieverError('SQL_REJECTED', 'S-R4') from None

    def _validate(self, plan: dict, slots: dict) -> str:
        sql = plan.get('sql')
        if not isinstance(sql, str) or not sql.strip() or len(sql.encode('utf-8')) > 16384:
            _reject()
        trees = sqlglot.parse(sql, read='postgres')
        if len(trees) != 1 or type(trees[0]) is not exp.Select:
            _reject()
        tree = trees[0]
        # 새 파서가 인식하는 구문도 명시적으로 허용하지 않았으면 기본 거부함.
        allowed_args = {'expressions', 'from_', 'where', 'group', 'having', 'order', 'limit'}
        if any(value and key not in allowed_args for key, value in tree.args.items()):
            _reject()
        nodes = list(tree.walk())
        if len(nodes) > 1500 or any(type(node) not in _ALLOWED_NODES for node in nodes):
            _reject()
        self._check_function_spelling(nodes, sql)
        if sum(isinstance(node, exp.Select) for node in nodes) != 1:
            _reject()
        tables = list(tree.find_all(exp.Table))
        if len(tables) != 1 or not tree.args.get('from_') or tree.args['from_'].this is not tables[0]:
            _reject()
        table = tables[0]
        if any(value and key not in {'this', 'db', 'alias'} for key, value in table.args.items()):
            _reject()
        view = f'{_name(table.args.get("db"))}.{_name(table.this)}'
        if view not in self.catalog or plan.get('view') != view:
            _reject()
        alias = table.args.get('alias')
        if alias and alias.args.get('columns'):
            _reject()
        qualifier = _name(alias.this) if alias else _name(table.this)
        columns = {item['name']: self._column_type(item['type']) for item in self.catalog[view]['columns']}
        limit = tree.args.get('limit')
        if not isinstance(limit, exp.Limit) or not isinstance(limit.expression, exp.Literal):
            _reject()
        if limit.expression.is_string or not re.fullmatch(r'[0-9]+', limit.expression.this):
            _reject()
        if not 1 <= int(limit.expression.this) <= 200:
            _reject()
        if any(value and key != 'expression' for key, value in limit.args.items()):
            _reject()
        output_names, output_types = [], {}
        for expression in tree.expressions:
            if isinstance(expression, exp.Alias):
                name = _name(expression.args.get('alias'))
            elif isinstance(expression, exp.Column):
                name = _name(expression.this)
            else:
                # 별칭 없는 계산식은 출력 계약을 안정적으로 확인할 수 없음.
                _reject()
            if name in output_types:
                _reject()
            output_names.append(name)
            output_types[name] = self._type(expression, columns, qualifier)
        if output_names != plan.get('selected_columns') or not output_names:
            _reject()
        for clause in ('where', 'having'):
            if tree.args.get(clause) and self._type(tree.args[clause].this, columns, qualifier) != 'bool':
                _reject()
        if tree.args.get('where') and any(isinstance(node, _AGGREGATES) for node in tree.args['where'].walk()):
            _reject()
        group = tree.args.get('group')
        if group:
            if any(value and key != 'expressions' for key, value in group.args.items()):
                _reject()
            for expression in group.expressions:
                self._type(expression, columns, qualifier)
                if any(isinstance(node, _AGGREGATES) for node in expression.walk()):
                    _reject()
        if tree.args.get('order'):
            for ordered in tree.args['order'].expressions:
                self._type(ordered.this, {**columns, **output_types}, qualifier)
        self._check_grain(tree, plan, view)
        query_id = plan.get('query_id')
        if query_id is not None or plan.get('route') == 'fixed':
            registered = self.queries.get(query_id, {})
            if not registered or sha256(sql.encode()).digest() != sha256(registered['sql'].encode()).digest():
                _reject()
            if registered['view'] != view or registered['grain'] != plan.get('grain'):
                _reject()
        self._check_metrics(tree, plan, slots)
        self._check_conditions(tree, plan, slots, fixed=query_id is not None)
        return sql

    @staticmethod
    def _check_function_spelling(nodes: list, sql: str) -> None:
        """파서가 다른 제품의 함수 별칭을 같은 노드로 바꾸는 경우를 차단함."""
        names = {exp.Count: 'COUNT', exp.Sum: 'SUM', exp.Avg: 'AVG', exp.Min: 'MIN',
                 exp.Max: 'MAX', exp.Coalesce: 'COALESCE', exp.Round: 'ROUND',
                 exp.TimestampTrunc: 'DATE_TRUNC', exp.TimeToStr: 'TO_CHAR'}
        for node in nodes:
            expected = names.get(type(node))
            if expected:
                start, end = node.meta.get('start'), node.meta.get('end')
                if not isinstance(start, int) or not isinstance(end, int):
                    _reject()
                if sql[start:end + 1].upper() != expected:
                    _reject()

    @staticmethod
    def _column_type(value: str) -> str:
        if value in _NUMERIC_TYPES:
            return 'number'
        if value == 'date':
            return 'date'
        if value == 'boolean':
            return 'bool'
        if value == 'text' or value.startswith('character'):
            return 'text'
        _reject()

    def _type(self, node: exp.Expression, columns: dict, qualifier: str) -> str:
        """허용 식을 재귀 검사하고 PostgreSQL 기본 타입으로 단순화함."""
        if isinstance(node, (exp.Alias, exp.Paren)):
            return self._type(node.this, columns, qualifier)
        if isinstance(node, exp.Column):
            if node.args.get('db') or node.args.get('catalog'):
                _reject()
            if node.args.get('table') and _name(node.args['table']) != qualifier:
                _reject()
            name = _name(node.this)
            if name not in columns:
                _reject()
            return columns[name]
        if isinstance(node, exp.Literal):
            if not node.is_string:
                number = Decimal(node.this)
                if not number.is_finite():
                    _reject()
            return 'text' if node.is_string else 'number'
        if isinstance(node, exp.Null):
            return 'null'
        if isinstance(node, exp.Boolean):
            return 'bool'
        if isinstance(node, exp.Cast):
            if type(node.this) is not exp.Literal or node.to.expressions:
                _reject()
            target = node.to.this.value
            if target == 'DATE' and node.this.is_string:
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', node.this.this):
                    _reject()
                date.fromisoformat(node.this.this)
                return 'date'
            if target == 'DECIMAL':
                number = Decimal(node.this.this)
                if not number.is_finite():
                    _reject()
                return 'number'
            _reject()
        if isinstance(node, exp.Count):
            if node.expressions:
                _reject()
            if isinstance(node.this, exp.Star):
                if any(node.this.args.values()):
                    _reject()
            else:
                self._type(node.this, columns, qualifier)
            self._reject_nested_aggregate(node)
            return 'number'
        if isinstance(node, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
            value_type = self._type(node.this, columns, qualifier)
            if isinstance(node, (exp.Sum, exp.Avg)) and value_type != 'number':
                _reject()
            if node.expressions or value_type in {'null', 'bool'}:
                _reject()
            self._reject_nested_aggregate(node)
            return value_type
        if isinstance(node, exp.Coalesce):
            children = [node.this, *node.expressions]
            if len(children) < 2:
                _reject()
            types = {self._type(child, columns, qualifier) for child in children} - {'null'}
            if len(types) != 1:
                _reject()
            return types.pop()
        if isinstance(node, exp.Round):
            if self._type(node.this, columns, qualifier) != 'number':
                _reject()
            precision = node.args.get('decimals')
            if precision is not None:
                if type(precision) is not exp.Literal or precision.is_string or not re.fullmatch(r'\d+', precision.this):
                    _reject()
                if int(precision.this) > 20:
                    _reject()
            return 'number'
        if isinstance(node, exp.TimestampTrunc):
            if node.args.get('zone') or self._type(node.this, columns, qualifier) != 'date':
                _reject()
            if not isinstance(node.args.get('unit'), exp.Var) or node.args['unit'].name.upper() not in {'DAY', 'MONTH', 'YEAR'}:
                _reject()
            return 'date'
        if isinstance(node, exp.TimeToStr):
            if self._type(node.this, columns, qualifier) not in {'date', 'number'}:
                _reject()
            fmt = node.args.get('format')
            if not isinstance(fmt, exp.Literal) or not fmt.is_string or len(fmt.this) > 64:
                _reject()
            return 'text'
        if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
            if self._type(node.this, columns, qualifier) != 'number' or self._type(node.expression, columns, qualifier) != 'number':
                _reject()
            return 'number'
        if isinstance(node, exp.Neg):
            if self._type(node.this, columns, qualifier) != 'number':
                _reject()
            return 'number'
        if isinstance(node, (exp.And, exp.Or)):
            if any(self._type(child, columns, qualifier) != 'bool' for child in (node.this, node.expression)):
                _reject()
            return 'bool'
        if isinstance(node, exp.Not):
            if self._type(node.this, columns, qualifier) != 'bool':
                _reject()
            return 'bool'
        if isinstance(node, _COMPARISONS):
            left = self._type(node.this, columns, qualifier)
            right = self._type(node.expression, columns, qualifier)
            self._compatible(left, right, node.this, node.expression)
            return 'bool'
        if isinstance(node, exp.Is):
            self._type(node.this, columns, qualifier)
            if not isinstance(node.expression, exp.Null):
                _reject()
            return 'bool'
        if isinstance(node, exp.In):
            left = self._type(node.this, columns, qualifier)
            if not node.expressions or any(value and key not in {'this', 'expressions'} for key, value in node.args.items()):
                _reject()
            for child in node.expressions:
                _literal(child)
                self._compatible(left, self._type(child, columns, qualifier), node.this, child)
            return 'bool'
        if isinstance(node, exp.Between):
            left = self._type(node.this, columns, qualifier)
            if node.args.get('symmetric'):
                _reject()
            for key in ('low', 'high'):
                child = node.args[key]
                _literal(child)
                self._compatible(left, self._type(child, columns, qualifier), node.this, child)
            return 'bool'
        _reject()

    @staticmethod
    def _compatible(left: str, right: str, left_node: exp.Expression, right_node: exp.Expression) -> None:
        if left == right and left != 'null':
            return
        # 날짜 비교에서 PostgreSQL의 날짜 리터럴 해석만 좁게 허용함.
        if {left, right} == {'date', 'text'}:
            literal = right_node if right == 'text' else left_node
            if type(literal) is exp.Literal and literal.is_string and re.fullmatch(r'\d{4}-\d{2}-\d{2}', literal.this):
                date.fromisoformat(literal.this)
                return
        _reject()

    @staticmethod
    def _reject_nested_aggregate(node: exp.Expression) -> None:
        if any(isinstance(child, _AGGREGATES) for child in node.walk() if child is not node):
            _reject()

    @staticmethod
    def _check_grain(tree: exp.Select, plan: dict, view: str) -> None:
        grain = plan.get('grain')
        if grain not in _GROUP_KEYS:
            _reject()
        grouped = tree.args.get('group')
        aggregates = any(isinstance(node, _AGGREGATES) for expression in tree.expressions for node in expression.walk())
        if not aggregates and not grouped:
            if grain != _BASE_GRAINS[view.split('.')[1]]:
                _reject()
            return
        keys = set()
        group_sql = set()
        for expression in grouped.expressions if grouped else []:
            group_sql.add(expression.sql(dialect='postgres'))
            if isinstance(expression, exp.Column):
                keys.add('month' if expression.name == 'base_month' else expression.name)
            elif isinstance(expression, exp.TimestampTrunc) and expression.args['unit'].name.upper() == 'MONTH':
                keys.add('month')
            else:
                _reject()
        if keys != _GROUP_KEYS[grain] or (grain == 'aggregate' and grouped):
            _reject()
        for expression in tree.expressions:
            inner = expression.this if isinstance(expression, exp.Alias) else expression
            if not any(isinstance(node, _AGGREGATES) for node in inner.walk()):
                if inner.sql(dialect='postgres') not in group_sql:
                    _reject()
            else:
                for column in inner.find_all(exp.Column):
                    current = column.parent
                    while current is not None and current is not inner and not isinstance(current, _AGGREGATES):
                        current = current.parent
                    if not isinstance(current, _AGGREGATES) and column.sql(dialect='postgres') not in group_sql:
                        _reject()

    @staticmethod
    def _check_metrics(tree: exp.Select, plan: dict, slots: dict) -> None:
        """모델이 합계나 카드 수라는 이름으로 다른 계산을 숨기지 못하게 함."""
        metrics = slots.get('metrics') or []
        if plan.get('route') == 'fixed' or not metrics:
            return
        expressions = [item.this if isinstance(item, exp.Alias) else item for item in tree.expressions]
        if plan.get('grain') != 'aggregate':
            for metric in metrics:
                if not any(type(item) is exp.Column and _name(item.this) == metric for item in expressions):
                    _reject()
            return
        expected = {'sum': exp.Sum, 'avg': exp.Avg, 'min': exp.Min, 'max': exp.Max,
                    'count': exp.Count}.get(str(slots.get('intent', '')))
        for metric in metrics:
            found = False
            for item in expressions:
                inner = item
                while isinstance(inner, (exp.Round, exp.Coalesce)):
                    inner = inner.this
                if metric == 'card_count':
                    if type(inner) is exp.Count and type(inner.this) is exp.Star:
                        found = True
                elif isinstance(inner, _AGGREGATES) and type(inner.this) is exp.Column:
                    if _name(inner.this.this) == metric and (expected is None or type(inner) is expected):
                        found = True
            if not found:
                _reject()

    def _guarantees(self, node: exp.Expression | None) -> set[tuple]:
        """OR 양쪽에 공통으로 있는 조건만 필수 조건으로 인정함."""
        if node is None:
            return set()
        if isinstance(node, (exp.Where, exp.Paren)):
            return self._guarantees(node.this)
        if isinstance(node, exp.And):
            return self._guarantees(node.this) | self._guarantees(node.expression)
        if isinstance(node, exp.Or):
            return self._guarantees(node.this) & self._guarantees(node.expression)
        if isinstance(node, _COMPARISONS) and isinstance(node.this, exp.Column):
            try:
                return {(node.this.name, type(node).__name__, _literal(node.expression))}
            except RetrieverError:
                return set()
        if isinstance(node, exp.Between) and isinstance(node.this, exp.Column):
            return {(node.this.name, 'GTE', _literal(node.args['low'])),
                    (node.this.name, 'LTE', _literal(node.args['high']))}
        if isinstance(node, exp.In) and isinstance(node.this, exp.Column):
            return {(node.this.name, 'IN', tuple(_literal(child) for child in node.expressions))}
        return set()

    def _check_conditions(self, tree: exp.Select, plan: dict, slots: dict, fixed: bool) -> None:
        guaranteed = self._guarantees(tree.args.get('where'))
        permitted: set[tuple] = set()
        if slots.get('grain') is not None and slots['grain'] != plan.get('grain'):
            _reject()
        status = slots.get('status_filter')
        if status is not None:
            permitted.add(('current_status', 'EQ', status))
            if ('current_status', 'EQ', status) not in guaranteed:
                _reject()
        operators = {'=': 'EQ', '==': 'EQ', '!=': 'NEQ', '<>': 'NEQ', '>': 'GT', '>=': 'GTE', '<': 'LT', '<=': 'LTE', 'IN': 'IN'}
        for condition in slots.get('filters') or []:
            value = condition['value']
            if isinstance(value, list):
                value = tuple(value)
            operator = operators.get(str(condition.get('operator', condition.get('op', ''))).upper())
            if not operator or (condition['column'], operator, value) not in guaranteed:
                _reject()
            permitted.add((condition['column'], operator, value))
        start, end = plan.get('period_start'), plan.get('period_end')
        if (start is None) != (end is None):
            _reject()
        for key in ('period_start', 'period_end'):
            if slots.get(key) is not None and slots[key] != plan.get(key):
                _reject()
        if fixed:
            return
        # 현재 슬롯에는 집계 후 필터가 없으므로 HAVING을 임의로 보충할 수 없음.
        if tree.args.get('having'):
            _reject()
        if start is None:
            self._check_exact_predicates(tree.args.get('where'), permitted)
            return
        if not isinstance(start, str) or not isinstance(end, str):
            _reject()
        if date.fromisoformat(start) > date.fromisoformat(end):
            _reject()
        view = plan['view'].split('.')[1]
        field = {'customer_daily_usage': 'usage_date', 'monthly_usage': 'month',
                 'merchant_usage': 'month', 'customer_delinquency': 'as_of_date',
                 'customer_delinquency_history': 'as_of_date'}.get(view)
        if field is None:
            _reject()
        if field == 'month':
            start, end = start[:7], end[:7]
        exact_day = start == end and (field, 'EQ', start) in guaranteed
        if not exact_day and ((field, 'GTE', start) not in guaranteed or (field, 'LTE', end) not in guaranteed):
            _reject()
        permitted.update({(field, 'GTE', start), (field, 'LTE', end)})
        if start == end:
            permitted.add((field, 'EQ', start))
        self._check_exact_predicates(tree.args.get('where'), permitted)

    def _check_exact_predicates(self, node: exp.Expression | None, permitted: set) -> None:
        """질문에 없는 조건을 덧붙여 결과를 몰래 좁히는 것을 막음."""
        if node is None:
            return
        if isinstance(node, (exp.Where, exp.Paren)):
            self._check_exact_predicates(node.this, permitted)
            return
        if isinstance(node, (exp.And, exp.Or)):
            self._check_exact_predicates(node.this, permitted)
            self._check_exact_predicates(node.expression, permitted)
            return
        predicates = self._guarantees(node)
        if not predicates or not predicates <= permitted:
            _reject()

