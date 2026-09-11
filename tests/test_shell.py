from __future__ import annotations

from urllib.parse import urlparse

from app.api import shell


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "image/png", "content-length": "8"}

    def iter_content(self, chunk_size=65536):
        yield b"\x89PNG\r\n\x1a\n"


def test_favicon_proxy_fetches_public_icon_and_reuses_disk_cache(authenticated_client, monkeypatch):
    calls = []
    monkeypatch.setattr(shell, "_public_http_url", lambda value: urlparse(value))
    monkeypatch.setattr(shell.requests, "get", lambda url, **kwargs: calls.append(url) or _FakeResponse())

    first = authenticated_client.get("/api/shell/favicon", params={"url": "https://example.test/products/1"})
    assert first.status_code == 200
    assert first.content == b"\x89PNG\r\n\x1a\n"
    assert calls == ["https://example.test/favicon.ico"]

    def unexpected_request(*args, **kwargs):
        raise AssertionError("cached favicon should not be fetched again")

    monkeypatch.setattr(shell.requests, "get", unexpected_request)
    second = authenticated_client.get("/api/shell/favicon", params={"url": "https://example.test/products/1"})
    assert second.status_code == 200
    assert second.content == first.content
    assert second.headers["cache-control"].startswith("private, max-age=")


def test_favicon_proxy_rejects_private_addresses(authenticated_client):
    response = authenticated_client.get("/api/shell/favicon", params={"url": "http://127.0.0.1:18180"})
    assert response.status_code == 403
