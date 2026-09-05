"""把 Streamlit 表单值转换为后端 payload；不发送请求或保存页面状态。"""

from collections.abc import Mapping, Sequence
from typing import Any


def clean_cases(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """保留具有字符串输入输出的行，并丢弃数据编辑器产生的全空占位行。"""

    cases: list[dict[str, str]] = []
    for row in rows:
        input_value = row.get("input", "")
        output_value = row.get("output", "")
        if not isinstance(input_value, str) or not isinstance(output_value, str):
            continue
        if input_value == "" and output_value == "" and bool(row.get("_placeholder")):
            continue
        cases.append({"input": input_value, "output": output_value})
    return cases


def build_problem_payload(
    values: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    testcases: Sequence[Mapping[str, Any]],
    *,
    inherit_time_limit: bool,
    inherit_memory_limit: bool,
) -> dict[str, Any]:
    """构造题目完整替换请求，显式用 ``None`` 表达继承语言默认限制。"""

    tags_value = values.get("tags", "")
    tags = (
        [tag.strip() for tag in tags_value.split(",") if tag.strip()]
        if isinstance(tags_value, str)
        else list(tags_value or [])
    )
    return {
        "id": str(values.get("id", "")).strip(),
        "title": str(values.get("title", "")).strip(),
        "description": str(values.get("description", "")).strip(),
        "input_description": str(values.get("input_description", "")).strip(),
        "output_description": str(values.get("output_description", "")).strip(),
        "samples": clean_cases(samples),
        "constraints": str(values.get("constraints", "")).strip(),
        "testcases": clean_cases(testcases),
        "hint": str(values.get("hint", "")).strip(),
        "source": str(values.get("source", "")).strip(),
        "tags": tags,
        "time_limit": None if inherit_time_limit else float(values["time_limit"]),
        "memory_limit": None if inherit_memory_limit else int(values["memory_limit"]),
        "author": str(values.get("author", "")).strip(),
        "difficulty": str(values.get("difficulty", "")).strip(),
    }


def submission_query(
    *,
    user_id: str,
    problem_id: str,
    status: str,
    use_pagination: bool,
    page: int,
    page_size: int,
) -> dict[str, str | int]:
    """只发送用户实际启用的筛选项，并保持分页参数成对出现。"""

    params: dict[str, str | int] = {}
    if user_id.strip():
        params["user_id"] = user_id.strip()
    if problem_id.strip():
        params["problem_id"] = problem_id.strip()
    if status != "全部":
        params["status"] = status
    if use_pagination:
        params.update(page=page, page_size=page_size)
    return params


def should_poll(status: str | None) -> bool:
    """仅 pending 是后台仍可能变化的任务状态。"""

    return status == "pending"
