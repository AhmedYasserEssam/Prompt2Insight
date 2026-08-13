from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_connection_setup_service
from app.application.databases.setup_connection import ConnectionSetupService
from app.domain.databases.connection_profiles import (
    ConnectionProfileInput,
    ConnectionProfileView,
    ConnectionTestResult,
    SetupProgress,
)

router = APIRouter(prefix="/connection-profiles", tags=["connections"])
ConnectionSetupDependency = Annotated[ConnectionSetupService, Depends(get_connection_setup_service)]


@router.get("", response_model=list[ConnectionProfileView])
async def list_profiles(service: ConnectionSetupDependency) -> list[ConnectionProfileView]:
    return await service.list()


@router.post("/test", response_model=ConnectionTestResult)
async def test_profile(
    input: ConnectionProfileInput, service: ConnectionSetupDependency
) -> ConnectionTestResult:
    return await service.test(input)


@router.post("/setup", response_model=SetupProgress)
async def setup_profile(
    input: ConnectionProfileInput, service: ConnectionSetupDependency
) -> SetupProgress:
    return await service.save_and_introspect(input)


@router.post("/{profile_id}/select", response_model=SetupProgress)
async def select_profile(profile_id: UUID, service: ConnectionSetupDependency) -> SetupProgress:
    return await service.select(profile_id)
