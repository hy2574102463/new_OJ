"""校验 AI 命题请求；模型结果仍复用题目模块的 ProblemPayload。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai import AIRoundMode


class AITaskCreate(BaseModel):
    """创建首轮任务所需的命题要求和可选参考题目。"""

    requirement: str = Field(min_length=1, max_length=4000)
    problem_id: str | None = Field(default=None, max_length=200)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("requirement", "problem_id", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        """清理边界空白；空的可选题目 ID 视为未提供。"""

        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class AIRoundCreate(BaseModel):
    """为已完成任务追加一轮明确的修改要求。"""

    requirement: str = Field(min_length=1, max_length=4000)
    mode: AIRoundMode = AIRoundMode.REVISE

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("requirement", mode="before")
    @classmethod
    def trim_requirement(cls, value: Any) -> Any:
        """拒绝只包含空白字符的追问。"""

        return value.strip() if isinstance(value, str) else value

    @field_validator("mode", mode="before")
    @classmethod
    def parse_mode(cls, value: Any) -> Any:
        """HTTP JSON 使用字符串；在严格模型校验前转换为明确枚举。"""

        return AIRoundMode(value) if isinstance(value, str) else value
