"""定义题目请求、JSON 持久化和公开响应所需的数据模型。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProblemCase(BaseModel):
    """一个样例或测试点；输入输出字段必填，但内容可以为空字符串。"""

    input: str
    output: str

    model_config = ConfigDict(extra="forbid", strict=True)


class ProblemPayload(BaseModel):
    """新增和完整编辑题目时允许客户端提交的字段。"""

    id: str
    title: str
    description: str
    input_description: str
    output_description: str
    samples: list[ProblemCase] = Field(min_length=1)
    constraints: str
    testcases: list[ProblemCase] = Field(min_length=1)
    hint: str = ""
    source: str = ""
    tags: list[str] = Field(default_factory=list)
    time_limit: float | None = Field(default=None, gt=0)
    memory_limit: int | None = Field(default=None, gt=0)
    author: str = ""
    difficulty: str = ""

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator(
        "id",
        "title",
        "description",
        "input_description",
        "output_description",
        "constraints",
        mode="before",
    )
    @classmethod
    def trim_required_text(cls, value: Any) -> Any:
        """去除必填说明首尾空白；清理后为空则交给后续校验拒绝。"""

        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("required text must not be empty")
        return value

    @field_validator("hint", "source", "author", "difficulty", mode="before")
    @classmethod
    def trim_optional_text(cls, value: Any) -> Any:
        """规范化可选说明文字，同时保持缺省值为稳定空字符串。"""

        return value.strip() if isinstance(value, str) else value

    @field_validator("time_limit", mode="before")
    @classmethod
    def validate_time_limit_number(cls, value: Any) -> Any:
        """只接受 JSON 数字作为时间限制，并拒绝 bool 伪装成 0/1。"""

        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("time_limit must be a number")
        return float(value)

    @field_validator("memory_limit", mode="before")
    @classmethod
    def validate_memory_limit_integer(cls, value: Any) -> Any:
        """内存以整数 MB 表示，不接受浮点数、字符串或 bool。"""

        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("memory_limit must be an integer")
        return value


class StoredProblem(ProblemPayload):
    """磁盘中的完整题目，额外保存仅由 Step 5 管理的日志可见性。"""

    public_cases: bool = False


def problem_detail_data(problem: StoredProblem) -> dict[str, Any]:
    """生成 Step 1 详情响应，并隐藏内部 ``public_cases`` 字段。"""

    data = problem.model_dump(exclude={"public_cases"}, mode="json")
    # 内部 None 留给 Step 2 表示“继承语言限制”，API 则按文档展示稳定默认值。
    data["time_limit"] = problem.time_limit if problem.time_limit is not None else 3.0
    data["memory_limit"] = (
        problem.memory_limit if problem.memory_limit is not None else 128
    )
    return data
