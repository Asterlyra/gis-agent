import json
import httpx
import pytest
from fastapi.testclient import TestClient
from gis_agent_v2 import credentials, config, provider
from gis_agent_v2.web import app

KEY = 'sk-test-only-not-a-real-key-123456789'
NEW_KEY = 'sk-test-only-replacement-987654321'
HEADERS = {'X-GIS-Request':'settings', 'Origin':'http://testserver'}


@pytest.fixture
def vault(monkeypatch, tmp_path):
    class MemoryVault:
        priority = 10
        value = None
        def get_password(self, *args): return self.value
        def set_password(self, service, account, value): self.value = value
        def delete_password(self, *args): self.value = None
    backend = MemoryVault()
    monkeypatch.setenv('GIS_APP_HOME', str(tmp_path))
    monkeypatch.delenv('GIS_CONFIG_SNAPSHOT', raising=False)
    monkeypatch.setenv('GIS_API_KEY', 'legacy-test-key')
    monkeypatch.setattr(credentials.store, 'backend', lambda: backend)
    return backend


def test_save_replace_clear_without_exposing_or_writing_key(vault, tmp_path):
    client = TestClient(app)
    for key in (KEY, NEW_KEY):
        response = client.put('/api/settings', headers=HEADERS, json={'key':key})
        assert response.status_code == 200 and response.json()['configured']
        assert vault.value == key and config.Settings.load().api_key == key
        for endpoint in ('/api/settings', '/api/health'):
            response = client.get(endpoint)
            assert key not in response.text and 'api_key' not in response.text
        assert key not in (tmp_path/'settings.json').read_text()
    assert client.delete('/api/settings', headers=HEADERS).status_code == 200
    assert vault.value is None
    assert not config.Settings.load().api_key  # legacy key must not reappear
    assert not client.get('/api/settings').json()['configured']


def test_settings_are_same_origin_and_validation_never_echoes_input(vault):
    client = TestClient(app)
    for method, path, body in [('PUT','/api/settings',{'key':KEY}), ('POST','/api/settings/test',{'key':KEY}), ('DELETE','/api/settings',None)]:
        assert client.request(method,path,json=body).status_code == 403
        assert client.request(method,path,json=body,headers={**HEADERS,'Origin':'https://evil.example'}).status_code == 403
        assert client.request(method,path,json=body,headers={**HEADERS,'Sec-Fetch-Site':'cross-site'}).status_code == 403
    response = client.put('/api/settings',headers=HEADERS,json={'key':{'secret':KEY}})
    assert response.status_code == 422 and KEY not in response.text
    assert vault.value is None


def test_snapshot_keeps_running_worker_configuration(vault, monkeypatch):
    credentials.save_key(KEY)
    snapshot = config.Settings.load().worker_environment()
    credentials.save_key(NEW_KEY)
    for key in ('GIS_CONFIG_SNAPSHOT','GIS_WORKER_API_KEY'):
        monkeypatch.setenv(key,snapshot[key])
    assert config.Settings.load().api_key == KEY
    assert config.Settings.load().base_url == 'https://api.deepseek.com'


def test_probe_checks_tool_call_without_saving_or_returning_upstream_secret(vault, monkeypatch):
    original = httpx.Client
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'function':{'name':'connection_probe','arguments':'{"ok":true}'}}]}}]})
    monkeypatch.setattr(provider.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    client = TestClient(app)
    result = client.post('/api/settings/test',headers=HEADERS,json={'key':KEY})
    assert result.json()['ok'] and vault.value is None and KEY not in result.text
    assert str(seen[0].url) == 'https://api.deepseek.com/chat/completions'
    assert seen[0].headers['Authorization'] == 'Bearer ' + KEY
    monkeypatch.setattr(provider.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(lambda r:httpx.Response(401,text=KEY)),**kwargs))
    result = client.post('/api/settings/test',headers=HEADERS,json={'key':KEY})
    assert result.json()['code'] == 'HTTP_401' and KEY not in result.text


def test_store_failure_has_no_plaintext_fallback(vault, monkeypatch, tmp_path):
    def unavailable():
        raise credentials.CredentialError('系统凭据存储不可用。')
    monkeypatch.setattr(credentials.store,'backend',unavailable)
    result = TestClient(app).put('/api/settings',headers=HEADERS,json={'key':KEY})
    assert result.status_code == 503 and KEY not in result.text
    assert not (tmp_path/'settings.json').exists()
