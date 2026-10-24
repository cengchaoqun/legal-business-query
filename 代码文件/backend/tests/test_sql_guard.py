import json
from pathlib import Path

import pytest

from app.sql_guard import SQLGuard, GuardError


@pytest.fixture
def guard():
    root = Path(__file__).resolve().parents[2] / '交付方案' / '微调数据'
    return SQLGuard(json.loads((root / 'schema.json').read_text('utf-8')), json.loads((root / 'metrics.json').read_text('utf-8')))


@pytest.mark.parametrize('sql', [
    "SELECT COUNT(*) AS n FROM v_case WHERE status='OPEN'",
    "WITH x AS (SELECT case_id FROM v_case) SELECT COUNT(*) FROM x",
    "SELECT case_id FROM v_case UNION ALL SELECT case_id FROM v_case",
    "SELECT c.case_id FROM v_case c WHERE EXISTS (SELECT 1 FROM v_receipt r WHERE r.case_id=c.case_id)",
])
def test_approved_readonly_queries(guard, sql):
    assert guard.validate(sql, []).scope_id == 'demo'


@pytest.mark.parametrize('sql', [
    'DELETE FROM v_case', 'SELECT * FROM mysql.user', 'SELECT * FROM other.v_case',
    'SELECT * FROM v_case; SELECT 1', 'SELECT SLEEP(1)', 'SELECT LOAD_FILE("/etc/passwd")',
    'SELECT * FROM v_case FOR UPDATE', "SELECT * INTO OUTFILE '/tmp/x' FROM v_case",
    'SELECT /* hi */ * FROM v_case', 'SELECT missing FROM v_case',
    'WITH x AS (SELECT secret FROM v_case) SELECT * FROM x',
    'SELECT z.case_id FROM v_case c', 'SELECT unknown_function(case_id) FROM v_case',
    'SELECT * FROM v_case c JOIN v_team t ON c.case_id=t.team_id',
    "SELECT 'unterminated",
])
def test_unsafe_queries_are_denied(guard, sql):
    with pytest.raises(GuardError):
        guard.validate(sql, [])


def test_unknown_scope_and_metric_fail_closed(guard):
    with pytest.raises(GuardError):
        guard.validate('SELECT 1', [], 'another_tenant')
    with pytest.raises(GuardError):
        guard.validate('SELECT 1', ['invented'])


@pytest.mark.parametrize('sql,metric', [
    ('SELECT SUM(amount) FROM v_receipt', 'net_receipts'),
    ("SELECT SUM(amount) FROM v_receipt WHERE status='CONFIRMED' AND received_at>='2025-01-01'", 'net_receipts'),
    ("SELECT SUM(hours) FROM v_worklog WHERE status='PENDING'", 'approved_hours'),
    ("SELECT COUNT(c.case_id) FROM v_case c JOIN v_case_lawyer l ON l.case_id=c.case_id WHERE c.opened_at>='2025-01-01'", 'participating_cases'),
    ("SELECT SUM(r.amount) FROM v_receipt r JOIN v_worklog w ON r.case_id=w.case_id", 'net_receipts'),
])
def test_obvious_metric_errors_are_rejected(guard, sql, metric):
    with pytest.raises(GuardError) as error:
        guard.validate(sql, [metric])
    assert error.value.code == 'SQL_METRIC_MISMATCH'


def test_valid_net_receipts(guard):
    sql = "SELECT COALESCE(SUM(CASE WHEN receipt_type='REFUND' THEN -amount ELSE amount END),0) FROM v_receipt WHERE status='CONFIRMED' AND received_at >= '2025-01-01' AND received_at < '2026-01-01'"
    assert guard.validate(sql, ['net_receipts']).sql


def test_delivered_reference_sql_is_supported(guard):
    path = Path(__file__).resolve().parents[2] / '交付方案/微调数据/seed_records.jsonl'
    for line in path.read_text('utf-8').splitlines():
        record = json.loads(line)
        decision = json.loads(record['messages'][-1]['content'])
        if decision['action'] == 'sql':
            try:
                guard.validate(decision['sql'], decision['metric_ids'])
            except GuardError as error:
                pytest.fail(f'{record["id"]}: {error.code}')


@pytest.mark.parametrize('sql', [
    'SELECT * FROM v_case c JOIN v_team t ON c.owner_team_id=t.team_id OR 1=1',
    'SELECT SUM(r.amount) FROM v_receipt r JOIN v_case_lawyer l ON r.case_id=l.case_id',
    'SELECT COUNT(*) FROM v_case c JOIN v_case_lawyer l ON c.case_id=l.case_id',
    'SELECT @secret',
])
def test_join_fanout_and_session_variables_are_rejected(guard, sql):
    with pytest.raises(GuardError):
        guard.validate(sql, [])


@pytest.mark.parametrize('sql', [
    'SELECT SUM(amount) FROM v_receipt',
    "SELECT SUM(hours) FROM v_worklog WHERE worked_at>='2025-01-01'",
    "WITH x AS (SELECT amount AS renamed FROM v_receipt) SELECT SUM(renamed) FROM x",
    "SELECT SUM(r.amount) FROM v_case c JOIN v_receipt r ON r.case_id=c.case_id JOIN v_receipt s ON s.case_id=c.case_id",
    "SELECT SUM(w.hours) FROM v_case c JOIN v_worklog w ON w.case_id=c.case_id JOIN v_worklog x ON x.case_id=c.case_id",
    "SELECT SUM(CASE WHEN receipt_type='REFUND' THEN -amount ELSE amount END) FROM v_receipt WHERE status='CONFIRMED' OR received_at>'2025-01-01'",
])
def test_empty_metric_ids_do_not_bypass_financial_and_fanout_rules(guard, sql):
    with pytest.raises(GuardError) as error:
        guard.validate(sql, [])
    assert error.value.code == 'SQL_METRIC_MISMATCH'


@pytest.mark.parametrize('sql,metrics', [
    ("SELECT SUM(amount)+SUM(CASE WHEN receipt_type='REFUND' THEN -amount ELSE amount END) FROM v_receipt WHERE status='CONFIRMED' AND received_at>'2025-01-01'", []),
    ("SELECT SUM(CASE WHEN receipt_type='REFUND' THEN -amount ELSE amount END) FROM v_receipt WHERE status='CONFIRMED' AND (received_at>'2025-01-01' OR 1=1)", []),
    ("SELECT COUNT(*) FROM v_case WHERE opened_at>'2025-01-01'", ['new_cases']),
])
def test_metric_predicates_cannot_be_decoys(guard, sql, metrics):
    with pytest.raises(GuardError):
        guard.validate(sql, metrics)


@pytest.mark.parametrize('sql', [
    'SELECT c.case_id FROM v_case c JOIN v_team t ON c.owner_team_id=t.team_id JOIN v_lawyer l ON c.owner_team_id=t.team_id',
    'SELECT c.case_id FROM v_case c WHERE EXISTS (SELECT 1 FROM v_receipt r WHERE r.case_id=c.owner_team_id)',
])
def test_join_keys_must_link_actual_join_and_correlation(guard, sql):
    with pytest.raises(GuardError):
        guard.validate(sql, [])


def test_quarter_group_function(guard):
    sql = "SELECT CONCAT(YEAR(received_at),'-Q',QUARTER(received_at)) AS quarter, SUM(CASE WHEN receipt_type='REFUND' THEN -amount ELSE amount END) AS net FROM v_receipt WHERE status='CONFIRMED' AND received_at>='2025-01-01' GROUP BY quarter"
    assert guard.validate(sql, ['net_receipts']).sql
