import hmac

from fastapi import APIRouter, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    if not request.app.state.settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    configured_token = request.app.state.settings.metrics_auth_token
    if configured_token is not None:
        expected = f"Bearer {configured_token.get_secret_value()}"
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    db = getattr(request.app.state, "db", None)
    redis = getattr(request.app.state, "redis", None)
    if db is not None and redis is not None:
        try:
            request.app.state.metrics.set_dependency_status("mongodb", bool(await db.ping()))
        except Exception:
            request.app.state.metrics.set_dependency_status("mongodb", False)
        try:
            request.app.state.metrics.set_dependency_status("redis", bool(await redis.ping()))
        except Exception:
            request.app.state.metrics.set_dependency_status("redis", False)
    return Response(
        content=request.app.state.metrics.render(),
        media_type=CONTENT_TYPE_LATEST,
    )
