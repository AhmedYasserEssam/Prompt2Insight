from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import Prompt2InsightError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.exception_handler(Prompt2InsightError)
async def prompt2insight_error_handler(_: Request, error: Prompt2InsightError) -> JSONResponse:
    """Keep expected application failures out of FastAPI's unhandled-500 path."""
    status_code = 503 if error.retryable else 400
    return JSONResponse(
        status_code=status_code,
        content={"code": error.code.value, "message": error.message, "retryable": error.retryable},
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)
