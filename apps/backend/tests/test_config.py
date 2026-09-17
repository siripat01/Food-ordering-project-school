from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def test_missing_required_configuration_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    errors = {str(error["loc"][0]) for error in exc_info.value.errors()}
    assert {"mongodb_uri", "jwt_secret"} <= errors


def test_weak_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            mongodb_uri="mongodb://localhost:27017",
            jwt_secret="too-short",
        )


def test_placeholder_jwt_secret_is_rejected() -> None:
    placeholder = "replace-with-at-least-32-random-characters"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            mongodb_uri="mongodb://localhost:27017",
            jwt_secret=placeholder,
        )
    assert placeholder not in str(exc_info.value)


def test_application_can_be_constructed_with_valid_configuration(settings: Settings) -> None:
    app = create_app(settings, initialize_clients=False)
    assert app.title == "Food Ordering API"
    paths = {route.path for route in app.routes}
    assert "/api/v1/health/live" in paths
    assert "/api/v1/health/ready" in paths
    assert not any(path.startswith("/api/v1/recommendations") for path in paths)


def test_recommendation_configuration_is_not_part_of_settings() -> None:
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://localhost:27017",
        jwt_secret="test-secret-0123456789abcdef0123456789",
    )

    assert not hasattr(settings, "recommender_enabled")
    assert not hasattr(settings, "recommendation_user_ref_secret")


def test_llm_requires_provider_neutral_api_key(settings: Settings) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            mongodb_uri=settings.mongodb_uri,
            jwt_secret=settings.jwt_secret,
            llm_enabled=True,
        )
    assert "LLM_ENABLED requires LLM_API_KEY" in str(exc_info.value)


def test_legacy_openai_key_name_remains_backward_compatible(settings: Settings) -> None:
    configured = Settings(
        _env_file=None,
        mongodb_uri=settings.mongodb_uri,
        jwt_secret=settings.jwt_secret,
        llm_enabled=True,
        OPENAI_API_KEY="legacy-test-provider-key",
    )

    assert configured.llm_api_key is not None
