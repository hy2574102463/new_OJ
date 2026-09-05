"""Step 1 题目 HTTP 接口，只负责鉴权、参数接收和响应编排。"""

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_user, require_admin
from app.models.users import UserRecord
from app.schemas.problems import ProblemPayload, problem_detail_data
from app.schemas.responses import response_body

router = APIRouter(prefix="/api/problems", tags=["problems"])


@router.get("/")
async def list_problems(
    request: Request,
    _current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """向登录用户返回题目 ID 和标题摘要，不加载额外公开字段。"""

    problems = await request.app.state.problem_service.list_problems()
    summaries = [{"id": problem.id, "title": problem.title} for problem in problems]
    return response_body(200, "success", summaries)


@router.post("/")
async def create_problem(
    payload: ProblemPayload,
    request: Request,
    _current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """由任意登录用户新增完整题目配置。"""

    problem = await request.app.state.problem_service.create_problem(payload)
    return response_body(200, "add success", {"id": problem.id})


@router.get("/{problem_id:path}")
async def get_problem(
    problem_id: str,
    request: Request,
    _current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """向登录用户返回完整题目配置和稳定的可选字段默认值。"""

    problem = await request.app.state.problem_service.get_problem(problem_id)
    return response_body(200, "success", problem_detail_data(problem))


@router.put("/{problem_id:path}")
async def update_problem(
    problem_id: str,
    payload: ProblemPayload,
    request: Request,
    _current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """由登录用户完整替换题目公开字段，路径 ID 必须与正文一致。"""

    problem = await request.app.state.problem_service.update_problem(problem_id, payload)
    return response_body(200, "update success", {"id": problem.id})


@router.delete("/{problem_id:path}")
async def delete_problem(
    problem_id: str,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> dict[str, object]:
    """只允许管理员删除题目；普通用户在查找题目前即返回 403。"""

    deleted_id = await request.app.state.problem_service.delete_problem(problem_id)
    return response_body(200, "delete success", {"id": deleted_id})
