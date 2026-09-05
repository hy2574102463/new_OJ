"""实现语言注册、默认初始化和列表查询的业务规则。"""

from app.core.exceptions import AppError
from app.models.languages import LanguageRecord
from app.repositories.languages import DuplicateLanguageError, LanguageRepository
from app.schemas.languages import LanguagePayload


class LanguageService:
    """协调语言仓库，并把重复名称转换为公开业务异常。"""

    def __init__(self, languages: LanguageRepository) -> None:
        self.languages = languages

    async def ensure_defaults(self) -> None:
        """保证应用启动和测试 reset 后总有 Python/C++。"""

        await self.languages.ensure_defaults()

    async def register(self, payload: LanguagePayload, user_id: int) -> LanguageRecord:
        """注册登录用户提交且已通过模板校验的新语言。"""

        language = LanguageRecord(
            name=payload.name,
            name_key=payload.name.casefold(),
            file_ext=payload.file_ext,
            compile_cmd=payload.compile_cmd,
            run_cmd=payload.run_cmd,
            time_limit=payload.time_limit,
            memory_limit=payload.memory_limit,
            created_by=user_id,
        )
        try:
            await self.languages.create(language)
        except DuplicateLanguageError as exc:
            raise AppError(409, "language already exists") from exc
        return language

    async def list_languages(self) -> list[LanguageRecord]:
        """返回全部已注册语言。"""

        return await self.languages.list_all()
