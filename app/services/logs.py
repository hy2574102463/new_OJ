"""实现测试点日志可见性、访问审计以及管理员查询规则。"""

from datetime import datetime, timezone

from app.core.exceptions import AppError
from app.models.logs import LogAccessAudit
from app.models.submissions import CaseResultRecord, SubmissionRecord
from app.models.users import UserRecord, UserRole
from app.repositories.logs import LogRepository
from app.repositories.problems import ProblemRepository
from app.repositories.submissions import SubmissionRepository


class LogService:
    """协调提交、题目策略与审计存储，不依赖 FastAPI 请求对象。"""

    def __init__(
        self,
        submissions: SubmissionRepository,
        problems: ProblemRepository,
        audits: LogRepository,
    ) -> None:
        """注入三个数据来源，使权限判断可以独立测试和复用。"""

        self.submissions = submissions
        self.problems = problems
        self.audits = audits

    async def get_submission_log(
        self, submission_id: int, current_user: UserRecord
    ) -> tuple[SubmissionRecord, list[CaseResultRecord]]:
        """返回授权日志；既有资源的成功和拒绝访问均写入审计。"""

        submission = await self.submissions.get(submission_id)
        if submission is None:
            # 不存在的 ID 没有可靠题目归属，按契约返回 404 且不制造审计。
            raise AppError(404, "submission not found")
        problem = await self.problems.get(submission.problem_id)
        if problem is None:
            raise AppError(404, "problem not found")

        allowed = (
            current_user.role is UserRole.ADMIN
            or submission.user_id == current_user.user_id
            or problem.public_cases
        )
        if not allowed:
            # 权限结论已经确定，先持久化 403 再交给异常处理器返回。
            await self.audits.create_access_audit(
                current_user.user_id,
                submission.problem_id,
                datetime.now(timezone.utc),
                "403",
            )
            raise AppError(403, "permission denied")

        details = await self.submissions.get_case_results(submission_id)
        # 只有明细成功读取后才记录 200，避免内部读取失败被误记为成功访问。
        await self.audits.create_access_audit(
            current_user.user_id,
            submission.problem_id,
            datetime.now(timezone.utc),
            "200",
        )
        return submission, details

    async def list_access_audits(
        self,
        user_id: str | None,
        problem_id: str | None,
        page: str | None,
        page_size: str | None,
    ) -> list[LogAccessAudit]:
        """严格解析可选筛选和分页；两个一级筛选均缺省时查询全部。"""

        parsed_user_id = self._parse_positive_query("user_id", user_id)
        normalized_problem_id = problem_id.strip() if problem_id is not None else None
        if normalized_problem_id == "":
            raise AppError(400, "problem_id must not be empty")
        parsed_page = self._parse_positive_query("page", page)
        parsed_page_size = self._parse_positive_query("page_size", page_size)
        if parsed_page is not None and parsed_page_size is None:
            raise AppError(400, "page_size is required when page is provided")
        return await self.audits.list_access_audits(
            parsed_user_id,
            normalized_problem_id,
            parsed_page,
            parsed_page_size,
        )

    @staticmethod
    def _parse_positive_query(name: str, value: str | None) -> int | None:
        """把十进制正整数字符串转成 int，拒绝符号、小数和空值。"""

        if value is None:
            return None
        if not value.isdecimal() or int(value) <= 0:
            raise AppError(400, f"invalid {name}")
        return int(value)
