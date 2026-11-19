"""Misskey クライアントのテスト。本物のサーバーには接続せず、httpx の MockTransport（にせのサーバー）を使う。"""

import json

import httpx
import pytest

from modules import misskey as mk

TOKEN = "secret-token-for-test"


def make_client(handler):
    return mk.MisskeyClient("misskey.example.org", TOKEN, transport=httpx.MockTransport(handler))


def error_response(status, code, message="something", headers=None):
    return httpx.Response(status, json={"error": {"code": code, "message": message, "id": "x", "kind": "client"}},
                          headers=headers)


def test_create_note_sends_expected_request():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"createdNote": {"id": "abc123", "text": seen["body"]["text"]}})

    with make_client(handler) as client:
        note = client.create_note("こんにちは", visibility="public", local_only=True)

    assert note["id"] == "abc123"
    assert seen["url"] == "https://misskey.example.org/api/notes/create"
    assert seen["auth"] == f"Bearer {TOKEN}"  # トークンはヘッダーで送る
    assert seen["body"] == {"text": "こんにちは", "visibility": "public", "localOnly": True}
    assert TOKEN not in json.dumps(seen["body"])  # 本文にはトークンを入れない


def test_get_me():
    def handler(request):
        assert request.url.path == "/api/i"
        return httpx.Response(200, json={"username": "saize_bot", "isBot": True})

    with make_client(handler) as client:
        assert client.get_me()["username"] == "saize_bot"


@pytest.mark.parametrize(
    "status,code,error_class",
    [
        (401, "AUTHENTICATION_FAILED", mk.AuthenticationError),
        (403, "PERMISSION_DENIED", mk.PermissionDeniedError),
        (500, "INTERNAL_ERROR", mk.MisskeyError),
    ],
)
def test_errors_are_classified_and_never_contain_token(status, code, error_class):
    with make_client(lambda r: error_response(status, code)) as client, pytest.raises(error_class) as info:
        client.create_note("テスト")
    assert info.value.status == status
    assert info.value.code == code
    assert TOKEN not in str(info.value)


def test_rate_limit_has_retry_after():
    handler = lambda r: error_response(429, "RATE_LIMIT_EXCEEDED", headers={"Retry-After": "42"})  # noqa: E731
    with make_client(handler) as client, pytest.raises(mk.RateLimitError) as info:
        client.create_note("テスト")
    assert info.value.retry_after == 42


def test_non_json_error_body_is_handled():
    with make_client(lambda r: httpx.Response(502, text="Bad Gateway")) as client, pytest.raises(mk.MisskeyError) as info:
        client.create_note("テスト")
    assert info.value.status == 502


def test_timeout_is_wrapped():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with make_client(handler) as client, pytest.raises(mk.MisskeyError, match="タイムアウト") as info:
        client.create_note("テスト")
    assert TOKEN not in str(info.value)


def test_missing_created_note_is_an_error():
    with make_client(lambda r: httpx.Response(200, json={})) as client, pytest.raises(mk.MisskeyError):
        client.create_note("テスト")


def test_empty_text_is_rejected_before_sending():
    def handler(request):
        raise AssertionError("送信してはいけない")

    with make_client(handler) as client, pytest.raises(ValueError):
        client.create_note("   ")


@pytest.mark.parametrize("host,token", [("", TOKEN), ("misskey.example.org", "")])
def test_missing_settings_are_rejected(host, token):
    with pytest.raises(ValueError):
        mk.MisskeyClient(host, token)
