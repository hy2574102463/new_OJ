"""Step 2/3 提交接口，负责创建、列表、详情轮询和管理员重判。"""

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_current_user, require_admin
from app.core.exceptions import AppError
from app.models.users import UserRecord
from app.schemas.responses import response_body
from app.schemas.submissions import (
    SubmissionPayload,
    submission_detail_data,
    submission_summary_data,
)

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


@router.get("/")
async def list_submissions(
    request: Request,
    user_id: str | None = Query(default=None),
    problem_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: str | None = Query(default=None),
    page_size: str | None = Query(default=None),
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """按用户/题目、任务状态和分页返回当前身份可见的提交摘要。"""

    total, submissions = await request.app.state.submission_service.list_submissions(
        current_user, user_id, problem_id, status, page, page_size
    )
    return response_body(
        200,
        "success",
        {
            "total": total,
            "submissions": [submission_summary_data(item) for item in submissions],
        },
    )


@router.put("/{submission_id}/rejudge")
async def rejudge_submission(
    submission_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> dict[str, object]:
    """由管理员清除旧结果，并用原 ID 和源码重新启动评测。"""

    if not submission_id.isdecimal() or int(submission_id) <= 0:
        raise AppError(400, "invalid submission id")
    submission = await request.app.state.submission_service.rejudge(
        int(submission_id)
    )
    return response_body(
        200,
        "rejudge started",
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
