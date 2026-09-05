"""定义提交任务状态及跨服务、仓库边界使用的领域对象。"""

from dataclasses import dataclass
from enum import Enum


class SubmissionStatus(str, Enum):
    """一次后台评测任务仅有的三种生命周期状态。"""

    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True)
class SubmissionRecord:
    """保存提交源码、归属、状态和可公开的汇总结果。"""

    submission_id: int
    user_id: int
    problem_id: str
    language_name: str
    code: str
    status: SubmissionStatus
    score: int | None
    counts: int | None
    compile_result: str | None
    compile_message: str | None
    run_result: str | None
    run_message: str | None
    error_info: str | None
    created_at: str
    finished_at: str | None
