import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.schemas import ModelDecision
from app.metadata import MetadataService
from app.model_client import ModelClient, ModelError


def test_decision_rejects_inconsistent_actions():
    with pytest.raises(ValueError):
        ModelDecision(action='clarify', sql='SELECT 1', question='哪一年？', reason='缺时间')


def test_metadata_invalid_import_keeps_current(tmp_path):
    meta = MetadataService(tmp_path)
    version = meta.version
    with pytest.raises(ValueError):
        meta.publish({'schema': {'tables': {}}, 'dictionary': {}, 'metrics': []}, 'admin')
    assert meta.version == version


def test_http_model_uses_contract_without_changing_code():
    def handler(request):
        body = json.loads(request.content)
        assert body['model'] == 'finetuned-v2'
        assert 'response_format' not in body and 'thinking' not in body
        assert 'retrieved_context' in body['messages'][-1]['content']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'action':'clarify','sql':None,'question':'哪一年？','reason':'缺少时间','metric_ids':[]})}}]})
    cfg = Settings(model_mode='http', model_base_url='https://model.test/v1', model_name='finetuned-v2',
                   model_json_mode=False, model_thinking=None)
    client = ModelClient(cfg, transport=httpx.MockTransport(handler))
    out = asyncio.run(client.generate({'question':'净回款','retrieved_context':{}}, []))
    assert out.action == 'clarify'


def test_mock_is_explicit_and_clarifies_missing_time():
    client = ModelClient(Settings(model_mode='mock'))
    out = asyncio.run(client.generate({'question':'净回款多少','conditions':{},'retrieved_context':{}}, []))
    assert out.action == 'clarify'


def test_http_failure_becomes_sanitized_error():
    def handler(request):
        raise httpx.ConnectError('secret connection detail', request=request)
    client = ModelClient(Settings(model_mode='http',model_base_url='https://model.test/v1'), transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError) as error:
        asyncio.run(client.generate({'question':'净回款'}, []))
    assert 'secret' not in str(error.value)


def test_deepseek_json_and_non_thinking_options():
    def handler(request):
        body = json.loads(request.content)
        assert body['response_format'] == {'type': 'json_object'}
        assert body['thinking'] == {'type': 'disabled'}
        assert request.headers['Authorization'] == 'Bearer local-test-key'
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'action': 'clarify', 'sql': None, 'question': '哪一年？', 'reason': '缺时间', 'metric_ids': [],
        })}}]})
    cfg = Settings(model_mode='http', model_base_url='https://api.deepseek.com',
                   model_name='deepseek-flash', model_api_key='local-test-key',
                   model_json_mode=True, model_thinking='disabled')
    output = asyncio.run(ModelClient(cfg, transport=httpx.MockTransport(handler)).generate({'question': '净回款'}, []))
    assert output.action == 'clarify'
