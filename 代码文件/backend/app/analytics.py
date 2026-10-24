"""独立分析连接；SQLite仅支持兼容单元测试，MySQL用于正式验收。"""
from datetime import date, datetime
from decimal import Decimal
import json
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from .sql_guard import ValidatedQuery


class AnalyticsError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable = False


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value


def explain_query(conn, sql, mysql):
    try:
        conn.exec_driver_sql(('EXPLAIN ' if mysql else 'EXPLAIN QUERY PLAN ') + sql,
                             execution_options={'no_parameters': True}).fetchall()
        return 'passed'
    except DBAPIError as error:
        # MySQL明确要求EXPLAIN视图者同时有底表SELECT；保持reader边界并记录未预检。
        if mysql and getattr(error.orig, 'args', [None])[0] == 1345:
            return 'unavailable_view_permissions'
        raise


class Analytics:
    def __init__(self, database_url: str, timeout_seconds=10, max_rows=1000, max_bytes=1000000):
        self.timeout_seconds = max(1, min(int(timeout_seconds), 10))
        self.max_rows = max(1, min(int(max_rows), 1000))
        self.max_bytes = max(1, min(int(max_bytes), 1000000))
        kwargs = {'pool_pre_ping': True, 'hide_parameters': True}
        if database_url.startswith('mysql'):
            kwargs['connect_args'] = {'connect_timeout': self.timeout_seconds, 'read_timeout': self.timeout_seconds, 'write_timeout': self.timeout_seconds}
        self.engine = create_engine(database_url, **kwargs)

    def _watermark(self, conn, scope_id):
        value = conn.execute(text('SELECT data_watermark FROM sync_watermark WHERE scope_id=:scope_id'), {'scope_id': scope_id}).scalar_one_or_none()
        if value is None:
            raise AnalyticsError('DATA_WATERMARK_MISSING', '同步水位尚未配置')
        return json_value(value)

    def get_watermark(self, scope_id='demo'):
        try:
            with self.engine.connect() as conn:
                return self._watermark(conn, scope_id)
        except DBAPIError:
            raise AnalyticsError('DATABASE_UNAVAILABLE', '分析数据库暂不可用') from None

    def execute(self, validated, timeout_seconds=None) -> dict:
        if not isinstance(validated, ValidatedQuery) or validated.scope_id != 'demo':
            raise AnalyticsError('QUERY_NOT_VALIDATED', '仅接受当前授权范围内已校验的查询')
        budget = min(self.timeout_seconds, float(timeout_seconds)) if timeout_seconds is not None else self.timeout_seconds
        if budget < 0.05:
            raise AnalyticsError('QUERY_TIMEOUT', '本次查询预算已用尽，请重试')
        start = time.monotonic()
        try:
            with self.engine.connect() as conn:
                mysql = conn.dialect.name == 'mysql'
                if mysql:
                    conn.exec_driver_sql(f'SET SESSION MAX_EXECUTION_TIME={max(1, int(budget * 1000))}')
                    conn.commit()
                    conn.exec_driver_sql('START TRANSACTION READ ONLY')
                else:
                    conn.exec_driver_sql('PRAGMA query_only=ON')
                try:
                    sql = f'SELECT * FROM ({validated.sql.rstrip(";")}) AS bounded_result LIMIT {self.max_rows + 1}'
                    explain = explain_query(conn, sql, mysql)
                    watermark = self._watermark(conn, validated.scope_id)
                    result = conn.exec_driver_sql(sql, execution_options={'no_parameters': True})
                    columns = list(result.keys())
                    if len(set(columns)) != len(columns):
                        raise AnalyticsError('AMBIGUOUS_RESULT', '查询结果字段重名，请为字段设置不同别名')
                    rows, truncated, byte_count = [], False, 0
                    for row in result.fetchmany(self.max_rows + 1):
                        record = {column: json_value(value) for column, value in zip(columns, row)}
                        size = len(json.dumps(record, ensure_ascii=False).encode('utf-8'))
                        if len(rows) == self.max_rows or byte_count + size > self.max_bytes:
                            truncated = True
                            break
                        rows.append(record)
                        byte_count += size
                    return {'columns': columns, 'rows': rows, 'row_count': len(rows), 'truncated': truncated,
                            'data_watermark': watermark, 'elapsed_ms': round((time.monotonic() - start) * 1000),
                            'execution_checks': {'explain': explain, 'read_only': True, 'ast_validated': True}}
                finally:
                    conn.rollback()
        except DBAPIError as error:
            code = getattr(error.orig, 'args', [None])[0]
            if code in (3024, 1317, 2013):
                raise AnalyticsError('QUERY_TIMEOUT', '本次查询超时，请缩小范围') from None
            if code in (1054, 1146):
                raise AnalyticsError('SCHEMA_CHANGED', '数据配置已变化，需管理员检查') from None
            raise AnalyticsError('DATABASE_UNAVAILABLE', '分析数据库暂不可用') from None
