from decimal import Decimal
import os

import pytest
from sqlalchemy import create_engine, text

from app.analytics import Analytics, AnalyticsError, json_value
from app.sql_guard import ValidatedQuery


def test_decimal_and_null_preserve_meaning():
    assert json_value(Decimal('123.40')) == '123.40'
    assert json_value(None) is None


def test_sqlite_compatibility_unit_result_limits_and_real_watermark(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'test.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE v_team(team_id INTEGER, team_name TEXT)'))
        conn.execute(text("INSERT INTO v_team VALUES(1,'甲'),(2,'乙'),(3,'丙')"))
        conn.execute(text('CREATE TABLE sync_watermark(scope_id TEXT, data_watermark TEXT)'))
        conn.execute(text("INSERT INTO sync_watermark VALUES('demo','2026-10-21T00:00:00+08:00')"))
    analytics = Analytics(url, max_rows=2)
    result = analytics.execute(ValidatedQuery('SELECT * FROM v_team ORDER BY team_id', 'demo'))
    assert result['row_count'] == 2 and result['truncated'] is True
    assert result['columns'] == ['team_id', 'team_name']
    assert result['data_watermark'] == '2026-10-21T00:00:00+08:00'


def test_database_error_is_sanitized(tmp_path):
    analytics = Analytics('sqlite:///' + str(tmp_path / 'empty.db'))
    with pytest.raises(AnalyticsError) as error:
        analytics.execute(ValidatedQuery('SELECT * FROM secret_table', 'demo'))
    assert 'secret_table' not in str(error.value)


def test_execution_requires_validated_object(tmp_path):
    analytics = Analytics('sqlite:///' + str(tmp_path / 'empty.db'))
    with pytest.raises(AnalyticsError):
        analytics.execute('SELECT 1')


def test_initialize_empty_sandbox_and_refuse_overwrite(tmp_path):
    from pathlib import Path
    from app.init_analytics import initialize
    url = 'sqlite:///' + str(tmp_path / 'fixture.db')
    fixtures = Path(__file__).resolve().parents[2] / '交付方案' / '微调数据'
    initialize(url, fixtures, '2026-10-21T00:00:00+08:00')
    result = Analytics(url).execute(ValidatedQuery('SELECT COUNT(*) AS n FROM v_case', 'demo'))
    assert result['rows'][0]['n'] == 7
    with pytest.raises(ValueError):
        initialize(url, fixtures, '2026-10-22T00:00:00+08:00')


def test_exhausted_execution_budget_does_not_open_database(tmp_path):
    analytics = Analytics('sqlite:///' + str(tmp_path / 'empty.db'))
    with pytest.raises(AnalyticsError) as error:
        analytics.execute(ValidatedQuery('SELECT 1', 'demo'), timeout_seconds=0)
    assert error.value.code == 'QUERY_TIMEOUT'


def test_explain_1345_is_explicitly_reported_and_other_errors_fail(tmp_path):
    from sqlalchemy.exc import OperationalError
    from app.analytics import explain_query
    class Connection:
        def __init__(self, code):
            self.code = code
        def exec_driver_sql(self, sql, **kwargs):
            raise OperationalError(sql, {}, Exception(self.code, 'private details'))
    assert explain_query(Connection(1345), 'SELECT 1', True) == 'unavailable_view_permissions'
    with pytest.raises(OperationalError):
        explain_query(Connection(1054), 'SELECT 1', True)


@pytest.mark.skipif(os.getenv('LEGAL_MYSQL_TEST') != '1', reason='需显式启动独立MySQL演示沙箱')
def test_mysql_all_reference_queries_and_execution_boundaries():
    import json
    from pathlib import Path
    from app.config import Settings
    from app.sql_guard import SQLGuard
    fixture = Path(__file__).resolve().parents[2] / '交付方案/微调数据'
    guard = SQLGuard(json.loads((fixture / 'schema.json').read_text('utf-8')), json.loads((fixture / 'metrics.json').read_text('utf-8')))
    references = {r['id']: r for r in json.loads((fixture / 'reference_results.json').read_text('utf-8'))}
    analytics = Analytics(Settings().analytics_database_url)
    def comparable(value):
        if value is None:
            return None
        try:
            return Decimal(str(value)).quantize(Decimal('0.000001'))
        except Exception:
            return value
    for line in (fixture / 'seed_records.jsonl').read_text('utf-8').splitlines():
        record = json.loads(line)
        decision = json.loads(record['messages'][-1]['content'])
        if decision['action'] != 'sql':
            continue
        result = analytics.execute(guard.validate(decision['sql'], decision['metric_ids']))
        expected = references[record['id']]
        assert result['columns'] == expected['columns'], record['id']
        actual_rows = [tuple(comparable(row[c]) for c in result['columns']) for row in result['rows']]
        expected_rows = [tuple(comparable(v) for v in row) for row in expected['rows']]
        assert sorted(actual_rows, key=str) == sorted(expected_rows, key=str), record['id']
        assert result['data_watermark']
        assert result['execution_checks']['explain'] in ('passed', 'unavailable_view_permissions')
    bounded = Analytics(Settings().analytics_database_url, max_rows=2)
    result = bounded.execute(guard.validate('SELECT case_id FROM v_case ORDER BY case_id', []))
    assert result['row_count'] == 2 and result['truncated']
    with pytest.raises(AnalyticsError):
        analytics.execute(ValidatedQuery('SELECT * FROM demo_case', 'demo'))
