from decimal import Decimal, InvalidOperation


def numeric(value):
    if value is None or isinstance(value, bool):
        return False
    try:
        Decimal(str(value))
        return True
    except InvalidOperation:
        return False


def make_result(raw, decision, conditions, package, model_name, model_mode):
    columns, rows = raw['columns'], raw['rows']
    metrics = [m for m in package['metrics'] if m['id'] in decision.metric_ids]
    summary = '该条件下无记录'
    chart = None
    if rows:
        summary = f'查询返回{len(rows)}条记录'
        if len(rows) == 1 and len(columns) == 1:
            value = rows[0][columns[0]]
            unit = '元' if any(x in decision.metric_ids for x in ('net_receipts','contract_fee')) else '小时' if 'approved_hours' in decision.metric_ids else '天' if 'avg_close_days' in decision.metric_ids else '件'
            if any(x in decision.metric_ids for x in ('period_close_open_ratio','cohort_close_rate','cohort_collection_rate')):
                summary = '暂无可计算比例（分母为0或无记录）' if value is None else f'{Decimal(str(value))*100:.2f}%'
            else:
                summary = '暂无可计算值' if value is None else f'{value} {unit}'
        if len(columns) == 2 and all(numeric(r[columns[1]]) for r in rows) and not any(r[columns[0]] is None for r in rows):
            chart = {'kind':'line' if conditions.get('group_by') in ('month','quarter') else 'bar','x':[str(r[columns[0]]) for r in rows], 'series':[{'name':metrics[0]['name'] if metrics else columns[1], 'data':[str(r[columns[1]]) for r in rows]}]}
    warnings = []
    if raw['truncated']:
        warnings.append('结果超过行数或响应大小限制，当前结果已截断，请缩小查询范围。')
    if model_mode == 'mock':
        warnings.append('规则模拟模型：SQL由规则生成，统计数值来自数据库；不是微调模型效果。')
    if package['schema'].get('synthetic'):
        warnings.append('当前连接使用模拟业务数据，不代表真实律所经营数据。')
    return {'summary':summary,'table':{'columns':columns,'rows':rows},'chart':chart,'sql':decision.sql,'warnings':warnings,
            'data_watermark':raw['data_watermark'],
            'basis':{'metrics':metrics,'conditions':conditions,'timezone':'Asia/Shanghai','data_watermark':raw['data_watermark'],'scope':'本机构全所授权视图','metadata_version':package['schema']['version'],'model_service_version':model_name,'execution_checks':raw.get('execution_checks',{})}}
