"""在隔离目录中编译、运行用户代码并执行资源限制。"""

import asyncio
import math
import os
import resource
import signal
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from app.judge.comparison import outputs_match
from app.judge.models import CaseResult, CaseStatus, CompileInfo, JudgeResult
from app.models.languages import LanguageRecord
from app.schemas.languages import parse_command_template
from app.schemas.problems import ProblemCase


MAX_CAPTURE_BYTES = 1024 * 1024
COMPILE_TIME_LIMIT = 10.0
COMPILE_MEMORY_LIMIT = 512
MAX_WRITTEN_FILE_BYTES = 16 * 1024 * 1024


class JudgeInfrastructureError(Exception):
    """表示工作目录、可执行程序或监控机制无法正常工作。"""


@dataclass(frozen=True)
class _ProcessResult:
    """保存一次受控子进程的内部结果。"""

    return_code: int
    stdout: str
    stderr: str
    time_seconds: float
    memory_mb: float
    timed_out: bool = False
    memory_exceeded: bool = False
    output_exceeded: bool = False


async def _read_limited(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
    """读取有限输出；达到上限后继续排空管道但不继续占用内存。"""

    captured = bytearray()
    exceeded = False
    while chunk := await stream.read(65536):
        remaining = MAX_CAPTURE_BYTES - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            exceeded = True
    return bytes(captured), exceeded


def _process_tree_rss(pid: int) -> int:
    """同步读取主进程及其子进程 RSS；调用方将其移到工作线程。"""

    try:
        process = psutil.Process(pid)
        processes = [process, *process.children(recursive=True)]
        return sum(item.memory_info().rss for item in processes if item.is_running())
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0


def _kill_process_group(pid: int) -> None:
    """终止用户进程组，确保派生子进程不能在评测结束后继续运行。"""

    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _set_resource_ceiling(pid: int, time_limit: float, memory_limit: int) -> None:
    """设置灾难性资源上限；精确 TLE/MLE 仍由异步监控先行判定。"""

    # 给解释器和动态链接器保留虚拟地址空间余量，避免硬上限抢先变成 RE。
    address_space = max(memory_limit * 2, memory_limit + 64) * 1024 * 1024
    cpu_seconds = max(1, math.ceil(time_limit) + 1)
    try:
        resource.prlimit(pid, resource.RLIMIT_AS, (address_space, address_space))
        resource.prlimit(pid, resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.prlimit(
            pid,
            resource.RLIMIT_FSIZE,
            (MAX_WRITTEN_FILE_BYTES, MAX_WRITTEN_FILE_BYTES),
        )
        resource.prlimit(pid, resource.RLIMIT_NOFILE, (64, 64))
    except ProcessLookupError:
        # 极短程序可能在设置限制前已经正常结束，无需把它误判为系统错误。
        pass
    except (OSError, ValueError) as exc:
        raise JudgeInfrastructureError("cannot apply resource limits") from exc


async def _feed_input(writer: asyncio.StreamWriter | None, data: bytes) -> None:
    """异步写入测试输入；进程提前退出时忽略断管错误。"""

    if writer is None:
        return
    try:
        writer.write(data)
        await writer.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        writer.close()


async def _run_process(
    command: list[str], cwd: Path, input_text: str, time_limit: float, memory_limit: int
) -> _ProcessResult:
    """运行一个无 shell 子进程，并在任一资源越界时清理整个进程组。"""

    started = time.perf_counter()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise JudgeInfrastructureError("cannot start configured command") from exc

    # 父进程设置 prlimit，避免在多线程 Python 服务中使用不安全的 preexec_fn。
    try:
        await asyncio.to_thread(
            _set_resource_ceiling, process.pid, time_limit, memory_limit
        )
    except BaseException:
        await asyncio.to_thread(_kill_process_group, process.pid)
        await process.wait()
        raise
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(_read_limited(process.stdout))
    stderr_task = asyncio.create_task(_read_limited(process.stderr))
    input_task = asyncio.create_task(_feed_input(process.stdin, input_text.encode()))
    peak_bytes = 0
    memory_exceeded = False
    timed_out = False
    deadline = started + time_limit

    try:
        while process.returncode is None:
            peak_bytes = max(
                peak_bytes, await asyncio.to_thread(_process_tree_rss, process.pid)
            )
            if peak_bytes > memory_limit * 1024 * 1024:
                memory_exceeded = True
                await asyncio.to_thread(_kill_process_group, process.pid)
                break
            if time.perf_counter() >= deadline:
                timed_out = True
                await asyncio.to_thread(_kill_process_group, process.pid)
                break
            await asyncio.sleep(0.01)
        await process.wait()
        await input_task
        stdout_data, stdout_exceeded = await stdout_task
        stderr_data, stderr_exceeded = await stderr_task
    except asyncio.CancelledError:
        # 应用关闭或任务取消时，先清理用户进程再传播取消信号。
        await asyncio.to_thread(_kill_process_group, process.pid)
        await process.wait()
        for task in (input_task, stdout_task, stderr_task):
            task.cancel()
        await asyncio.gather(input_task, stdout_task, stderr_task, return_exceptions=True)
        raise

    return _ProcessResult(
        return_code=process.returncode or 0,
        stdout=stdout_data.decode("utf-8", errors="replace"),
        stderr=stderr_data.decode("utf-8", errors="replace"),
        time_seconds=time.perf_counter() - started,
        memory_mb=peak_bytes / (1024 * 1024),
        timed_out=timed_out,
        memory_exceeded=memory_exceeded,
        output_exceeded=stdout_exceeded or stderr_exceeded,
    )


def _expand_command(template: str, source: Path, executable: Path) -> list[str]:
    """把已校验模板展开成参数数组，路径作为单个参数而非 shell 文本。"""

    arguments, _ = parse_command_template(template)
    return [argument.format(src=str(source), exe=str(executable)) for argument in arguments]


def _safe_message(message: str, workspace: Path) -> str:
    """截断工具输出并移除评测工作目录绝对路径。"""

    return message.replace(str(workspace), "<workspace>")[:4096]


class JudgeRunner:
    """依次编译和运行全部测试点，返回与 HTTP 无关的结构化结果。"""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    async def judge(
        self,
        code: str,
        language: LanguageRecord,
        testcases: list[ProblemCase],
        time_limit: float,
        memory_limit: int,
    ) -> JudgeResult:
        """评测源码；工作区或命令启动失败以基础设施异常向上传播。"""

        await asyncio.to_thread(self.workspace_root.mkdir, parents=True, exist_ok=True)
        raw = await asyncio.to_thread(
            tempfile.mkdtemp, dir=self.workspace_root, prefix="submission-"
        )
        workspace = Path(raw).resolve()
        try:
            return await self._judge_in_workspace(
                workspace, code, language, testcases, time_limit, memory_limit
            )
        finally:
            # 用户程序可能创建多层文件，递归清理由工作线程完成。
            await asyncio.to_thread(shutil.rmtree, workspace, ignore_errors=True)

    async def _judge_in_workspace(
        self,
        workspace: Path,
        code: str,
        language: LanguageRecord,
        testcases: list[ProblemCase],
        time_limit: float,
        memory_limit: int,
    ) -> JudgeResult:
        """在已经创建的单次目录中执行编译与逐点运行。"""

        source = workspace / f"main{language.file_ext}"
        executable = workspace / "program"
        await asyncio.to_thread(source.write_text, code, encoding="utf-8")

        compile_info: CompileInfo | None = None
        if language.compile_cmd is not None:
            compile_result = await _run_process(
                _expand_command(language.compile_cmd, source, executable),
                workspace,
                "",
                COMPILE_TIME_LIMIT,
                COMPILE_MEMORY_LIMIT,
            )
            if compile_result.return_code != 0 or compile_result.output_exceeded:
                message = _safe_message(compile_result.stderr, workspace)
                compile_info = CompileInfo("error", message)
                cases = tuple(
                    CaseResult(index, CaseStatus.CE, 0.0, 0.0)
                    for index, _ in enumerate(testcases, start=1)
                )
                return JudgeResult(
                    0, len(testcases) * 10, compile_info, None, None, cases
                )
            compile_info = CompileInfo("success", "")

        results: list[CaseResult] = []
        run_command = _expand_command(language.run_cmd, source, executable)
        for index, testcase in enumerate(testcases, start=1):
            process_result = await _run_process(
                run_command, workspace, testcase.input, time_limit, memory_limit
            )
            if process_result.memory_exceeded:
                status = CaseStatus.MLE
            elif process_result.timed_out:
                status = CaseStatus.TLE
            elif process_result.output_exceeded:
                status = CaseStatus.RE
            elif process_result.return_code != 0:
                status = CaseStatus.RE
            elif outputs_match(process_result.stdout, testcase.output):
                status = CaseStatus.AC
            else:
                status = CaseStatus.WA
            results.append(
                CaseResult(
                    index, status, process_result.time_seconds, process_result.memory_mb
                )
            )

        score = sum(result.result is CaseStatus.AC for result in results) * 10
        return JudgeResult(
            score=score,
            counts=len(results) * 10,
            compile_info=compile_info,
            run_result="finished",
            run_message=f"{len(results)} test cases finished",
            cases=tuple(results),
        )
