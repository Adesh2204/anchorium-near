"""Tests never reach NEAR AI Cloud: any connection attempt to a near.ai host fails the test (zero spend, R3)."""
import socket

import pytest

_real_getaddrinfo = socket.getaddrinfo


class NearAINetworkUsed(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_near_ai_network(monkeypatch):
    def guarded(host, *args, **kwargs):
        if isinstance(host, str) and (host == 'near.ai' or host.endswith('.near.ai')):
            raise NearAINetworkUsed(f'a test tried to reach {host}; mock NEAR AI Cloud instead')
        return _real_getaddrinfo(host, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded)
