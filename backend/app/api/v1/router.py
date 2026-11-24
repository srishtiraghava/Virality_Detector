from fastapi import APIRouter

from app.api.v1.endpoints import auth, health, jobs, videos

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(videos.router, prefix="/videos", tags=["videos"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["analysis jobs"])
