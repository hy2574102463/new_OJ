"""定义日志访问审计在 Repository、Service 和响应层之间的对象。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LogAccessAudit:
    """保存一次有效日志访问的身份、题目、动作、时间和结果。"""

    audit_id: int
    user_id: int
    problem_id: str
    action: str
    accessed_at: str
    status: str
