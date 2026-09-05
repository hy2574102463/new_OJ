"""Step 2 提交与轮询接口，不包含 Step 3 列表和重判能力。"""

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_user
from app.core.exceptions import AppError
from app.models.users import UserRecord
from app.schemas.responses import response_body
from app.schemas.submissions import SubmissionPayload, submission_detail_data

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


@router.post("/")
async def create_submission(
    payload: SubmissionPayload,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """创建持久化 pending 记录并立即返回，不等待用户程序执行。"""

    submission = await request.app.state.submission_service.submit(
        payload, current_user
    )
    return response_body(
        200,
        "success",
        {"submission_id": str(submission.submission_id), "status": "pending"},
    )


@router.get("/{submission_id}")
async def get_submission(
    submission_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """轮询本人或管理员可见的提交汇总，不公开测试点明细。"""

    if not submission_id.isdecimal() or int(submission_id) <= 0:
        raise AppError(400, "invalid submission id")
    submission = await request.app.state.submission_service.get_detail(
        int(submission_id), current_user
    )
    return response_body(200, "success", submission_detail_data(submission))
