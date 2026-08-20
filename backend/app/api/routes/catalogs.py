from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_catalog_configuration_service
from app.application.databases.configure_catalog import CatalogConfigurationService
from app.domain.databases.connection_profiles import CatalogStatus, CatalogValidationResult
from app.infrastructure.catalogs.models import AnalyticsCatalog

router = APIRouter(prefix="/connection-profiles/{profile_id}/catalog", tags=["catalogs"])
CatalogConfigurationDependency = Annotated[
    CatalogConfigurationService, Depends(get_catalog_configuration_service)
]


@router.get("", response_model=CatalogStatus)
async def get_catalog_status(
    profile_id: UUID, service: CatalogConfigurationDependency
) -> CatalogStatus:
    return await service.status(profile_id)


@router.post("/validate", response_model=CatalogValidationResult)
async def validate_catalog(
    profile_id: UUID, catalog: AnalyticsCatalog, service: CatalogConfigurationDependency
) -> CatalogValidationResult:
    return await service.validate(profile_id, catalog)


@router.post("/publish", response_model=CatalogStatus)
async def publish_catalog(
    profile_id: UUID, catalog: AnalyticsCatalog, service: CatalogConfigurationDependency
) -> CatalogStatus:
    return await service.publish(profile_id, catalog)
