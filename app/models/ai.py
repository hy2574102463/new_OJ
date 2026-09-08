"""定义 AI 命题任务的状态和跨 Service/Repository 传递的记录。"""

from dataclasses import dataclass
from enum import Enum


class AITaskStatus(str, Enum):
    """AI 后台任务可观察的完整生命周期。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AIRoundMode(str, Enum):
    """说明本轮是在修改当前题，还是基于上下文另出新题。"""

    REVISE = "revise"
    NEW = "new"


@dataclass(frozen=True)
class AITaskRecord:
    """保存一个多轮命题会话的最新状态和累计用量。"""

    task_id: str
    user_id: int
    problem_id: str | None
    status: AITaskStatus
    progress_percent: int
    progress_message: str
    result_json: str | None
    error_info: str | None
    input_tokens: int
    output_tokens: int
    cost: str
    usage_source: str
    current_round: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AIRoundRecord:
    """保存一轮用户要求、生成结果、状态与独立用量。"""

    task_id: str
    round_index: int
    requirement: str
    mode: AIRoundMode
    status: AITaskStatus
    progress_percent: int
    progress_message: str
    result_json: str | None
    input_tokens: int
    output_tokens: int
    cost: str
    usage_source: str
    created_at: str
    finished_at: str | None
