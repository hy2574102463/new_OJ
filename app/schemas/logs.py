"""将内部测试点和审计记录裁剪成 Step 5 安全响应结构。"""

from app.models.logs import LogAccessAudit
from app.models.submissions import CaseResultRecord, SubmissionRecord


def submission_log_data(
    submission: SubmissionRecord, details: list[CaseResultRecord]
) -> dict[str, object]:
    """只返回判定指标、总分和总测试点分值，不返回运行敏感内容。"""

    return {
        "details": [
            {
                "id": detail.case_index,
                "result": detail.result,
                "time": detail.time_seconds,
                "memory": detail.memory_mb,
            }
            for detail in details
        ],
        "score": submission.score,
        "counts": submission.counts,
    }


def access_audit_data(audit: LogAccessAudit) -> dict[str, str]:
    """生成 API 文档规定的五个审计字段，并隐藏内部自增 ID。"""

    return {
        "user_id": str(audit.user_id),
        "problem_id": audit.problem_id,
        "action": audit.action,
        "time": audit.accessed_at,
        "status": audit.status,
    }
