import os

import pytest

from convest.annotation.client import create_client, proxy_for_ark


@pytest.fixture
def proxy_env(monkeypatch):
    for name in list(os.environ):
        if name.lower().endswith('_proxy'):
            monkeypatch.delenv(name)
    return monkeypatch


@pytest.mark.parametrize('values, expected', [
    ({'ALL_PROXY': 'socks://127.0.0.1:7897'}, 'socks5://127.0.0.1:7897'),
    ({'all_proxy': 'socks://localhost:1080'}, 'socks5://localhost:1080'),
    ({'HTTPS_PROXY': 'socks://localhost:1081'}, 'socks5://localhost:1081'),
    ({'ALL_PROXY': 'socks://localhost:1080', 'HTTPS_PROXY': 'http://localhost:7897'}, 'http://localhost:7897'),
    ({'ALL_PROXY': 'socks5h://localhost:1080'}, 'socks5h://localhost:1080'),
    ({'ALL_PROXY': 'socks://localhost:1080', 'NO_PROXY': 'ark.cn-beijing.volces.com'}, None),
    ({'ALL_PROXY': 'socks://localhost:1080', 'NO_PROXY': '*'}, None),
    ({}, None),
])
def test_proxy_compatibility_without_network_or_environment_changes(proxy_env, values, expected):
    pytest.importorskip('openai')
    pytest.importorskip('socksio')
    for name, value in values.items():
        proxy_env.setenv(name, value)
    before = dict(os.environ)
    assert proxy_for_ark() == expected
    with create_client('diagnostic-placeholder', timeout=15) as client:
        assert client.timeout == 15
        assert client.max_retries == 0
    assert dict(os.environ) == before
