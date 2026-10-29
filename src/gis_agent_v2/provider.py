"""A bounded tool-calling probe. Upstream responses and credentials are not returned."""
import json
import httpx
from .config import Settings


def check_connection(key):
    settings = Settings(api_key=key)
    payload = {'model': settings.model, 'messages': [{'role': 'user', 'content': 'Call connection_probe with ok=true.'}],
               'tools': [{'type': 'function', 'function': {'name': 'connection_probe',
                         'description': 'Confirm tool calling works.', 'parameters': {'type': 'object',
                         'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}}}],
               'tool_choice': {'type': 'function', 'function': {'name': 'connection_probe'}},
               'thinking': {'type': 'disabled'}, 'temperature': 0, 'max_tokens': 128, 'stream': False}
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.post(settings.base_url + '/chat/completions', json=payload,
                                   headers={'Authorization': 'Bearer ' + key})
    except httpx.TimeoutException:
        return {'ok': False, 'code': 'TIMEOUT', 'message': '连接超时，请稍后重试。'}
    except httpx.HTTPError:
        return {'ok': False, 'code': 'NETWORK_ERROR', 'message': '无法连接DeepSeek，请检查网络。'}
    messages = {401: 'Key无效或已失效。', 402: '账户余额不足，请到DeepSeek平台检查。',
                403: '当前Key没有调用权限。', 429: '请求过于频繁，请稍后重试。'}
    if response.status_code != 200:
        return {'ok': False, 'code': 'HTTP_' + str(response.status_code),
                'message': messages.get(response.status_code, 'DeepSeek服务暂时不可用，请稍后重试。')}
    try:
        calls = response.json()['choices'][0]['message']['tool_calls']
        function = calls[0]['function']
        if function['name'] != 'connection_probe' or json.loads(function['arguments']) != {'ok': True}:
            raise ValueError()
    except (ValueError, KeyError, IndexError, TypeError):
        return {'ok': False, 'code': 'TOOL_CALL_UNAVAILABLE', 'message': '已收到响应，但工具调用格式不符合要求。'}
    return {'ok': True, 'code': 'CONNECTED', 'message': 'DeepSeek连接与工具调用测试通过。'}
