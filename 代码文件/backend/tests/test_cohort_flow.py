import asyncio
from app.config import Settings
from app.metadata import MetadataService
from app.model_client import ModelClient


def test_cohort_closed_uses_confirmed_cutoff(tmp_path):
    meta=MetadataService(tmp_path)
    q='2025年新收案件截至2026年1月1日零点的结案率'
    c=meta.conditions(q,{})
    assert c['time_range']=={'start':'2025-01-01','end':'2026-01-01'}
    assert c['as_of']=='2026-01-01'
    out=asyncio.run(ModelClient(Settings(model_mode='mock')).generate({'question':q,'conditions':c},[]))
    assert out.action=='sql'
    assert "c.closed_at<'2026-01-01'" in out.sql
    assert 'NULLIF(COUNT(*),0)' in out.sql


def test_cohort_collection_does_not_join_raw_receipts(tmp_path):
    meta=MetadataService(tmp_path)
    q='2025年新收案件截至2026年1月1日零点的回款率'
    c=meta.conditions(q,{})
    out=asyncio.run(ModelClient(Settings(model_mode='mock')).generate({'question':q,'conditions':c},[]))
    assert out.action=='sql'
    assert 'WITH paid AS' in out.sql and 'GROUP BY r.case_id' in out.sql
    assert 'SUM(c.contracted_fee)' in out.sql
