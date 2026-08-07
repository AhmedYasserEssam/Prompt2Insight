from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_analytics_request_service
from app.application.analytics.run_analytics_request import AnalyticsRequestService
from app.domain.analytics.models import AnalyticsRequest, AnalyticsResponse

router = APIRouter(prefix="/conversations", tags=["analytics"])
AnalyticsRequestServiceDependency = Annotated[
    AnalyticsRequestService, Depends(get_analytics_request_service)
]


@router.post("/{conversation_id}/requests", response_model=AnalyticsResponse)
async def create_analytics_request(
    conversation_id: UUID,
    request: AnalyticsRequest,
    service: AnalyticsRequestServiceDependency,
) -> AnalyticsResponse:
    return await service.run(conversation_id=conversation_id, request=request)


@router.get("/requests/{request_id}", response_model=AnalyticsResponse)
async def get_analytics_request(
    request_id: UUID,
    service: AnalyticsRequestServiceDependency,
) -> AnalyticsResponse:
    response = await service.get(request_id)
    if response is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    return response
