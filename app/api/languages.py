"""Step 2 语言 HTTP 接口，只编排鉴权、输入和统一响应。"""

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_current_user
from app.models.users import UserRecord
from app.schemas.languages import LanguagePayload
from app.schemas.responses import response_body

router = APIRouter(prefix="/api/languages", tags=["languages"])


@router.get("/")
async def list_languages(request: Request) -> dict[str, object]:
    """公开返回按名称排序的语言列表，不公开执行命令。"""

    languages = await request.app.state.language_service.list_languages()
    return response_body(200, "success", {"name": [item.name for item in languages]})


@router.post("/")
async def register_language(
    payload: LanguagePayload,
    request: Request,
    current_user: UserRecord = Depends(get_current_user),
) -> dict[str, object]:
    """允许任意登录用户注册经过严格校验的新语言。"""

    language = await request.app.state.language_service.register(
        payload, current_user.user_id
    )
    return response_body(200, "language registered", {"name": language.name})
