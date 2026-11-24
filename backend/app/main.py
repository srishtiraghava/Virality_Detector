import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging_config import configure_logging

logger = logging.getLogger("viralscope")


def create_app() -> FastAPI:
    configure_logging()

    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="A modular foundation for multimodal video intelligence.",
        debug=settings.debug,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix="/api/v1")

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        errors = [
            {key: value for key, value in error.items() if key != "input"}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": errors},
        )

    @application.get("/", tags=["root"])
    def root() -> dict[str, str]:
        return {"application": settings.app_name, "status": "ok"}

    @application.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.error(
            "Unhandled application exception",
            extra={"exception_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected error occurred."},
        )

    return application


app = create_app()
