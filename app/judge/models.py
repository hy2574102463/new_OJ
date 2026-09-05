"""定义评测器内部结果，避免混淆任务状态和测试点状态。"""

from dataclasses import dataclass
from enum import Enum


class CaseStatus(str, Enum):
    """课程契约允许的单个测试点结果。"""

    AC = "AC"
    WA = "WA"
    TLE = "TLE"
    MLE = "MLE"
    RE = "RE"
    CE = "CE"
    UNK = "UNK"


@dataclass(frozen=True)
class CaseResult:
    """记录一个测试点的状态、墙钟时间与峰值内存。"""

    case_index: int
    result: CaseStatus
    time_seconds: float
    memory_mb: float


@dataclass(frozen=True)
class CompileInfo:
    """记录编译型语言的编译结论和脱敏消息。"""

    result: str
    message: str


@dataclass(frozen=True)
class JudgeResult:
    """汇总一次评测；测试点明细暂不通过 Step 2 API 公开。"""

    score: int
    counts: int
    compile_info: CompileInfo | None
    run_result: str | None
    run_message: str | None
    cases: tuple[CaseResult, ...]
