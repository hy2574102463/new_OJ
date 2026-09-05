"""验证输出契约以及 Python/C++ 评测器的核心状态。"""

from pathlib import Path

import pytest

from app.judge.comparison import normalize_output, outputs_match
from app.judge.models import CaseStatus
from app.judge.runner import JudgeRunner
from app.models.languages import LanguageRecord
from app.schemas.problems import ProblemCase


PYTHON = LanguageRecord("python", "python", ".py", None, "python3 {src}", 1.0, 128, None)
CPP = LanguageRecord(
    "cpp", "cpp", ".cpp", "g++ {src} -std=c++14 -o {exe}", "{exe}", 1.0, 128, None
)


def test_extra_prompt_is_not_ignored() -> None:
    """额外提示语属于真实输出内容，必须判为不匹配。"""

    assert not outputs_match("answer: 3\n", "3\n")


def test_trailing_spaces_are_ignored() -> None:
    """每一行末尾的空格和制表符不影响结果。"""

    assert outputs_match("1  \n2\t", "1\n2")


def test_final_newlines_are_ignored() -> None:
    """输出末尾任意多个换行不影响结果。"""

    assert normalize_output("1\n\n\n") == "1"


def test_leading_spaces_and_internal_blank_lines_are_preserved() -> None:
    """行首空格与内部空行承载格式，不能被标准化删除。"""

    assert not outputs_match("  1\n\n2", "1\n2")


@pytest.mark.asyncio
async def test_python_runner_reports_ac_wa_and_re(tmp_path: Path) -> None:
    """解释型程序按退出码和严格输出分别产生 AC、WA、RE。"""

    runner = JudgeRunner(tmp_path / "judge")
    cases = [ProblemCase(input="1 2", output="3")]
    ac = await runner.judge(
        "a,b=map(int,input().split());print(a+b)", PYTHON, cases, 1.0, 128
    )
    wa = await runner.judge("print('prompt')", PYTHON, cases, 1.0, 128)
    runtime_error = await runner.judge("raise RuntimeError()", PYTHON, cases, 1.0, 128)
    assert ac.cases[0].result is CaseStatus.AC
    assert wa.cases[0].result is CaseStatus.WA
    assert runtime_error.cases[0].result is CaseStatus.RE


@pytest.mark.asyncio
async def test_runner_enforces_time_and_memory(tmp_path: Path) -> None:
    """无限循环与逐步分配的大数组分别触发 TLE 和 MLE。"""

    runner = JudgeRunner(tmp_path / "judge")
    case = [ProblemCase(input="", output="")]
    tle = await runner.judge("while True: pass", PYTHON, case, 0.1, 128)
    mle = await runner.judge(
        "import time\na=[]\nwhile True:\n a.append(bytearray(1024*1024)); time.sleep(.01)",
        PYTHON,
        case,
        3.0,
        32,
    )
    assert tle.cases[0].result is CaseStatus.TLE
    assert mle.cases[0].result is CaseStatus.MLE
    assert list((tmp_path / "judge").iterdir()) == []


@pytest.mark.asyncio
async def test_cpp_runner_compiles_and_reports_ce(tmp_path: Path) -> None:
    """C++ 正确代码先编译后 AC，语法错误则为所有测试点生成 CE。"""

    runner = JudgeRunner(tmp_path / "judge")
    cases = [ProblemCase(input="1 2", output="3")]
    ac = await runner.judge(
        "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}",
        CPP,
        cases,
        1.0,
        128,
    )
    ce = await runner.judge("int main( {", CPP, cases, 1.0, 128)
    assert ac.compile_info is not None and ac.compile_info.result == "success"
    assert ac.cases[0].result is CaseStatus.AC
    assert ce.compile_info is not None and ce.compile_info.result == "error"
    assert ce.cases[0].result is CaseStatus.CE
    assert str(tmp_path) not in ce.compile_info.message
