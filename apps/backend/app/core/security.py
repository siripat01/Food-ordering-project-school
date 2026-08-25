from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

import jwt

from app.core.config import Settings


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"
    LINE_CHAT = "line_chat"


class TokenError(ValueError):
    pass


def create_token(
    *,
    subject: str,
    token_type: TokenType,
    settings: Settings,
    lifetime: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "iat": now,
        "nbf": now,
        "exp": now + lifetime,
        "jti": str(uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def decode_token(
    token: str,
    *,
    expected_type: TokenType,
    settings: Settings,
) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "type", "iat", "nbf", "exp", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired authentication token") from exc
    if payload.get("type") != expected_type.value:
        raise TokenError("Invalid authentication token type")
    return dict(payload)
