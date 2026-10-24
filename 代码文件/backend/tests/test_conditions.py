import pytest

from app.metadata import MetadataService
from app.query_service import enforce_conditions
from app.schemas import ModelDecision
from app.sql_guard import GuardError


def test_team_name_does_not_imply_case_type(tmp_path):
    c=MetadataService(tmp_path).conditions('2025年劳动团队净回款',{})
    assert c['filters']=={'team':'劳动团队'}


def test_literal_in_projection_cannot_fake_filter():
    out=ModelDecision(action='sql',sql="SELECT '劳动团队' AS label, SUM(r.amount) AS amount FROM v_receipt r",reason='test',metric_ids=['net_receipts'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'filters':{'team':'劳动团队'}},out)


def test_projected_dates_cannot_fake_range():
    out=ModelDecision(action='sql',sql="SELECT '2025-01-01' AS a,'2026-01-01' AS b,SUM(r.amount) FROM v_receipt r",reason='test',metric_ids=['net_receipts'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'time_range':{'start':'2025-01-01','end':'2026-01-01'}},out)


def test_unused_cte_cannot_fake_team_filter():
    out=ModelDecision(action='sql',sql="WITH decoy AS (SELECT team_id FROM v_team WHERE team_name='劳动团队') SELECT COUNT(*) FROM v_case c WHERE c.status='OPEN'",reason='test',metric_ids=['open_cases'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'metric_ids':['open_cases'],'filters':{'team':'劳动团队'}},out)


def test_team_grouping_must_not_be_omitted():
    out=ModelDecision(action='sql',sql="SELECT COUNT(*) FROM v_case c WHERE c.status='OPEN'",reason='test',metric_ids=['open_cases'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'metric_ids':['open_cases'],'group_by':'team'},out)


def test_noncorrelated_subquery_cannot_fake_filter():
    out=ModelDecision(action='sql',sql="SELECT COUNT(*) FROM v_case c WHERE c.status='OPEN' AND EXISTS(SELECT 1 FROM v_team t WHERE t.team_name='劳动团队')",reason='test',metric_ids=['open_cases'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'metric_ids':['open_cases'],'filters':{'team':'劳动团队'}},out)


def test_scalar_count_cannot_escape_outer_team():
    out=ModelDecision(action='sql',sql="SELECT (SELECT COUNT(*) FROM v_case c WHERE c.status='OPEN') AS open_cases FROM v_team t WHERE t.team_name='劳动团队'",reason='test',metric_ids=['open_cases'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'metric_ids':['open_cases'],'filters':{'team':'劳动团队'}},out)


def test_left_join_on_does_not_filter_left_cases():
    out=ModelDecision(action='sql',sql="SELECT COUNT(*) FROM v_case c LEFT JOIN v_team t ON c.owner_team_id=t.team_id AND t.team_name='劳动团队' WHERE c.status='OPEN'",reason='test',metric_ids=['open_cases'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'metric_ids':['open_cases'],'filters':{'team':'劳动团队'}},out)


def test_year_group_cannot_pretend_to_be_month():
    out=ModelDecision(action='sql',sql="SELECT DATE_FORMAT(r.received_at,'%Y') AS dimension,SUM(r.amount) FROM v_receipt r GROUP BY DATE_FORMAT(r.received_at,'%Y')",reason='test',metric_ids=['net_receipts'])
    with pytest.raises(GuardError):
        enforce_conditions(out.sql,{'group_by':'month'},out)


def test_equivalent_midnight_range_is_accepted():
    out=ModelDecision(action='sql',sql="SELECT COUNT(*) FROM v_case c WHERE c.opened_at>='2025-01-01 00:00:00' AND c.opened_at<'2026-01-01 00:00:00'",reason='test',metric_ids=['new_cases'])
    enforce_conditions(out.sql,{'time_range':{'start':'2025-01-01','end':'2026-01-01'}},out)
