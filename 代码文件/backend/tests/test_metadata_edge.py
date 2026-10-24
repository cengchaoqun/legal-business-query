"""元数据发布与澄清条件的边界不能静默扩大或缩小统计范围。"""
import copy
from datetime import datetime, timezone

import pytest

from app.metadata import MetadataService


@pytest.fixture
def metadata(tmp_path):
    return MetadataService(tmp_path / 'metadata')


@pytest.mark.parametrize('value', [{}, None, [], 'invalid'])
def test_required_dictionary_missing_or_wrong_type_is_rejected(metadata, value):
    package = metadata.snapshot()
    package['dictionary'] = value
    with pytest.raises(ValueError, match='dictionary|字典'):
        metadata.validate(package)


@pytest.mark.parametrize('path,value', [('schema', []), ('metrics', {}), ('metrics', [None]), ('schema', {'version': 123})])
def test_malformed_package_is_a_validation_error(metadata, path, value):
    package = metadata.snapshot()
    package[path] = value
    with pytest.raises(ValueError):
        metadata.validate(package)


def test_duplicate_alias_across_standard_values_is_rejected_before_publish(metadata):
    package = metadata.snapshot()
    package['schema']['version'] = 'edge_v2'
    package['dictionary']['v_team.team_name']['民商团队'].append('综合组')
    package['dictionary']['v_team.team_name']['劳动团队'].append('综合组')
    original = metadata.snapshot()
    with pytest.raises(ValueError, match='别名|歧义'):
        metadata.publish(package, 'admin')
    assert metadata.snapshot() == original
    assert not (metadata.directory / 'active.json').exists()


@pytest.mark.parametrize('question', ['2025年海事团队净回款', '2025年王律师净回款', '2025年律师王净回款'])
def test_unknown_named_scope_requires_clarification(metadata, question):
    conditions = metadata.conditions(question, {})
    assert conditions['unresolved_values']
    assert conditions['filters'] == {}


@pytest.mark.parametrize('question,key', [('2025年民商团队和劳动团队净回款', 'team'), ('2025年律师甲和律师乙工时', 'lawyer')])
def test_multiple_named_values_are_not_silently_reduced(metadata, question, key):
    conditions = metadata.conditions(question, {})
    assert conditions['unresolved_values']
    assert key not in conditions['filters']


def test_longest_lawyer_name_does_not_become_two_people(metadata):
    conditions = metadata.conditions('2025年律师甲乙工时', {})
    assert conditions['filters']['lawyer'] == '律师甲乙'
    assert conditions['unresolved_values'] == []


def test_pending_new_metric_and_time_resets_old_scope_and_dimension(metadata):
    previous = {'metric_ids': ['net_receipts'], 'filters': {'lawyer': '律师甲', 'case_type': 'LABOR'}, 'group_by': 'month', 'sort_desc': True}
    conditions = metadata.conditions('2024年全所新收案件数', previous, pending=True)
    assert conditions['filters'] == {}
    assert 'group_by' not in conditions
    assert 'sort_desc' not in conditions
    assert conditions['metric_ids'] == ['new_cases']


def test_bare_clarification_year_keeps_metric_and_known_filter(metadata):
    previous = {'metric_ids': ['net_receipts'], 'filters': {'team': '劳动团队'}, 'group_by': 'month'}
    conditions = metadata.conditions('2025全年', previous, pending=True)
    assert conditions['metric_ids'] == ['net_receipts']
    assert conditions['filters'] == previous['filters']
    assert conditions['group_by'] == 'month'


def test_unresolved_name_is_preserved_until_answered(metadata):
    first = metadata.conditions('海事团队净回款', {})
    second = metadata.conditions('2025全年', first, pending=True)
    assert second['unresolved_values'] == first['unresolved_values']
    resolved = metadata.conditions('改为劳动团队', second, pending=True)
    assert resolved['unresolved_values'] == []
    assert resolved['filters']['team'] == '劳动团队'


def test_inclusive_year_range_has_complete_half_open_bounds(metadata):
    conditions = metadata.conditions('2024年至2025年净回款', {})
    assert conditions['time_range'] == {'start': '2024-01-01', 'end': '2026-01-01'}
    assert conditions['unsupported_time'] is False


