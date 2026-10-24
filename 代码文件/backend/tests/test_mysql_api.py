"""显式开启的真实MySQL接口回归，使用独立演示实例及本地随机账号。"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.main import create_app
from app.schemas import ModelDecision
from app.analytics import AnalyticsError

pytestmark=pytest.mark.skipif(os.environ.get('LEGAL_MYSQL_TEST')!='1',reason='需要独立MySQL沙箱；设置LEGAL_MYSQL_TEST=1开启')


@pytest.fixture
def live():
    passwords=json.loads((ROOT/'.runtime/demo-credentials.txt').read_text(encoding='utf-8'))['application_users']
    app=create_app(Settings(model_mode='mock'))
    with TestClient(app) as client:
        response=client.post('/api/auth/login',json={'username':'analyst','password':passwords['analyst']})
        assert response.status_code==200
        yield client,app


def ask(client,question,cid=None,key=None):
    from uuid import uuid4
    cid=cid or client.post('/api/conversations').json()['conversation_id']
    result=client.post(f'/api/conversations/{cid}/queries',json={'question':question,'client_request_id':key or str(uuid4())})
    return cid,result


def test_real_database_counts_receipts_and_hours(live):
    client,_=live
    for question,value in [('2025年新收案件数',4),('2025年净回款','17000.00'),('2025年有效工时','27.00')]:
        _,r=ask(client,question)
        assert r.status_code==200,r.json()
        assert r.json()['status']=='succeeded',r.json()
        table=r.json()['result']['table']
        assert list(table['rows'][0].values())==[value]
        assert r.json()['result']['basis']['execution_checks']['explain']=='unavailable_view_permissions'


def test_clarify_then_followup_and_duplicate(live):
    from uuid import uuid4
    client,_=live
    cid,r=ask(client,'净回款多少')
    assert r.json()['status']=='clarify'
    _,r=ask(client,'2025全年',cid)
    assert r.json()['status']=='succeeded',r.json()
    key='mysql-api-'+str(uuid4())
    _,r=ask(client,'只看劳动团队，按月展示',cid,key=key)
    assert r.json()['status']=='succeeded',r.json()
    assert r.json()['result']['table']['rows']==[{'dimension':'2025-06','net_receipts':'5000.00'}]
    _,again=ask(client,'只看劳动团队，按月展示',cid,key=key)
    assert again.json()['query_id']==r.json()['query_id']


def test_two_fact_preaggregation(live):
    client,_=live
    _,r=ask(client,'各案件2025年净回款与已审核工时')
    assert r.json()['status']=='succeeded',r.json()
    assert r.json()['result']['table']['rows'][0]=={'case_id':1,'net_receipts':'9000.00','hours':'15.00'}


def test_zero_denominator_preserved(live):
    client,_=live
    _,r=ask(client,'2030年期间收结比')
    assert r.json()['status']=='succeeded',r.json()
    assert r.json()['result']['table']['rows']==[{'ratio':None}]
    assert '暂无可计算比例' in r.json()['result']['summary']


def test_malicious_model_cannot_read_system_table(live):
    client,app=live
    class BadModel:
        async def generate(self,context,history):
            return ModelDecision(action='sql',sql='SELECT User FROM mysql.user',reason='test')
    app.state.query.model=BadModel()
    _,r=ask(client,'查看当前数据')
    assert r.json()['status']=='refuse'
    assert r.json()['error_code']=='SQL_OBJECT_DENIED'


def test_timeout_error_saved_and_releases_conversation(live):
    client,app=live
    def timeout(*args,**kwargs):
        raise AnalyticsError('QUERY_TIMEOUT','本次查询超时，请缩小范围')
    app.state.analytics.execute=timeout
    cid,r=ask(client,'2025年净回款')
    assert r.status_code==504
    qid=r.json()['query_id']
    assert client.get(f'/api/queries/{qid}').json()['error_code']=='QUERY_TIMEOUT'
    assert client.get(f'/api/conversations/{cid}/queries').json()['conversation']['active_query_id'] is None


def test_real_http_model_protocol_swap_with_same_mysql_chain(live):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from app.model_client import ModelClient, mock_decision
    client,app=live
    names=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            names.append(payload['model'])
            context=json.loads(payload['messages'][-1]['content'])
            decision=mock_decision(context)
            body=json.dumps({'choices':[{'message':{'content':decision.model_dump_json()}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        for name in ['protocol-fixture-base','protocol-fixture-finetuned']:
            settings=Settings(model_mode='http',model_base_url=f'http://127.0.0.1:{server.server_port}/v1',model_name=name)
            app.state.query.model=ModelClient(settings)
            app.state.query.settings=settings
            _,r=ask(client,'2025年净回款')
            assert r.json()['status']=='succeeded',r.json()
            assert r.json()['result']['table']['rows']==[{'net_receipts':'17000.00'}]
        assert names==['protocol-fixture-base','protocol-fixture-finetuned']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_current_open_grouped_by_team(live):
    client,_=live
    _,r=ask(client,'当前在办案件按各团队统计')
    assert r.json()['status']=='succeeded',r.json()
    assert r.json()['result']['table']['rows']==[{'dimension':'劳动团队','open_cases':1},{'dimension':'民商团队','open_cases':1}]


def test_cohort_rates_use_opening_year_and_cutoff(live):
    from decimal import Decimal
    client,_=live
    for metric,expected in [('结案率',Decimal('0.5')),('回款率',Decimal('0.4'))]:
        _,r=ask(client,f'2025年新收案件截至2026年1月1日零点的{metric}')
        assert r.json()['status']=='succeeded',r.json()
        actual=next(iter(r.json()['result']['table']['rows'][0].values()))
        assert Decimal(actual)==expected


def test_mysql_execution_budget_stops_expensive_read():
    """单独验证执行层超时防线；该笛卡尔积不会由生产SQLGuard放行。"""
    import time
    from app.analytics import Analytics
    from app.sql_guard import ValidatedQuery
    settings=Settings()
    executor=Analytics(settings.analytics_database_url,timeout_seconds=1)
    sql='SELECT SUM(a0.case_id) AS value FROM '+' CROSS JOIN '.join(f'v_case a{i}' for i in range(12))
    started=time.monotonic()
    try:
        with pytest.raises(AnalyticsError) as error:
            executor.execute(ValidatedQuery(sql,'demo'),timeout_seconds=1)
        assert error.value.code=='QUERY_TIMEOUT'
        assert time.monotonic()-started<6
        assert executor.get_watermark()
    finally:
        executor.engine.dispose()
