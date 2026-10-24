"""授权对象及可证明的局部指标规则；不声称能证明任意 SQL 的业务正确性。"""
from dataclasses import dataclass
import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope, Scope


class GuardError(ValueError):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str
    scope_id: str


class SQLGuard:
    FUNCTIONS = {'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'COALESCE', 'NULLIF', 'CASE', 'IF',
                 'DATE', 'DATE_FORMAT', 'YEAR', 'MONTH', 'DAY', 'QUARTER', 'DATEDIFF', 'TIMESTAMPDIFF',
                 'ROUND', 'ABS', 'CEIL', 'FLOOR', 'CAST', 'EXTRACT', 'CONCAT', 'LOWER', 'UPPER',
                 'AND', 'OR', 'EXISTS', 'TIME_TO_STR', 'TS_OR_DS_TO_TIMESTAMP', 'TS_OR_DS_TO_DATE', 'DATE_DIFF', 'TIMESTAMP_DIFF'}

    def __init__(self, schema: dict, metrics: list[dict]):
        self.schema = schema
        self.tables = {name.lower(): {col.lower(): 'UNKNOWN' for col in data['columns']} for name, data in schema['tables'].items()}
        self.metrics = {m['id'] for m in metrics}
        self.relations = set()
        for relation in schema.get('relationships', []):
            left, right = relation.split(' -> ')
            self.relations.add(frozenset((left.lower(), right.split()[0].lower())))

    def validate(self, sql: str, metric_ids: list[str], scope_id='demo') -> ValidatedQuery:
        def deny(code, message, retryable=False):
            raise GuardError(code, message, retryable)
        if scope_id != 'demo':
            deny('SQL_SCOPE_DENIED', '数据范围未授权')
        if not set(metric_ids).issubset(self.metrics):
            deny('SQL_METRIC_DENIED', '指标未批准')
        if not sql or len(sql) > 30000 or re.search(r'/\*|\*/|--|#', sql):
            deny('SQL_NOT_READ_ONLY', 'SQL为空、过长或包含注释')
        try:
            statements = sqlglot.parse(sql, read='mysql')
        except (sqlglot.errors.SqlglotError, RecursionError):
            deny('SQL_INVALID', 'SQL语法无法解析', True)
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
            deny('SQL_NOT_READ_ONLY', '仅允许单条只读SELECT')
        tree = statements[0]
        if len(list(tree.walk())) > 1500 or len(list(tree.find_all(exp.Select))) > 20:
            deny('SQL_TOO_COMPLEX', '查询复杂度超过上限')
        for node in tree.walk():
            if node.comments or isinstance(node, (exp.DML, exp.DDL, exp.Into, exp.Lock, exp.Command, exp.Parameter, exp.Var)):
                # 日期差的单位属于 Var，单独仅允许标准单位。
                if isinstance(node, exp.Var) and node.name.upper() in {'DAY', 'MONTH', 'YEAR', 'HOUR', 'MINUTE', 'SECOND'}:
                    continue
                deny('SQL_NOT_READ_ONLY', '查询包含不允许的操作')
            if isinstance(node, exp.Func):
                name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
                if name not in self.FUNCTIONS:
                    deny('SQL_FUNCTION_DENIED', '函数不在批准清单')
            if isinstance(node, exp.Table) and (node.db or node.catalog):
                deny('SQL_OBJECT_DENIED', '禁止跨schema对象')
            if isinstance(node, exp.Column) and (node.db or node.catalog):
                deny('SQL_OBJECT_DENIED', '禁止跨schema字段')
        scopes = list(traverse_scope(tree))
        for scope in scopes:
            for source in scope.sources.values():
                if isinstance(source, exp.Table) and source.name.lower() not in self.tables:
                    deny('SQL_OBJECT_DENIED', '对象不在批准清单')
        try:
            qualified = qualify(tree.copy(), dialect='mysql', schema=self.tables, quote_identifiers=False, identify=False)
        except (sqlglot.errors.OptimizeError, sqlglot.errors.SchemaError):
            deny('SQL_COLUMN_DENIED', '字段不存在、别名不正确或字段有歧义', True)
        self._metric_checks(qualified, metric_ids)
        self._join_checks(qualified)
        return ValidatedQuery(qualified.sql(dialect='mysql'), scope_id)

    def _join_checks(self, tree):
        def origin(scope, col, depth=0):
            if depth > 20:
                return None
            source = scope.sources.get(col.table)
            if isinstance(source, exp.Table):
                return f'{source.name.lower()}.{col.name.lower()}'
            if isinstance(source, Scope):
                for projection in source.expression.selects:
                    raw = projection.this if isinstance(projection, exp.Alias) else projection
                    if projection.alias_or_name == col.name and isinstance(raw, exp.Column):
                        return origin(source, raw, depth + 1)
            if source is None and scope.parent:
                return origin(scope.parent, col, depth + 1)
            return None
        for scope in traverse_scope(tree):
            for join in scope.expression.args.get('joins', []):
                if join.args.get('using') or not join.args.get('on'):
                    raise GuardError('SQL_JOIN_DENIED', '关联必须使用批准的显式键')
                if any(join.args['on'].find_all(exp.Or)):
                    raise GuardError('SQL_JOIN_DENIED', '关联条件不能通过OR绕过批准键')
                matched = False
                for eq in join.args['on'].find_all(exp.EQ):
                    if not isinstance(eq.left, exp.Column) or not isinstance(eq.right, exp.Column):
                        continue
                    pair = [origin(scope, col) for col in (eq.left, eq.right)]
                    if None not in pair and frozenset(pair) in self.relations and join.this.alias_or_name in (eq.left.table, eq.right.table):
                        matched = True
                if not matched:
                    raise GuardError('SQL_JOIN_DENIED', '关联键不在批准关系中')
            where = scope.expression.args.get('where')
            if where:
                for eq in where.find_all(exp.EQ):
                    if eq.find_ancestor(exp.Select) is not scope.expression:
                        continue
                    if isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column) and eq.left.table != eq.right.table:
                        pair = [origin(scope, col) for col in (eq.left, eq.right)]
                        if None in pair or frozenset(pair) not in self.relations:
                            raise GuardError('SQL_JOIN_DENIED', '关联子查询必须使用批准关联键')

    def _metric_checks(self, tree, metric_ids):
        def fail():
            raise GuardError('SQL_METRIC_MISMATCH', '查询不满足批准指标的状态、时间、符号或聚合粒度规则', True)
        metric_ids = set(metric_ids)
        scopes = list(traverse_scope(tree))
        def lineage(scope, column, depth=0):
            if depth > 20:
                fail()
            source = scope.sources.get(column.table)
            if isinstance(source, exp.Table):
                return {(source.name.lower(), column.name.lower())}
            if isinstance(source, Scope):
                result = set()
                for projection in source.expression.selects:
                    if projection.alias_or_name == column.name:
                        for inner in projection.find_all(exp.Column):
                            result |= lineage(source, inner, depth + 1)
                return result
            return set()
        # 不信任模型提交的指标标签：追溯派生列，避免把 amount 改名后漏过规则。
        for scope in scopes:
            for aggregate in scope.expression.find_all(exp.AggFunc):
                if aggregate.find_ancestor(exp.Select) is not scope.expression:
                    continue
                origins = set()
                for col in aggregate.find_all(exp.Column):
                    origins |= lineage(scope, col)
                if ('v_receipt', 'amount') in origins:
                    metric_ids.add('_receipt_total')
                if ('v_worklog', 'hours') in origins:
                    metric_ids.add('approved_hours')
        columns = {c.name.lower() for c in tree.find_all(exp.Column)}
        times = {'new_cases': 'opened_at', 'closed_cases': 'closed_at', 'net_receipts': 'received_at',
                 'approved_hours': 'worked_at', 'avg_close_days': 'closed_at', 'participating_cases': 'opened_at',
                 'contract_fee': 'opened_at', '_receipt_total': 'received_at'}
        # 时间列须实际出现在筛选条件，而不是仅投影给用户看。
        filters = list(tree.find_all(exp.Where)) + [j.args['on'] for j in tree.find_all(exp.Join) if j.args.get('on')]
        def conjunctions(node):
            if isinstance(node, (exp.Where, exp.Paren)):
                return conjunctions(node.this)
            if isinstance(node, exp.And):
                return conjunctions(node.left) + conjunctions(node.right)
            return [node]
        filtered_columns = {c.name.lower() for clause in filters for predicate in conjunctions(clause)
                            if isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.EQ))
                            for c in predicate.find_all(exp.Column)}
        for metric in metric_ids:
            if metric in times and times[metric] not in filtered_columns:
                fail()
        for scope in scopes:
            physical = {alias: source.name.lower() for alias, source in scope.sources.items() if isinstance(source, exp.Table)}
            if {'v_receipt', 'v_worklog'}.issubset(set(physical.values())) and any(scope.expression.find_all(exp.Sum)):
                fail()
            if any(list(physical.values()).count(table) > 1 for table in ('v_receipt', 'v_worklog')) and any(scope.expression.find_all(exp.AggFunc)):
                fail()
            clauses = ([scope.expression.args['where'].this] if scope.expression.args.get('where') else [])
            clauses += [j.args['on'] for j in scope.expression.args.get('joins', []) if j.args.get('on')]
            predicates = [p for clause in clauses for p in conjunctions(clause)]
            def required_status(table, status):
                aliases = {a for a, t in physical.items() if t == table}
                if not aliases:
                    return
                # OR可能绕开状态限定，保守拒绝；复杂写法可修复为单独CTE。
                for alias in aliases:
                    if not any(isinstance(eq.left, exp.Column) and eq.left.table == alias and eq.left.name == 'status'
                               and isinstance(eq.right, exp.Literal) and eq.right.this == status for eq in predicates if isinstance(eq, exp.EQ)):
                        fail()
            if metric_ids.intersection({'net_receipts', 'cohort_collection_rate', '_receipt_total'}):
                required_status('v_receipt', 'CONFIRMED')
                if 'v_receipt' in physical.values():
                    negative_refund = False
                    signed_cases = []
                    for case in scope.expression.find_all(exp.Case):
                        branches = case.args.get('ifs', [])
                        if len(branches) != 1:
                            continue
                        branch = branches[0]
                        condition, positive, otherwise = branch.this, branch.args['true'], case.args.get('default')
                        if not (isinstance(condition, exp.EQ) and isinstance(condition.left, exp.Column)
                                and condition.left.name == 'receipt_type' and isinstance(condition.right, exp.Literal)):
                            continue
                        def amount(expr):
                            return isinstance(expr, exp.Column) and expr.name == 'amount'
                        def minus_amount(expr):
                            return isinstance(expr, exp.Neg) and amount(expr.this)
                        valid_sign = ((condition.right.this == 'REFUND' and minus_amount(positive) and amount(otherwise))
                                      or (condition.right.this == 'PAYMENT' and amount(positive) and minus_amount(otherwise)))
                        negative_refund |= valid_sign
                        if valid_sign:
                            signed_cases.append(case)
                    isolated_type = any(isinstance(p, exp.EQ) and isinstance(p.left, exp.Column)
                                        and p.left.name == 'receipt_type' and isinstance(p.right, exp.Literal)
                                        and p.right.this in ('PAYMENT', 'REFUND') for p in predicates)
                    if not negative_refund and (not isolated_type or metric_ids.intersection({'net_receipts', 'cohort_collection_rate'})):
                        fail()
                    if not isolated_type or metric_ids.intersection({'net_receipts', 'cohort_collection_rate'}):
                        for projection in scope.expression.selects:
                            for col in projection.find_all(exp.Column):
                                if col.name == 'amount' and physical.get(col.table) == 'v_receipt':
                                    if not any(case is col.find_ancestor(exp.Case) for case in signed_cases):
                                        fail()
            if 'approved_hours' in metric_ids:
                required_status('v_worklog', 'APPROVED')
            if 'closed_cases' in metric_ids or 'avg_close_days' in metric_ids:
                required_status('v_case', 'CLOSED')
            if 'open_cases' in metric_ids:
                required_status('v_case', 'OPEN')
            if metric_ids.intersection({'new_cases', 'contract_fee'}) and 'v_case' in physical.values():
                aliases = {a for a, t in physical.items() if t == 'v_case'}
                for alias in aliases:
                    if not any(isinstance(p, exp.NEQ) and isinstance(p.left, exp.Column)
                               and p.left.table == alias and p.left.name == 'status'
                               and isinstance(p.right, exp.Literal) and p.right.this == 'CANCELLED' for p in predicates):
                        # 单独统计CLOSED/OPEN时也已经排除撤销。
                        if not any(isinstance(p, exp.EQ) and isinstance(p.left, exp.Column)
                                   and p.left.table == alias and p.left.name == 'status'
                                   and isinstance(p.right, exp.Literal) and p.right.this in ('CLOSED', 'OPEN') for p in predicates):
                            fail()
            if 'participating_cases' in metric_ids and 'v_case_lawyer' in physical.values() and any(scope.expression.find_all(exp.Count)):
                if not any(isinstance(c.this, exp.Distinct) and any(x.name == 'case_id' for x in c.find_all(exp.Column)) for c in scope.expression.find_all(exp.Count)):
                    fail()
            if any(t in physical.values() for t in ('v_receipt', 'v_worklog', 'v_case_lawyer')):
                for count in scope.expression.find_all(exp.Count):
                    if 'v_case' in physical.values() and isinstance(count.this, exp.Star):
                        fail()
                    if any(c.name == 'case_id' and physical.get(c.table) == 'v_case' for c in count.find_all(exp.Column)) and not isinstance(count.this, exp.Distinct):
                        fail()