@pytest.mark.parametrize('question', ['2025年13月净回款', '2025年第一季度净回款', '2025年1月至3月净回款', '2025年到2024年净回款', '2025年和2024年净回款'])
def test_unsupported_time_is_never_silently_reduced_to_first_year(metadata, question):
    conditions = metadata.conditions(question, {})
    assert conditions['unsupported_time'] is True
    assert 'time_range' not in conditions


def test_relative_year_is_computed_in_shanghai_timezone(metadata):
    conditions = metadata.conditions('去年净回款', {}, now=datetime(2025, 12, 31, 18, tzinfo=timezone.utc))
    assert conditions['time_range'] == {'start': '2025-01-01', 'end': '2026-01-01'}


def test_month_and_team_type_boundaries_are_preserved(metadata):
    conditions = metadata.conditions('2025年2月劳动团队净回款', {})
    assert conditions['time_range'] == {'start': '2025-02-01', 'end': '2025-03-01'}
    assert conditions['filters'] == {'team': '劳动团队'}
    assert conditions['unresolved_values'] == []
    assert conditions['unsupported_time'] is False


def test_general_lawyer_ranking_is_not_an_unknown_name(metadata):
    conditions = metadata.conditions('2025年各律师已审核工时排名', {})
    assert conditions['unresolved_values'] == []
    assert conditions['group_by'] == 'lawyer'


def test_hyphen_year_range_and_month_range_are_not_confused(metadata):
    assert metadata.conditions('2024-2025年净回款', {})['time_range'] == {'start': '2024-01-01', 'end': '2026-01-01'}
    unsupported = metadata.conditions('2024年至2025年2月净回款', {})
    assert unsupported['unsupported_time'] and 'time_range' not in unsupported


def test_negative_named_filter_requires_explicit_supported_scope(metadata):
    conditions = metadata.conditions('2025年净回款，不要劳动团队', {})
    assert conditions['unresolved_values']
    assert 'team' not in conditions['filters']


def test_quarter_dimension_followup_does_not_clear_confirmed_time(metadata):
    previous = metadata.conditions('2025年劳动团队净回款', {})
    conditions = metadata.conditions('净回款按季度', previous, pending=True)
    assert conditions['time_range'] == previous['time_range']
    assert conditions['filters'] == previous['filters']
    assert conditions['group_by'] == 'quarter'


@pytest.mark.parametrize('question', ['最近三个月净回款', '2025年6月以来净回款', '上周工时'])
def test_unsupported_relative_or_open_range_does_not_reuse_old_time(metadata, question):
    previous = metadata.conditions('2024年净回款', {})
    conditions = metadata.conditions(question, previous, pending=True)
    assert conditions['unsupported_time'] is True
    assert 'time_range' not in conditions


@pytest.mark.parametrize('suffix,metric', [('结案率', 'cohort_close_rate'), ('回款率', 'cohort_collection_rate')])
def test_explicit_cohort_as_of_keeps_intake_year_separate(metadata, suffix, metric):
    conditions = metadata.conditions(f'2025年新收案件截至2026年1月1日零点的{suffix}', {})
    assert conditions['metric_ids'] == [metric]
    assert conditions['time_range'] == {'start': '2025-01-01', 'end': '2026-01-01'}
    assert conditions['as_of'] == '2026-01-01'
    assert conditions['unsupported_time'] is False


@pytest.mark.parametrize('question,dimension', [
    ('按案件归属团队统计2025年的净回款', 'team'),
    ('2025年全团队新收多少案件？', 'team'),
    ('2025年每位律师已审核工时排名', 'lawyer'),
    ('2025年各位律师已审核工时排名', 'lawyer'),
    ('2025年各主办律师新收案件数', 'lawyer'),
    ('2025年各参与律师的参与案件数', 'lawyer'),
])
def test_generic_role_and_dimension_phrases_are_not_unknown_names(metadata, question, dimension):
    conditions = metadata.conditions(question, {})
    assert conditions['unresolved_values'] == []
    assert conditions['group_by'] == dimension


@pytest.mark.parametrize('question', ['2025年海事团队净回款', '2025年未知律师工时', '2025年归属海事团队净回款'])
def test_generic_phrase_handling_does_not_erase_real_unknown_values(metadata, question):
    assert metadata.conditions(question, {})['unresolved_values']
