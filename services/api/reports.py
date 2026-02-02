from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.models import ProblemReport

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportRequest(BaseModel):
    category: str = Field(..., pattern="^(bug|feature|feedback)$")
    description: str = Field(..., min_length=10, max_length=2000)
    page: str | None = None
    username: str | None = None


class ReportResponse(BaseModel):
    id: str
    message: str


@router.post("", response_model=ReportResponse)
async def submit_report(request: ReportRequest, db: Session = Depends(get_db)):
    """Submit a problem report or feature request."""
    report = ProblemReport(
        category=request.category,
        description=request.description,
        page=request.page,
        username=request.username,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return ReportResponse(id=report.id, message="Report submitted successfully")
