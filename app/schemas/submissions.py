"""定义 Step 2 提交请求和详情响应的稳定结构。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.submissions import SubmissionRecord


class SubmissionPayload(BaseModel):
    """接收题目、语言和非空源码，限制单请求内存占用。"""

    problem_id: str
    language: str
    code: str = Field(max_length=1024 * 1024)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("problem_id", "language", mode="before")
    @classmethod
    def trim_identifier(cls, value: Any) -> Any:
        """去除标识符两端空白并拒绝空值。"""

        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("identifier must not be empty")
        return value

    @field_validator("code")
    @classmethod
    def reject_empty_code(cls, value: str) -> str:
        """空白也是合法源码字符，但完全空的提交没有可执行内容。"""

        if not value:
            raise ValueError("code must not be empty")
        return value


def submission_detail_data(submission: SubmissionRecord) -> dict[str, object]:
    """将内部提交转换为 API 详情，确保源码永不进入响应。"""

    compile_info = None
    if submission.compile_result is not None:
        compile_info = {
            "result": submission.compile_result,
            "message": submission.compile_message or "",
        }
    run_info = None
    if submission.run_result is not None:
        run_info = {
            "result": submission.run_result,
            "message": submission.run_message or "",
        }
    return {
        "submission_id": str(submission.submission_id),
        "status": submission.status.value,
        "score": submission.score,
        "counts": submission.counts,
        "compile_info": compile_info,
        "run_info": run_info,
        "error_info": submission.error_info,
    }
