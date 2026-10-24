import asyncio
from pathlib import Path

from app.config import Settings
from app.metadata import MetadataService
from app.model_client import ModelClient


def test_clarification_year_inherits_net_receipts(tmp_path):
    meta = MetadataService(tmp_path)
    old = meta.conditions('净回款多少', {})
    first = asyncio.run(ModelClient(Settings(model_mode='mock')).generate({'question':'净回款多少','conditions':old}, []))
    assert first.action == 'clarify'
    new = meta.conditions('2025全年', old, pending=True)
    out = asyncio.run(ModelClient(Settings(model_mode='mock')).generate({'question':'2025全年','conditions':new}, []))
    assert out.action == 'sql'
    assert "r.status='CONFIRMED'" in out.sql
    assert "ELSE -r.amount" in out.sql


def test_team_followup_keeps_period_changes_dimension(tmp_path):
    meta = MetadataService(tmp_path)
    old = meta.conditions('2025年净回款按团队统计', {})
    followup = meta.conditions('只看劳动团队，按月展示', old)
    assert followup['time_range']['start'] == '2025-01-01'
    assert followup['metric_ids'] == ['net_receipts']
    assert followup['group_by'] == 'month'
    assert followup['filters']['team'] == '劳动团队'


def test_two_facts_query_preaggregates(tmp_path):
    meta = MetadataService(tmp_path)
    c = meta.conditions('各案件2025年净回款与已审核工时', {})
    out = asyncio.run(ModelClient(Settings(model_mode='mock')).generate({'question':'各案件2025年净回款与已审核工时','conditions':c}, []))
    assert out.action == 'sql'
    assert 'WITH paid AS' in out.sql and 'worked AS' in out.sql
    assert 'GROUP BY r.case_id' in out.sql and 'GROUP BY w.case_id' in out.sql


def test_new_question_does_not_keep_old_team(tmp_path):
    meta = MetadataService(tmp_path)
    old = meta.conditions('2025年劳动团队净回款', {})
    new = meta.conditions('当前在办案件多少', old)
    assert new['metric_ids'] == ['open_cases']
    assert 'team' not in new['filters']
