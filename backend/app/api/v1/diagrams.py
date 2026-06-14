from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.diagram import (
    DiagramAssetResponse,
    DiagramGenerateResponse,
    DiagramRegenerateRequest,
    DiagramStatusResponse,
)
from app.services.diagram_service import (
    get_course_diagram_status,
    get_video_diagrams,
    queue_course_diagrams,
    skip_diagram,
    update_diagram_for_regeneration,
)
from app.tasks.diagram_tasks import process_diagram_task

router = APIRouter(tags=["diagrams"])


@router.post(
    "/courses/{course_id}/diagrams/generate",
    response_model=DiagramGenerateResponse,
)
async def generate_course_diagrams(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DiagramGenerateResponse:
    response, diagram_ids = await queue_course_diagrams(
        db,
        current_user.id,
        course_id,
    )
    for diagram_id in diagram_ids:
        process_diagram_task.apply_async(args=[str(diagram_id)], queue="diagrams")
    return response


@router.get(
    "/courses/{course_id}/diagrams/status",
    response_model=DiagramStatusResponse,
)
async def course_diagram_status(
    course_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DiagramStatusResponse:
    return await get_course_diagram_status(db, current_user.id, course_id)


@router.get(
    "/videos/{video_id}/diagrams",
    response_model=list[DiagramAssetResponse],
)
async def video_diagrams(
    video_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DiagramAssetResponse]:
    return await get_video_diagrams(db, current_user.id, video_id)


@router.post(
    "/diagrams/{diagram_id}/regenerate",
    response_model=DiagramAssetResponse,
)
async def regenerate_diagram(
    diagram_id: UUID,
    payload: DiagramRegenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DiagramAssetResponse:
    asset = await update_diagram_for_regeneration(
        db,
        current_user.id,
        diagram_id,
        prompt=payload.prompt,
        mode=payload.mode,
    )
    process_diagram_task.apply_async(args=[str(asset.id)], queue="diagrams")
    return DiagramAssetResponse.model_validate(asset)


@router.delete("/diagrams/{diagram_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_diagram(
    diagram_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await skip_diagram(db, current_user.id, diagram_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
