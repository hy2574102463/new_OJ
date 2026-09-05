"""Step 5 审计 HTTP 接口，只处理管理员依赖、查询参数和响应编排。"""

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import require_admin
from app.models.users import UserRecord
from app.schemas.logs import access_audit_data
from app.schemas.responses import response_body

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/access/")
async def list_log_access_audits(
    request: Request,
    user_id: str | None = Query(default=None),
    problem_id: str | None = Query(default=None),
    page: str | None = Query(default=None),
    page_size: str | None = Query(default=None),
    _admin: UserRecord = Depends(require_admin),
) -> dict[str, object]:
    """让管理员按用户、题目和分页查询访问审计；本操作不自审计。"""

    audits = await request.app.state.log_service.list_access_audits(
        user_id, problem_id, page, page_size
    )
    return response_body(200, "success", [access_audit_data(item) for item in audits])
