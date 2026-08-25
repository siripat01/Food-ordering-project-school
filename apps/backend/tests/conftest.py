from __future__ import annotations

import os

import pytest

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789")
os.environ.setdefault(
    "RECOMMENDATION_USER_REF_SECRET",
    "test-recommendation-ref-secret-0123456789abcdef",
)
# The Taskiq broker is a module-level singleton built from the process
# environment, because `taskiq worker app.core.taskiq:broker` resolves it by
# import path. Point it at the same Redis the integration tests use.
os.environ.setdefault(
    "REDIS_URL",
    os.getenv("TEST_REDIS_URL", "redis://localhost:6379/0"),
)


@pytest.fixture
def settings():
    from app.core.config import Settings

    return Settings(
        _env_file=None,
        app_env="test",
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="test-secret-0123456789abcdef0123456789",
        recommendation_user_ref_secret="test-recommendation-ref-secret-0123456789abcdef",
        cookie_secure=False,
    )
