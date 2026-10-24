import asyncio
from app.config import Settings
from app.metadata import MetadataService
from app.model_client import ModelClient


def decide(meta,q,previous=None):
    c=meta.conditions(q,previous or {})
    return asyncio.run(ModelClient(Settings(model_mode='mock')).generate({'question':q,'conditions':c},[]))


def test_lawyer_workload_is_clarified(tmp_path):
    out=decide(MetadataService(tmp_path),'2025年律师工作量排名')
    assert out.action=='clarify' and '工时' in out.question


def test_hours_ranking_orders_by_measure(tmp_path):
    out=decide(MetadataService(tmp_path),'2025年各律师已审核工时排名')
    assert 'ORDER BY approved_hours DESC' in out.sql


def test_specific_month_is_not_whole_year(tmp_path):
    c=MetadataService(tmp_path).conditions('2025年2月净回款',{})
    assert c['time_range']=={'start':'2025-02-01','end':'2025-03-01'}
