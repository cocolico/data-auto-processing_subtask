"""Shared Ark client with process-local proxy compatibility."""
from urllib.request import getproxies, proxy_bypass

ARK_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'
ARK_HOST = 'ark.cn-beijing.volces.com'


def proxy_for_ark():
    proxies = getproxies()
    if proxy_bypass(ARK_HOST):
        return None
    proxy = proxies.get('https') or proxies.get('all')
    if proxy and proxy.lower().startswith('socks://'):
        return 'socks5://' + proxy.split('://', 1)[1]
    if proxy and '://' not in proxy:
        return 'http://' + proxy
    return proxy


def create_client(key, *, timeout=420):
    import httpx
    from openai import DefaultHttpxClient, OpenAI

    # An explicit transport prevents HTTPX from parsing unrelated environment
    # proxies again. Keep environment CA certificates and never mutate os.environ.
    transport = httpx.HTTPTransport(proxy=proxy_for_ark(), trust_env=True)
    http_client = DefaultHttpxClient(transport=transport, timeout=timeout)
    try:
        return OpenAI(api_key=key, base_url=ARK_BASE_URL, max_retries=0,
                      timeout=timeout, http_client=http_client)
    except Exception:
        http_client.close()
        raise
