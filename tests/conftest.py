"""Unit and desktop tests use injected transports and never contact live services."""
import socket

import httpx
import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Use a fixture or mock transport for network requests.')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
