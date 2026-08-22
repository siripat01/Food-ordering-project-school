from __future__ import annotations

import os

import pytest

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789")


@pytest.fixture
def settings():
    from app.core.config import Settings

    return Settings(
        _env_file=None,
        app_env="test",
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="test-secret-0123456789abcdef0123456789",
        cookie_secure=False,
    )
