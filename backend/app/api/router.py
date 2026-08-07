from fastapi import APIRouter

from app.api.routes import analytics_requests, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(analytics_requests.router)
