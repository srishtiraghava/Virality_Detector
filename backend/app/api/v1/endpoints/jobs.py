from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.repositories.analysis_jobs import get_analysis_job_for_user
from app.schemas.analysis_job import AnalysisJobResponse

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/{job_id}", response_model=AnalysisJobResponse)
def read_analysis_job(
    job_id: UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> AnalysisJobResponse:
    job = get_analysis_job_for_user(session, current_user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return AnalysisJobResponse.model_validate(job)
