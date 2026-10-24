"""一条同步HTTP请求内完成编排，幂等与终态由数据库事务保证。"""
import asyncio
import datetime as dt
import time

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope, Scope

from .analytics import AnalyticsError
from .model_client import ModelError
from .result_service import make_result
from .schemas import ModelDecision
from .sql_guard import SQLGuard, GuardError


def enforce_conditions(sql, conditions, decision):
    """只接受能证实保留已确认条件的常规表达；复杂等价写法保守退回。"""
    tree = sqlglot.parse_one(sql, read='mysql')
    def same_boundary(actual,expected):
        return actual==expected or (len(expected)==10 and actual in (expected+' 00:00:00',expected+'T00:00:00'))
    scopes=traverse_scope(tree)
    if not scopes or isinstance(tree,exp.Union):
        raise GuardError('SQL_CONDITION_UNPROVEN','当前查询结构无法证明保留已确认条件，请改为单一结果查询',True)
    root=scopes[-1]
    if (conditions.get('filters') or conditions.get('group_by')) and root.subquery_scopes:
        raise GuardError('SQL_CONDITION_UNPROVEN','带维度条件的查询请使用显式关联或按案件预聚合，不能用独立标量子查询绕过筛选',True)
    active=[]
    def visit(scope):
        if scope in active:
            return
        active.append(scope)
        for _,source in scope.selected_sources.values():
            if isinstance(source,Scope):
                visit(source)
        for sub in scope.subquery_scopes:
            visit(sub)
    visit(root)
    if any(not any(scope.expression is cte.this for scope in active) for cte in tree.find_all(exp.CTE)):
        raise GuardError('SQL_CONDITION_UNPROVEN','查询包含不参与结果的CTE，不能据此证明筛选条件',True)
    predicates=[]
    def flatten(node):
        if isinstance(node,exp.And):
            return flatten(node.left)+flatten(node.right)
        if isinstance(node,exp.Paren):
            return flatten(node.this)
        return [node]
    scope_predicates={}
    where_predicates={}
    for scope in active:
        current=[]
        where_current=[]
        parts=([(scope.expression.args['where'].this,None,True)] if scope.expression.args.get('where') else [])
        for join in scope.expression.args.get('joins',[]):
            if join.args.get('on') and join.side.upper()!='RIGHT':
                parts.append((join.args['on'],join.this.alias_or_name if join.side.upper()=='LEFT' else None,False))
        for part,only_alias,is_where in parts:
            for pred in flatten(part):
                if isinstance(pred,(exp.EQ,exp.GTE,exp.LT)) and isinstance(pred.left,exp.Column) and isinstance(pred.right,exp.Literal):
                    if only_alias and pred.left.table!=only_alias:
                        continue
                    source=scope.sources.get(pred.left.table)
                    if not source and not pred.left.table and len(scope.sources)==1:
                        source=next(iter(scope.sources.values()))
                    if isinstance(source,exp.Table):
                        item=(source.name,pred.left.name,type(pred),pred.right.this)
                        predicates.append(item)
                        current.append(item)
                        if is_where:
                            where_current.append(item)
        scope_predicates[id(scope)]=current
        where_predicates[id(scope)]=where_current
    names={'team':('v_team','team_name'),'lawyer':('v_lawyer','lawyer_name'),'case_type':('v_case','case_type')}
    for name, value in conditions.get('filters', {}).items():
        if name in names and not any((table,col)==names[name] and op is exp.EQ and val==value for table,col,op,val in where_predicates[id(root)]):
            raise GuardError('SQL_CONDITION_MISSING', '生成的SQL遗漏已确认筛选条件', True)
    if conditions.get('metric_ids') and not set(conditions['metric_ids']).issubset(decision.metric_ids):
        raise GuardError('SQL_METRIC_MISMATCH','生成的SQL改变了已确认的统计指标',True)
    if conditions.get('as_of') and any(m in decision.metric_ids for m in ('cohort_close_rate','cohort_collection_rate')):
        cutoff=conditions['as_of']
        if 'cohort_close_rate' in decision.metric_ids:
            valid=any(isinstance(n,exp.LT) and isinstance(n.left,exp.Column) and n.left.name=='closed_at' and isinstance(n.right,exp.Literal) and n.right.this==cutoff for agg in tree.find_all(exp.Sum) for n in agg.walk())
        else:
            valid=any(t=='v_receipt' and field=='received_at' and op is exp.LT and val==cutoff for t,field,op,val in predicates)
        if not valid:
            raise GuardError('SQL_CUTOFF_MISSING','生成的SQL遗漏已确认的累计截止日期',True)
    bounds = conditions.get('time_range')
    if bounds and decision.metric_ids != ['open_cases']:
        mapping={'new_cases':('v_case','opened_at'),'closed_cases':('v_case','closed_at'),'net_receipts':('v_receipt','received_at'),'approved_hours':('v_worklog','worked_at'),'avg_close_days':('v_case','closed_at'),'contract_fee':('v_case','opened_at'),'participating_cases':('v_case','opened_at'),'cohort_close_rate':('v_case','opened_at'),'cohort_collection_rate':('v_case','opened_at')}
        needed=[mapping[mid] for mid in decision.metric_ids if mid in mapping]
        if 'period_close_open_ratio' in decision.metric_ids:
            needed += [('v_case','opened_at'),('v_case','closed_at')]
        for table in {table for table,col in needed}:
            fields={col for current,col in needed if current==table}
            relevant=[scope for scope in active if any(isinstance(source,exp.Table) and source.name==table for _,source in scope.selected_sources.values())]
            if not relevant:
                raise GuardError('SQL_CONDITION_MISSING','查询未使用指标要求的数据表',True)
            def bounded(scope,column):
                return all(any(t==table and field==column and op is operation and same_boundary(val,bounds[key]) for t,field,op,val in scope_predicates[id(scope)]) for key,operation in [('start',exp.GTE),('end',exp.LT)])
            for scope in relevant:
                if not any(bounded(scope,col) for col in fields):
                    raise GuardError('SQL_CONDITION_MISSING','生成的SQL未使用已确认的左闭右开时间范围',True)
            if any(not any(bounded(scope,col) for scope in relevant) for col in fields):
                raise GuardError('SQL_CONDITION_MISSING','生成的SQL遗漏指标要求的时间字段',True)
    group=conditions.get('group_by')
    if group:
        node=root.expression.args.get('group')
        if not node:
            raise GuardError('SQL_GROUP_MISSING','生成的SQL遗漏已确认分组粒度',True)
        group_columns=[]
        for col in node.find_all(exp.Column):
            source=root.sources.get(col.table)
            if isinstance(source,exp.Table):
                group_columns.append((source.name,col.name))
        if group=='team' and ('v_team','team_name') not in group_columns:
            raise GuardError('SQL_GROUP_MISSING','应按案件归属团队分组',True)
        if group=='lawyer' and not any(t=='v_lawyer' and col in ('lawyer_id','lawyer_name') for t,col in group_columns):
            raise GuardError('SQL_GROUP_MISSING','应按律师分组',True)
        if group=='month' and not any(isinstance(part,exp.TimeToStr) and isinstance(part.args.get('format'),exp.Literal) and part.args['format'].this=='%Y-%m' for part in node.walk()):
            raise GuardError('SQL_GROUP_MISSING','应按月份分组',True)
        if group=='quarter' and not ('QUARTER' in node.sql(dialect='mysql').upper() and 'YEAR' in node.sql(dialect='mysql').upper()):
            raise GuardError('SQL_GROUP_MISSING','应按季度分组',True)


