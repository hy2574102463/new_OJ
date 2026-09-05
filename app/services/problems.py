"""实现题目新增、查询、完整编辑和删除的业务状态规则。"""

from app.core.exceptions import AppError
from app.repositories.problems import ProblemRepository
from app.schemas.problems import ProblemPayload, StoredProblem


class ProblemService:
    """协调题目 JSON Repository，并把存储结果转换成 API 业务异常。"""

    def __init__(self, problems: ProblemRepository) -> None:
        """注入题目仓库，使 Service 不依赖 FastAPI Request。"""

        self.problems = problems

    async def list_problems(self) -> list[StoredProblem]:
        """取得按 ID 排序的全部题目，供路由裁剪成摘要。"""

        return await self.problems.list_all()

    async def create_problem(self, payload: ProblemPayload) -> StoredProblem:
        """创建默认不公开测试日志的题目；重复 ID 返回 409。"""

        problem = StoredProblem.model_validate(
            {**payload.model_dump(mode="python"), "public_cases": False}
        )
        if not await self.problems.create(problem):
            raise AppError(409, "problem id already exists")
        return problem

    async def get_problem(self, problem_id: str) -> StoredProblem:
        """按清理后的 ID 查询题目，不存在时返回 404。"""

        problem = await self.problems.get(problem_id.strip())
        if problem is None:
            raise AppError(404, "problem not found")
        return problem

    async def update_problem(
        self, problem_id: str, payload: ProblemPayload
    ) -> StoredProblem:
        """校验路径与正文 ID，并完整替换公开字段、保留内部字段。"""

        normalized_path_id = problem_id.strip()
        if normalized_path_id != payload.id:
            raise AppError(400, "problem id does not match path")
        if not await self.problems.update_payload(payload):
            raise AppError(404, "problem not found")
        return await self.get_problem(payload.id)

    async def delete_problem(self, problem_id: str) -> str:
        """删除清理后的题目 ID，不存在时返回 404。"""

        normalized_id = problem_id.strip()
        if not await self.problems.delete(normalized_id):
            raise AppError(404, "problem not found")
        return normalized_id
