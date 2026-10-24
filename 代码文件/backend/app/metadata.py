"""小规模全量元数据上下文和原子配置发布。"""
import copy
import datetime as dt
import json
import os
import re
import threading
from pathlib import Path

from .config import ROOT

SOURCE = ROOT / '交付方案' / '微调数据'
SHANGHAI = dt.timezone(dt.timedelta(hours=8), name='Asia/Shanghai')
REQUIRED_DICTIONARIES = {'v_case.case_type', 'v_case.status', 'v_case.outcome', 'v_receipt.receipt_type',
                         'v_receipt.status', 'v_worklog.status', 'v_team.team_name', 'v_lawyer.lawyer_name'}


class MetadataService:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.canonical = json.loads((SOURCE / 'schema.json').read_text(encoding='utf-8'))
        self.base_metric_ids = {x['id'] for x in json.loads((SOURCE / 'metrics.json').read_text(encoding='utf-8'))}
        active = self.directory / 'active.json'
        self.package = json.loads(active.read_text(encoding='utf-8')) if active.exists() else {
            'schema': self.canonical,
            'dictionary': json.loads((SOURCE / 'value_dictionary.json').read_text(encoding='utf-8')),
            'metrics': json.loads((SOURCE / 'metrics.json').read_text(encoding='utf-8')),
        }
        self.validate(self.package)

    @property
    def version(self):
        return self.package['schema']['version']

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.package)

    def validate(self, package):
        if not isinstance(package, dict) or not all(k in package for k in ('schema', 'dictionary', 'metrics')):
            raise ValueError('配置包需要schema、dictionary、metrics')
        schema = package['schema']
        if not isinstance(schema, dict):
            raise ValueError('schema必须是对象')
        if not isinstance(schema.get('version'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', schema['version']):
            raise ValueError('schema.version必须为安全的版本标识')
        if schema.get('synthetic') is not True:
            raise ValueError('当前演示连接只能发布标记synthetic=true的配置；生产接入需要同步切换数据配置')
        tables = schema.get('tables', {})
        if not isinstance(tables, dict) or set(tables) != set(self.canonical['tables']):
            raise ValueError('schema.tables必须匹配批准的六类分析视图')
        for table, spec in tables.items():
            if not isinstance(spec, dict) or not isinstance(spec.get('columns'), dict) or set(spec['columns']) != set(self.canonical['tables'][table]['columns']):
                raise ValueError(f'schema.tables.{table}.columns与批准字段不一致')
            if not isinstance(spec.get('grain'), str) or not spec['grain'].strip() or not all(isinstance(x, str) and x.strip() for x in spec['columns'].values()):
                raise ValueError(f'{table}需要粒度及字段说明')
        if schema.get('relationships') != self.canonical['relationships']:
            raise ValueError('关联关系需要与批准的物理关系一致')
        if not isinstance(package['metrics'], list) or not all(isinstance(x, dict) and isinstance(x.get('id'), str) for x in package['metrics']):
            raise ValueError('metrics必须是指标对象数组')
        ids = [x['id'] for x in package['metrics']]
        if len(ids) != len(set(ids)) or set(ids) != self.base_metric_ids:
            raise ValueError('metrics需要完整且唯一的已批准指标')
        if any(not isinstance(x.get(key), str) or not x[key].strip() for x in package['metrics'] for key in ('rule', 'name')):
            raise ValueError('指标缺少名称或口径')
        dictionary = package['dictionary']
        if not isinstance(dictionary, dict) or not REQUIRED_DICTIONARIES.issubset(dictionary):
            raise ValueError('dictionary必须完整包含批准字段的必需字典')
        for key, mapping in dictionary.items():
            if key == 'ambiguities':
                if not isinstance(mapping, dict) or any(not isinstance(k, str) or not k.strip() or not isinstance(v, list)
                    or not v or not all(isinstance(s, str) and s.strip() for s in v) for k, v in mapping.items()):
                    raise ValueError('dictionary.ambiguities必须是非空候选字符串数组的映射')
                continue
            if not isinstance(key, str) or '.' not in key:
                raise ValueError(f'字典字段路径无效：{key}')
            table, column = key.split('.', 1)
            if table not in tables or column not in tables[table]['columns'] or not isinstance(mapping, dict) or not mapping:
                raise ValueError(f'字典引用未知字段：{key}')
            if any(not isinstance(k, str) or not k.strip() or not isinstance(v, list) or not all(isinstance(s, str) and s.strip() for s in v) for k, v in mapping.items()):
                raise ValueError(f'字典别名必须为字符串数组：{key}')
            owners = {}
            for value, aliases in mapping.items():
                for alias in [value] + aliases:
                    normalized = alias.strip().casefold()
                    if normalized in owners and owners[normalized] != value:
                        raise ValueError(f'字典别名跨标准值存在歧义：{key}.{alias}')
                    owners[normalized] = value

    def publish(self, package, actor):
        self.validate(package)
        if len(json.dumps(package).encode()) > 2_000_000:
            raise ValueError('配置包超过2MB')
        with self.lock:
            if package['schema']['version'] == self.version:
                raise ValueError('请使用不同的元数据版本号')
            archived = self.directory / f'{self.version}.json'
            archived.write_text(json.dumps(self.package, ensure_ascii=False, indent=2), encoding='utf-8')
            updated = copy.deepcopy(package)
            updated['publication'] = {'actor': actor, 'at': dt.datetime.now(dt.timezone.utc).isoformat()}
            pending = self.directory / 'active.tmp'
            pending.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(pending, self.directory / 'active.json')
            self.package = updated
        return self.version

    def conditions(self, question, previous, pending=False, now=None):
        """只解析唯一确定的条件；真实模型仍得到完整问题和历史。"""
        now = now or dt.datetime.now(SHANGHAI)
        now = now.replace(tzinfo=SHANGHAI) if now.tzinfo is None else now.astimezone(SHANGHAI)
        pairs = [('净回款', 'net_receipts'), ('回款', 'net_receipts'), ('有效工时', 'approved_hours'), ('工时', 'approved_hours'), ('期间收结比', 'period_close_open_ratio'), ('签约服务费', 'contract_fee'), ('参与', 'participating_cases'), ('新收', 'new_cases'), ('新接', 'new_cases'), ('在办', 'open_cases'), ('平均办案', 'avg_close_days')]
        found = next((mid for text, mid in pairs if text in question), None)
        if found is None and '结案' in question and '结案率' not in question:
            found = 'closed_cases'
        explicit_time = bool(re.search(r'20\d{2}|去年|今年|上月|本月|(?:上|本|第[一二三四1-4])季度|[上下]半年|过去\d+天', question))
        explicit_followup = bool(re.search(r'只看|那.+呢|改为|换成|还是', question))
        # 待澄清并不表示用户下一句话永远属于旧问题；完整的新指标与时间重新建立上下文。
        new_question = bool(found and explicit_time and not explicit_followup)
        followup = not new_question and (pending or bool(re.search(r'只看|那.+呢|按月|按季度|改为|换成|还是|期间收结比|^\d{4}全年[。！]?$|^202\d年[。！]?$', question)))
        result = copy.deepcopy(previous) if followup else {}
        result['unresolved_values'] = list(result.get('unresolved_values', []))
        result['unsupported_time'] = bool(result.get('unsupported_time', False))
        years = re.findall(r'(20\d{2})年?', question)
        year_range = re.search(r'(20\d{2})年?\s*(?:至|到|[-~～—])\s*(20\d{2})年?', question)
        unsupported = bool(re.search(r'第?[一二三四1234]季度|上季度|本季度|[上下]半年|过去\d+天|20\d{2}[-/]\d{1,2}(?!\d)|\d{1,2}月\d{1,2}[日号]|Q[1-4]|最近|近[一二三四五六七八九十\d]+个?[年月周天日]|[上本]周|昨天|今天|今日|昨日|以来|以前|以后', question, re.I))
        month_matches = re.findall(r'(\d{1,2})月', question)
        if len(month_matches) > 1 or year_range and month_matches:
            unsupported = True
        if year_range and not unsupported:
            first, last = map(int, year_range.groups())
            if first <= last:
                result['time_range'] = {'start': f'{first}-01-01', 'end': f'{last+1}-01-01'}
                result['unsupported_time'] = False
            else:
                unsupported = True
        elif len(years) > 1:
            unsupported = True
        elif years and not unsupported:
            year = int(years[0])
            result['time_range'] = {'start': f'{year}-01-01', 'end': f'{year+1}-01-01'}
            result['unsupported_time'] = False
            month_match = re.search(r'20\d{2}年(\d{1,2})月', question)
            if month_match:
                month = int(month_match[1])
                if 1 <= month <= 12:
                    start_date = dt.date(year, month, 1)
                    end_date = dt.date(year+1,1,1) if month == 12 else dt.date(year,month+1,1)
                    result['time_range'] = {'start':start_date.isoformat(),'end':end_date.isoformat()}
                else:
                    unsupported = True
        elif '去年' in question or '今年' in question:
            year = now.year - 1 if '去年' in question else now.year
            result['time_range'] = {'start': f'{year}-01-01', 'end': f'{year+1}-01-01'}
            result['unsupported_time'] = False
        elif '上月' in question or '本月' in question:
            end = now.replace(day=1)
            start = (end-dt.timedelta(days=1)).replace(day=1) if '上月' in question else end
            end = end if '上月' in question else now+dt.timedelta(days=1)
            result['time_range'] = {'start': start.date().isoformat(), 'end': end.date().isoformat()}
            result['unsupported_time'] = False
        elif month_matches:
            unsupported = True
        if unsupported:
            result.pop('time_range', None)
            result.pop('as_of', None)
            result['unsupported_time'] = True
        # 仅支持明确的“受理年 + 截至某日零点”格式，受理期间与截止时点分别保存。
        cohort_as_of = re.search(r'(20\d{2})年新收案件截至(20\d{2})年(\d{1,2})月(\d{1,2})日零点的?(结案率|回款率)', question)
        if cohort_as_of:
            intake_year, cutoff_year, cutoff_month, cutoff_day = map(int, cohort_as_of.groups()[:4])
            try:
                cutoff = dt.date(cutoff_year, cutoff_month, cutoff_day)
                if cutoff < dt.date(intake_year, 1, 1):
                    raise ValueError('截至时点早于受理期间')
            except ValueError:
                result.pop('time_range', None)
                result.pop('as_of', None)
                result['unsupported_time'] = True
            else:
                result['time_range'] = {'start': f'{intake_year}-01-01', 'end': f'{intake_year+1}-01-01'}
                result['as_of'] = cutoff.isoformat()
                result['unsupported_time'] = False
        if '结案率' in question and '期间收结比' not in question:
            result.pop('metric_ids', None)
            result['ambiguous_metric'] = True
        elif '结案' in question and not found:
            found = 'closed_cases'
        if '回款' in question and '工时' in question:
            result['metric_ids'] = ['net_receipts', 'approved_hours']
        elif found:
            result['metric_ids'] = [found]
            result.pop('ambiguous_metric', None)
        if '新收' in question and '结案率' in question and '截至' in question:
            result['metric_ids'] = ['cohort_close_rate']
            result.pop('ambiguous_metric', None)
        if '新收' in question and '回款率' in question and '截至' in question:
            result['metric_ids'] = ['cohort_collection_rate']
            result.pop('ambiguous_metric', None)
        if '按月' in question:
            result['group_by'] = 'month'
        elif '按季度' in question:
            result['group_by'] = 'quarter'
        elif '团队' in question and any(s in question for s in ('按', '各', '每', '全团队')):
            result['group_by'] = 'team'
        elif '律师' in question and any(s in question for s in ('各', '每', '排名', '排行')):
            result['group_by'] = 'lawyer'
        filters = result.setdefault('filters', {})
        dictionary = self.snapshot()['dictionary']
        remaining_question = question
        labels = {'team': '团队', 'case_type': '案件类型', 'lawyer': '律师'}
        def resolve_flag(target):
            prefix = labels[target] + '：'
            result['unresolved_values'] = [x for x in result['unresolved_values'] if not x.startswith(prefix)]
        def flag(target, description):
            value = labels[target] + '：' + description
            if value not in result['unresolved_values']:
                result['unresolved_values'].append(value)
        def matched_values(source, mapping):
            # 先占用最长姓名/别名的区间，避免“律师甲乙”同时被当作“律师甲”。
            candidates = sorted(((token, value) for value, aliases in mapping.items() for token in [value] + aliases),
                                key=lambda item: len(item[0]), reverse=True)
            occupied, values = set(), set()
            for token, value in candidates:
                for match in re.finditer(re.escape(token), source):
                    positions = set(range(match.start(), match.end()))
                    if positions.isdisjoint(occupied):
                        occupied.update(positions)
                        values.add(value)
            residual = ''.join(' ' if i in occupied else char for i, char in enumerate(source))
            return values, residual
        for field, target in [('v_team.team_name', 'team'), ('v_case.case_type', 'case_type'), ('v_lawyer.lawyer_name', 'lawyer')]:
            values, residual = matched_values(remaining_question, dictionary[field])
            if values:
                resolve_flag(target)
                if re.search(r'不要|排除|不含|不看|除了|除去', question):
                    filters.pop(target, None)
                    flag(target, '排除条件尚未确认，请明确需要包含的范围')
                elif len(values) == 1:
                    filters[target] = next(iter(values))
                else:
                    filters.pop(target, None)
                    flag(target, '、'.join(sorted(values)) + '（请确认组合范围）')
            remaining_question = residual
        # 去掉已匹配名称后再找未识别的命名范围；一般的“按团队/各律师”不是具体名称。
        fillers = ['净回款', '有效工时', '签约服务费', '新收案件', '新收', '新接', '在办案件', '在办', '案件数',
                   '案件', '回款', '工时', '统计', '查询', '查看', '只看', '只查', '改为', '换成', '对比', '比较',
                   '排名', '排行', '最高', '多少', '全年', '全所', '本所', '全部', '所有', '今年', '去年', '上月', '本月',
                   '已审核', '审核通过', '有效', '工作量', '参与', '主办', '负责',
                   '按月', '按季度', '分别', '各个', '每个', '期间', '按', '分', '各', '每', '年', '月', '那', '呢', '的']
        cleaned = remaining_question
        for filler in sorted(fillers, key=len, reverse=True):
            cleaned = cleaned.replace(filler, ' ')
        team_candidates = re.findall(r'[\u4e00-\u9fffA-Za-z]{1,16}(?:团队|组)', cleaned)
        lawyer_candidates = re.findall(r'律师[\u4e00-\u9fffA-Za-z]{1,12}|[\u4e00-\u9fffA-Za-z]{1,12}律师', cleaned)
        generic_names = {'team': {'团队', '归属团队', '全团队', '分组'}, 'lawyer': {'律师', '位律师'}}
        for target, candidates in [('team', team_candidates), ('lawyer', lawyer_candidates)]:
            # 仅忽略完整的泛指词，不按后缀裁剪未知名称；“海事团队”“未知律师”仍保留待确认。
            names = [name for name in candidates if name not in generic_names[target]]
            if names:
                filters.pop(target, None)
                resolve_flag(target)
                flag(target, '、'.join(sorted(set(names))) + '（未匹配，请核实）')
        if '全部团队' in question or '全所' in question:
            filters.pop('team', None)
            resolve_flag('team')
        if '全部律师' in question or '所有律师' in question:
            filters.pop('lawyer', None)
            resolve_flag('lawyer')
        if '甲' in question and not any(v in question for v in ('律师甲', '甲律师', '甲乙律师')):
            result['ambiguous_lawyer'] = True
        else:
            result.pop('ambiguous_lawyer', None)
        if '排名' in question or '排行' in question or '最高' in question:
            result['sort_desc'] = True
        return result
