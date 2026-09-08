"""AI 智能命题 HTTP 接口，仅负责鉴权、参数和统一响应编排。"""

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_user
from app.models.users import UserRecord
from app.schemas.ai import AIRoundCreate, AITaskCreate
from app.schemas.responses import response_body


router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/model-config")
async def get_model_config(
    request: Request, _current_user: UserRecord = Depends(get_current_user)
) -> dict[str, object]:
    """向登录用户返回部署配置摘要，但永不返回模型密钥。"""

    return response_body(200, "success", request.app.state.ai_service.config())


@router.post("/problem-tasks/")
async def create_problem_task(
    payload: AITaskCreate,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """创建首轮 pending 任务，让 HTTP 请求无需等待模型完成。"""

    task = await request.app.state.ai_service.create(payload, current_user)
    return response_body(
        200, "task created", {"task_id": task.task_id, "status": task.status.value}
    )


@router.get("/problem-tasks/{task_id}")
async def get_problem_task(
    task_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """由任务创建者或管理员轮询完整状态、结果和累计用量。"""

    data = await request.app.state.ai_service.get(task_id, current_user)
    return response_body(200, "success", data)


@router.post("/problem-tasks/{task_id}/rounds")
async def continue_problem_task(
    task_id: str,
    payload: AIRoundCreate,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """为已完成任务追加修改要求并启动下一轮。"""

    task = await request.app.state.ai_service.continue_task(
        task_id, payload, current_user
    )
    return response_body(
        200, "task continued", {"task_id": task.task_id, "status": task.status.value,
                                "round": task.current_round}
    )


@router.put("/problem-tasks/{task_id}/cancel")
async def cancel_problem_task(
    task_id: str,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """真实取消活动后台任务；已结束任务由 Service 返回 409。"""

    task = await request.app.state.ai_service.cancel(task_id, current_user)
    return response_body(
        200, "task cancelled", {"task_id": task.task_id, "status": task.status.value}
    )
