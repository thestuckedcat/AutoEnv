"""Protocol tests may connect only to loopback; never to registered lab targets."""
from __future__ import annotations

import ipaddress
import socket

import pytest

from local_remote.webhdfs import LocalWebHDFS


@pytest.fixture(autouse=True)
def loopback_connections_only(monkeypatch):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def check(address):
        if not isinstance(address, tuple):
            return
        host = address[0]
        if host == "localhost":
            return
        try:
            allowed = ipaddress.ip_address(host).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise AssertionError(f"offline tests cannot connect outside loopback: {address!r}")

    def connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture
def webhdfs_factory():
    services = []

    def create(directories=None, downloads=None):
        service = LocalWebHDFS(directories, downloads)
        services.append(service)
        return service

    yield create
    for service in reversed(services):
        service.close()
