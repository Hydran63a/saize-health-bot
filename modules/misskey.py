"""Misskey API クライアント（Phase 5）。

確認した仕様（Misskey 公式ドキュメントとソースコード）:
- API はすべて POST・JSON。ベース URL は https://{ホスト}/api
- 認証は Authorization: Bearer ヘッダー（本文の "i" より優先される）
- POST /api/i            : 自分のアカウント情報（権限 read:account）
- POST /api/notes/create : ノートを作る（権限 write:notes）。成功時 {"createdNote": {...}}
- エラーは {"error": {"message", "code", "id", "kind"}}。401=認証失敗、403=権限不足、429=回数制限（Retry-After 付き）

安全のための決まり:
- トークンは本文に入れず、ヘッダーで送る。例外メッセージやログにもトークンを含めない
- 証明書の確認は無効にしない（truststore で OS の証明書ストアを使う）
"""

from __future__ import annotations

import ssl
from typing import Any

import httpx

USER_AGENT = "saize-health-bot (unofficial)"


class MisskeyError(Exception):
    """Misskey API のエラー。status は HTTP ステータス、code は Misskey のエラーコード。"""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class AuthenticationError(MisskeyError):
    """401：トークンが無い・間違っている。"""


class PermissionDeniedError(MisskeyError):
    """403：トークンに必要な権限が無い、またはアカウントが使えない。"""


class RateLimitError(MisskeyError):
    """429：回数制限。retry_after は待つべき秒数（分かる場合）。"""

    def __init__(self, message: str, *, retry_after: int | None = None, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


def make_ssl_context() -> ssl.SSLContext:
    """OS の証明書ストアを使う SSL 設定。truststore が無ければ Python 標準の設定。"""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


class MisskeyClient:
    def __init__(
        self,
        host: str,
        token: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,  # テストで「にせのサーバー」を差し込むため
    ) -> None:
        if not host:
            raise ValueError("MISSKEY_HOST が設定されていません")
        if not token:
            raise ValueError("MISSKEY_TOKEN が設定されていません")
        self.host = host
        self._client = httpx.Client(
            base_url=f"https://{host}/api",
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            timeout=timeout,
            verify=make_ssl_context(),
            transport=transport,
        )

    def __enter__(self) -> "MisskeyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(endpoint, json=payload)
        except httpx.TimeoutException as exc:
            raise MisskeyError(f"{self.host} への接続がタイムアウトしました") from exc
        except httpx.HTTPError as exc:
            # exc の文字列には URL などが入る。ヘッダー（トークン）は含まれない
            raise MisskeyError(f"{self.host} に接続できませんでした: {type(exc).__name__}") from exc

        if response.status_code == 204:
            return {}
        if response.is_success:
            return response.json()
        raise self._error_from(response)

    def _error_from(self, response: httpx.Response) -> MisskeyError:
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        code = error.get("code")
        message = f"Misskey API エラー（HTTP {response.status_code}" + (f"・{code}）" if code else "）")
        if error.get("message"):
            message += f": {error['message']}"

        status = response.status_code
        if status == 401:
            return AuthenticationError(message + "（トークンを確認してください）", status=status, code=code)
        if status == 403:
            return PermissionDeniedError(message + "（トークンの権限を確認してください）", status=status, code=code)
        if status == 429:
            retry = response.headers.get("Retry-After")
            return RateLimitError(message, status=status, code=code,
                                  retry_after=int(retry) if retry and retry.isdigit() else None)
        return MisskeyError(message, status=status, code=code)

    # ------------------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        """自分のアカウント情報（POST /api/i）。"""
        return self._post("/i", {})

    def create_note(self, text: str, *, visibility: str = "public", local_only: bool = False) -> dict[str, Any]:
        """ノートを作る（POST /api/notes/create）。作られたノートを返す。"""
        if not text.strip():
            raise ValueError("投稿する文章が空です")
        result = self._post("/notes/create", {"text": text, "visibility": visibility, "localOnly": local_only})
        note = result.get("createdNote")
        if not note:
            raise MisskeyError("投稿の結果に createdNote がありません")
        return note
