from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal, Self

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    mongodb_uri: SecretStr
    mongodb_users_database: str = "Users"
    mongodb_products_database: str = "Products"
    mongodb_orders_database: str = "Orders"

    jwt_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = "food-ordering-api"
    jwt_audience: str = "food-ordering-web"
    access_token_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    oauth_state_ttl_minutes: int = Field(default=10, ge=2, le=30)

    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    backend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    cors_origins: list[AnyHttpUrl] = [AnyHttpUrl("http://localhost:3000")]
    cookie_secure: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    metrics_enabled: bool = True
    sse_heartbeat_seconds: int = Field(default=15, ge=5, le=60)
    sse_subscriber_queue_size: int = Field(default=50, ge=10, le=500)

    line_enabled: bool = False
    line_channel_secret: SecretStr | None = None
    line_channel_access_token: SecretStr | None = None
    line_login_channel_id: str | None = None
    line_login_channel_secret: SecretStr | None = None
    line_redirect_uri: AnyHttpUrl | None = None

    llm_enabled: bool = False
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("llm_api_key", "LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_api_base: AnyHttpUrl | None = None
    llm_model: str = "ordering-assistant"
    llm_primary_model: str = "deepseek/deepseek-v4-flash"
    llm_complex_model: str = "deepseek/deepseek-v4-pro"
    llm_fallback_models: list[str] = Field(default_factory=list)
    llm_complexity_routing_enabled: bool = True
    llm_complexity_classifier: Literal["heuristic", "llm"] = "heuristic"
    llm_complexity_classifier_timeout_ms: int = Field(default=1500, ge=250, le=5000)
    llm_complexity_keywords: list[str] = Field(
        default_factory=lambda: [
            "หลายรายการ",
            "หลายเมนู",
            "อย่างละ",
            "แยกหมายเหตุ",
            "แพ้อาหาร",
            "ข้อจำกัด",
            "งบประมาณ",
            "เปรียบเทียบ",
            "multiple items",
            "dietary restriction",
            "allergy",
            "compare",
        ]
    )
    llm_routing_strategy: Literal["simple-shuffle", "least-busy", "latency-based-routing"] = (
        "simple-shuffle"
    )
    llm_timeout_seconds: float = Field(default=20, ge=1, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_tool_iterations: int = Field(default=4, ge=1, le=8)
    llm_max_output_tokens: int = Field(default=800, ge=100, le=4000)
    llm_memory_messages: int = Field(default=12, ge=2, le=30)
    llm_memory_ttl_minutes: int = Field(default=30, ge=5, le=1440)
    llm_confirmation_ttl_minutes: int = Field(default=5, ge=1, le=30)
    llm_requests_per_minute: int = Field(default=10, ge=1, le=120)
    llm_input_cost_per_million: float = Field(default=0, ge=0)
    llm_output_cost_per_million: float = Field(default=0, ge=0)
    llm_cache_enabled: bool = False
    llm_cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    llm_cache_max_entries: int = Field(default=200, ge=10, le=10_000)

    recommender_enabled: bool = False
    recommender_url: AnyHttpUrl | None = None
    recommender_timeout_seconds: float = Field(default=3, ge=0.5, le=15)
    recommender_mode: Literal["local", "external_first", "external_fallback"] = "local"
    recommendation_user_ref_secret: SecretStr = Field(min_length=32)
    recommendation_user_ref_key_version: str = Field(default="v1", pattern=r"^v[1-9][0-9]*$")
    recommendation_user_ref_previous_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    recommendation_event_retention_days: int = Field(default=180, ge=30, le=730)
    recommendation_slate_retention_days: int = Field(default=7, ge=1, le=30)
    recommendation_daily_impression_cap: int = Field(default=10, ge=1, le=100)
    recommendation_daily_click_cap: int = Field(default=5, ge=1, le=50)
    recommendation_daily_add_to_cart_cap: int = Field(default=3, ge=1, le=20)
    recommendation_item_item_rollout_percent: int = Field(default=0, ge=0, le=100)
    recommendation_model_poll_seconds: int = Field(default=30, ge=5, le=300)
    recommendation_result_cache_ttl_seconds: int = Field(default=30, ge=1, le=300)
    recommendation_result_cache_max_entries: int = Field(default=500, ge=10, le=10_000)
    recommendation_model_max_products: int = Field(default=10_000, ge=100, le=100_000)
    recommendation_model_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )
    recommendation_profile_order_limit: int = Field(default=50, ge=1, le=200)
    recommendation_profile_product_limit: int = Field(default=20, ge=1, le=50)

    @field_validator("mongodb_uri")
    @classmethod
    def validate_mongodb_uri(cls, value: SecretStr) -> SecretStr:
        uri = value.get_secret_value()
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("MONGODB_URI must use mongodb:// or mongodb+srv://")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def reject_placeholder_jwt_secret(cls, value: SecretStr) -> SecretStr:
        return cls._validate_generated_secret(value, "JWT_SECRET")

    @field_validator("recommendation_user_ref_secret")
    @classmethod
    def reject_placeholder_recommendation_secret(cls, value: SecretStr) -> SecretStr:
        return cls._validate_generated_secret(value, "RECOMMENDATION_USER_REF_SECRET")

    @staticmethod
    def _validate_generated_secret(value: SecretStr, variable_name: str) -> SecretStr:
        secret = value.get_secret_value()
        normalized = secret.lower().strip()
        if normalized.startswith("replace-") or normalized in {
            "changeme",
            "mysecret",
            "secret",
        }:
            raise ValueError(
                f"{variable_name} must be a generated random value, not a placeholder"
            )
        if len(set(secret)) < 12:
            raise ValueError(f"{variable_name} does not contain enough character diversity")
        return value

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard_cors(cls, value: list[AnyHttpUrl]) -> list[AnyHttpUrl]:
        if any(str(origin) == "*" for origin in value):
            raise ValueError("CORS_ORIGINS must be an explicit allowlist")
        return value

    @model_validator(mode="after")
    def validate_feature_configuration(self) -> Self:
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if self.line_enabled:
            required = {
                "LINE_CHANNEL_SECRET": self.line_channel_secret,
                "LINE_CHANNEL_ACCESS_TOKEN": self.line_channel_access_token,
                "LINE_LOGIN_CHANNEL_ID": self.line_login_channel_id,
                "LINE_LOGIN_CHANNEL_SECRET": self.line_login_channel_secret,
                "LINE_REDIRECT_URI": self.line_redirect_uri,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("LINE_ENABLED requires: " + ", ".join(sorted(missing)))
        if self.llm_enabled and not self.llm_api_key:
            raise ValueError("LLM_ENABLED requires LLM_API_KEY")
        if self.recommender_enabled and not self.recommender_url:
            raise ValueError("RECOMMENDER_ENABLED requires RECOMMENDER_URL")
        if self.recommender_mode != "local" and not self.recommender_enabled:
            raise ValueError("External RECOMMENDER_MODE requires RECOMMENDER_ENABLED=true")
        previous_values: set[str] = set()
        current_secret = self.recommendation_user_ref_secret.get_secret_value()
        jwt_secret = self.jwt_secret.get_secret_value()
        if current_secret == jwt_secret:
            raise ValueError("Recommendation pseudonym secret must not reuse JWT_SECRET")
        for version, secret in self.recommendation_user_ref_previous_secrets.items():
            if not re.fullmatch(r"v[1-9][0-9]*", version):
                raise ValueError(
                    "RECOMMENDATION_USER_REF_PREVIOUS_SECRETS keys must be versions like v1"
                )
            if version == self.recommendation_user_ref_key_version:
                raise ValueError(
                    "Current recommendation key version cannot also be a previous version"
                )
            self._validate_generated_secret(
                secret,
                "RECOMMENDATION_USER_REF_PREVIOUS_SECRETS",
            )
            value = secret.get_secret_value()
            if value == jwt_secret:
                raise ValueError(
                    "Previous recommendation pseudonym secrets must not reuse JWT_SECRET"
                )
            if value == current_secret or value in previous_values:
                raise ValueError("Recommendation pseudonym secrets must be distinct")
            previous_values.add(value)
        return self

    @property
    def cors_origin_strings(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.cors_origins]


@lru_cache
def get_settings() -> Settings:
    return Settings()
