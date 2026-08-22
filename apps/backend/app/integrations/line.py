from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

import httpx
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser

from app.core.config import Settings


class LineUpstreamError(RuntimeError):
    pass


class LineOAuthClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    def authorization_url(self, *, state: str, nonce: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.settings.line_login_channel_id,
            "redirect_uri": str(self.settings.line_redirect_uri),
            "state": state,
            "scope": "profile openid email",
            "nonce": nonce,
        }
        return "https://access.line.me/oauth2/v2.1/authorize?" + urlencode(params)

    async def exchange_and_verify(self, *, code: str, nonce: str) -> dict[str, Any]:
        token_response = await self.client.post(
            "https://api.line.me/oauth2/v2.1/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": str(self.settings.line_redirect_uri),
                "client_id": self.settings.line_login_channel_id,
                "client_secret": self.settings.line_login_channel_secret.get_secret_value()
                if self.settings.line_login_channel_secret
                else "",
            },
        )
        if token_response.status_code != 200:
            raise LineUpstreamError("LINE token exchange failed")
        id_token = token_response.json().get("id_token")
        if not isinstance(id_token, str):
            raise LineUpstreamError("LINE did not return an ID token")
        verify_response = await self.client.post(
            "https://api.line.me/oauth2/v2.1/verify",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "id_token": id_token,
                "client_id": self.settings.line_login_channel_id,
                "nonce": nonce,
            },
        )
        if verify_response.status_code != 200:
            raise LineUpstreamError("LINE ID token verification failed")
        claims = cast(dict[str, Any], verify_response.json())
        if claims.get("nonce") != nonce or not claims.get("sub"):
            raise LineUpstreamError("LINE ID token claims are invalid")
        return claims


class LineBotClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_client: AsyncApiClient | None = None
        self.messaging: AsyncMessagingApi | None = None
        self.parser: WebhookParser | None = None

    async def start(self) -> None:
        if not self.settings.line_enabled:
            return
        configuration = Configuration(
            access_token=self.settings.line_channel_access_token.get_secret_value()
            if self.settings.line_channel_access_token
            else ""
        )
        self.api_client = AsyncApiClient(configuration)
        self.messaging = AsyncMessagingApi(self.api_client)
        self.parser = WebhookParser(
            self.settings.line_channel_secret.get_secret_value()
            if self.settings.line_channel_secret
            else ""
        )

    async def close(self) -> None:
        if self.api_client is not None:
            await self.api_client.close()
        self.api_client = None
        self.messaging = None
        self.parser = None

    def parse(self, body: str, signature: str) -> list[Any]:
        if self.parser is None:
            raise RuntimeError("LINE integration is disabled")
        return cast(list[Any], self.parser.parse(body, signature))

    async def reply_text(self, *, reply_token: str, text: str) -> None:
        if self.messaging is None:
            raise RuntimeError("LINE integration is disabled")
        await self.messaging.reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text[:5000])],
            )
        )

    async def push_text(self, *, line_user_id: str, text: str) -> None:
        if self.messaging is None:
            raise RuntimeError("LINE integration is disabled")
        await self.messaging.push_message(
            PushMessageRequest(
                to=line_user_id,
                messages=[TextMessage(text=text[:5000])],
            )
        )
