from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import get_connection_setup_service
from app.domain.databases.connection_profiles import SchemaRefreshResult
from app.main import app


class RefreshServiceStub:
    async def refresh_schema(self, profile_id):
        return SchemaRefreshResult(
            profile_id=profile_id,
            schema_snapshot_id=uuid4(),
            schema_changed=True,
            state="stale",
        )


def test_refresh_response_does_not_expose_credentials() -> None:
    profile_id = uuid4()
    app.dependency_overrides[get_connection_setup_service] = lambda: RefreshServiceStub()
    try:
        response = TestClient(app).post(f"/api/v1/connection-profiles/{profile_id}/refresh-schema")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["profile_id"] == str(profile_id)
    serialized = response.text.lower()
    assert "password" not in serialized
    assert "database_url" not in serialized
    assert "postgresql://" not in serialized
    assert "secret" not in serialized
