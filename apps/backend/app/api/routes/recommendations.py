from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import CustomerDependency, get_recommendation_service
from app.domain.recommendations import (
    RecommendationEventCreate,
    RecommendationEventResponse,
    RecommendationResponse,
)
from app.services.recommendations import RecommendationService

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=RecommendationResponse)
async def get_recommendations(
    customer: CustomerDependency,
    recommendations: Annotated[RecommendationService, Depends(get_recommendation_service)],
    limit: Annotated[int, Query(ge=1, le=20)] = 6,
) -> RecommendationResponse:
    return await recommendations.recommend(user_id=customer.id, limit=limit)


@router.post(
    "/events",
    response_model=RecommendationEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def record_recommendation_event(
    payload: RecommendationEventCreate,
    customer: CustomerDependency,
    recommendations: Annotated[RecommendationService, Depends(get_recommendation_service)],
) -> RecommendationEventResponse:
    created = await recommendations.record_client_event(
        user_id=customer.id,
        payload=payload,
    )
    return RecommendationEventResponse(accepted=True, duplicate=not created)


@router.delete("/data", status_code=status.HTTP_204_NO_CONTENT)
async def purge_recommendation_data(
    customer: CustomerDependency,
    recommendations: Annotated[RecommendationService, Depends(get_recommendation_service)],
) -> Response:
    await recommendations.purge_user_data(user_id=customer.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
