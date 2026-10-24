import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(app_database_url='sqlite://', analytics_database_url='sqlite://',model_mode='mock', metadata_dir=tmp_path/'meta')
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.storage.seed_user('主管', 'test-password-1', 'analyst')
        app.state.storage.seed_user('其他主管', 'test-password-2', 'analyst')
        app.state.storage.seed_user('管理员', 'test-password-3', 'admin')
        yield c


def login(c, name='主管', password='test-password-1'):
    assert c.post('/api/auth/login',json={'username':name,'password':password}).status_code == 200


def test_permissions_and_ownership(client):
    assert client.get('/api/me').status_code == 401
    login(client)
    cid = client.post('/api/conversations').json()['conversation_id']
    assert client.get('/api/admin/query-runs').status_code == 403
    login(client, '其他主管','test-password-2')
    assert client.get(f'/api/conversations/{cid}/queries').status_code == 404
    assert client.post(f'/api/conversations/{cid}/queries',json={'client_request_id':'request-001','question':'当前在办数'}).status_code == 404


def test_retry_receives_rejected_sql_without_bypassing_guard(client):
    from app.schemas import ModelDecision
    calls = []
    bad_sql = "SELECT SUM(r.amount) FROM v_receipt r WHERE r.status='CONFIRMED' AND r.received_at>='2025-01-01' AND r.received_at<'2026-01-01'"
    class Model:
        async def generate(self, context, history):
            calls.append(context.copy())
            if len(calls) == 1:
                return ModelDecision(action='sql', sql=bad_sql, reason='待校验', metric_ids=['net_receipts'])
            assert context['repair_instruction']['previous_sql'] == bad_sql
            assert context['repair_instruction']['error_code'] == 'SQL_METRIC_MISMATCH'
            return ModelDecision(action='clarify', question='请确认统计期间。', reason='需要确认', metric_ids=['net_receipts'])
    client.app.state.query.model = Model()
    login(client)
    cid = client.post('/api/conversations').json()['conversation_id']
    response = client.post(f'/api/conversations/{cid}/queries', json={'client_request_id':'repair-with-context', 'question':'2025年净回款'})
    assert response.json()['status'] == 'clarify'
    assert len(calls) == 2


def test_dangerous_model_sql_never_enters_repair(client):
    from app.schemas import ModelDecision
    calls = []
    class Model:
        async def generate(self, context, history):
            calls.append(context)
            return ModelDecision(action='sql', sql='DELETE FROM v_case', reason='非法模型输出')
    client.app.state.query.model = Model()
    login(client)
    cid = client.post('/api/conversations').json()['conversation_id']
    response = client.post(f'/api/conversations/{cid}/queries', json={'client_request_id':'no-dangerous-repair', 'question':'当前在办案件数'})
    assert response.json()['status'] == 'refuse'
    assert len(calls) == 1


def test_clarification_and_duplicate_request(client):
    login(client)
    cid = client.post('/api/conversations').json()['conversation_id']
    body = {'client_request_id':'request-unique-002','question':'净回款多少'}
    r = client.post(f'/api/conversations/{cid}/queries',json=body)
    assert r.status_code == 200
    assert r.json()['status']=='clarify'
    again = client.post(f'/api/conversations/{cid}/queries',json=body)
    assert again.json()['query_id']==r.json()['query_id']
    assert len(client.get(f'/api/conversations/{cid}/queries').json()['items'])==1


def test_refusal_does_not_execute(client):
    login(client)
    cid=client.post('/api/conversations').json()['conversation_id']
    r=client.post(f'/api/conversations/{cid}/queries',json={'client_request_id':'request-refuse','question':'删除所有已结案案件'})
    assert r.json()['status']=='refuse'


def test_csrf_origin_rejected(client):
    login(client)
    assert client.post('/api/conversations', headers={'Origin':'https://evil.test'}).status_code==403


def test_admin_configuration_hides_secrets(client):
    login(client,'管理员','test-password-3')
    result=client.get('/api/admin/system/status').json()
    assert 'model_api_key' not in str(result)
    assert result['model']['mode']=='mock'