class QueryService:
    def __init__(self, settings, storage, metadata, model, analytics):
        self.settings, self.storage, self.metadata, self.model, self.analytics = settings, storage, metadata, model, analytics

    async def run(self, cid, user, request):
        conv = self.storage.get_conversation(cid, user['id'])
        if conv is None:
            raise LookupError('会话不存在')
        # 配置或条件解析失败应发生在抢占会话之前，避免残留processing锁。
        conditions = self.metadata.conditions(request.question,conv['confirmed_conditions'],bool(conv['pending_clarification']))
        package = self.metadata.snapshot()
        row, created = self.storage.begin_query(cid,user['id'],request.client_request_id,request.question, self.settings.request_timeout_seconds)
        if not created:
            return row
        started = time.monotonic()
        qid = row['query_id']
        decision = None
        def finish(status, result=None, error=None):
            return self.storage.finish_query(qid,status,decision=decision.model_dump() if decision else None,result=result,conditions=conditions,
                error_code=getattr(error,'code',None),error_message=str(error) if error else None,
                metadata_version=package['schema']['version'],model_service_version=self.settings.model_name+(' (mock)' if self.settings.model_mode=='mock' else ''),elapsed_ms=round((time.monotonic()-started)*1000))
        try:
            if conditions.get('unresolved_values'):
                decision=ModelDecision(action='clarify',question='当前字典无法唯一匹配：'+ '、'.join(conditions['unresolved_values'])+'。请提供标准名称或明确一个筛选值。',reason='字段值需要核实，不能改查全所数据')
                return finish('clarify')
            if conditions.get('unsupported_time'):
                decision=ModelDecision(action='clarify',question='请明确统计期间，例如2025全年、2025年2月或2024年至2025年。涉及截至日比例时还需明确截至日期。',reason='当前表达无法唯一解析时间范围')
                return finish('clarify')
            if conditions.get('ambiguous_metric'):
                decision=ModelDecision(action='clarify',question='你要看期间收结比，还是新收案件截至指定日期的结案率？',reason='需要确认指标分母及截止点')
                return finish('clarify')
            if '回款率' in request.question and conditions.get('metric_ids')!=['cohort_collection_rate']:
                decision=ModelDecision(action='clarify',question='是否指某年新收案件的累计净回款/签约服务费？请明确受理年份和截至日期。',reason='回款率的分母与截止点需要确认')
                return finish('clarify')
            self.storage.update_stage(qid,'retrieving')
            context = {'question':request.question,'conditions':conditions,'retrieved_context':{**package,'dialect':'mysql8','now':dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),'timezone':'Asia/Shanghai','scope':'本机构全所授权视图'}}
            history = [q for q in self.storage.list_queries(cid) if q['query_id'] != qid and q['status'] != 'processing']
            guard = SQLGuard(package['schema'],package['metrics'])
            validated = None
            for attempt in range(2):
                self.storage.update_stage(qid,'generating')
                try:
                    remaining = self.settings.request_timeout_seconds-(time.monotonic()-started)
                    if remaining <= 0:
                        raise ModelError('QUERY_TIMEOUT','请求超时，请稍后重试')
                    decision = await asyncio.wait_for(self.model.generate(context,history),timeout=remaining)
                    if decision.action != 'sql':
                        return finish(decision.action)
                    self.storage.update_stage(qid,'validating')
                    validated = guard.validate(decision.sql,decision.metric_ids,user['scope_id'])
                    enforce_conditions(validated.sql,conditions,decision)
                    decision.sql = validated.sql
                    break
                except (GuardError,ModelError) as error:
                    if attempt == 0 and (getattr(error,'retryable',False) or error.code=='MODEL_FORMAT_INVALID'):
                        context['repair_instruction']={'error_code':error.code,'message':str(error),
                            'previous_sql':decision.sql if decision else None,
                            'requirement':'只按已批准schema和系统约定的SQL子集修正一次，不得扩大查询范围'}
                        continue
                    raise
            self.storage.update_stage(qid,'executing')
            remaining = self.settings.request_timeout_seconds-(time.monotonic()-started)
            if remaining < self.settings.query_timeout_seconds+0.2:
                raise AnalyticsError('QUERY_TIMEOUT','本次请求剩余时间不足以安全执行，请重试或简化问题')
            raw = await asyncio.to_thread(self.analytics.execute,validated)
            self.storage.update_stage(qid,'formatting')
            result=make_result(raw,decision,conditions,package,self.settings.model_name,self.settings.model_mode)
            return finish('succeeded',result)
        except GuardError as error:
            if not error.retryable:
                decision=ModelDecision(action='refuse',reason=str(error))
                return finish('refuse',error=error)
            return finish('failed',error=error)
        except (ModelError,AnalyticsError) as error:
            return finish('failed',error=error)
        except (TimeoutError,asyncio.CancelledError):
            error=ModelError('QUERY_TIMEOUT','本次请求已超时或取消，请查看历史后重试')
            finish('failed',error=error)
            if isinstance(asyncio.current_task(), asyncio.Task) and asyncio.current_task().cancelling():
                raise
            return self.storage.get_query(qid)
        except Exception:
            return finish('failed',error=ModelError('QUERY_FAILED','本次查询未完成，请管理员根据请求编号查看记录'))
