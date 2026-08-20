from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.databases.models import SchemaSnapshot, SQLDialect
from app.infrastructure.catalogs.models import AnalyticsCatalog


class ConnectionProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dialect: SQLDialect
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database_name: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=255)
    credential_reference: str = Field(min_length=1, max_length=512)


class ConnectionTestResult(BaseModel):
    status: str
    message: str
    code: str | None = None


class ConnectionProfileView(BaseModel):
    id: UUID
    name: str
    dialect: SQLDialect
    host: str
    port: int
    database_name: str
    username: str
    credential_reference: str
    state: str
    created_at: datetime | None = None


class SetupProgress(BaseModel):
    profile: ConnectionProfileView
    schema_state: str
    catalog_state: str
    conversation_id: UUID | None = None


class SchemaRefreshResult(BaseModel):
    profile_id: UUID
    schema_snapshot_id: UUID
    schema_changed: bool
    state: str


class CatalogValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class CatalogStatus(BaseModel):
    catalog: AnalyticsCatalog | None = None
    schema_snapshot: SchemaSnapshot
    state: str
    content_hash: str | None = None
