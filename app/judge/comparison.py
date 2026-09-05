"""实现 OJ 输出标准化和严格比较，不处理程序执行。"""


def normalize_output(output: str) -> str:
    """删除每行末尾空白和最终多余换行，保留行首及内部空行。"""

    normalized_lines = [line.rstrip() for line in output.split("\n")]
    return "\n".join(normalized_lines).rstrip("\n")


def outputs_match(actual: str, expected: str) -> bool:
    """按课程约定比较用户输出与标准答案。"""

    return normalize_output(actual) == normalize_output(expected)
