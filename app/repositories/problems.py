"""使用每题一个 JSON 文件持久化题目，并隔离阻塞磁盘操作。"""

import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.problems import ProblemPayload, StoredProblem


MANAGED_FILENAME = re.compile(r"^[0-9a-f]{64}\.json$")


class ProblemStorageError(Exception):
    """表示题目目录、JSON 内容或原子文件操作发生内部错误。"""


class ProblemRepository:
    """提供异步题目 CRUD；HTTP 状态和权限由 Service/Router 负责。"""

    def __init__(self, directory: Path) -> None:
        """保存题目目录，并创建单进程内串行化文件变更的异步锁。"""

        self.directory = directory
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """创建题目目录并校验全部已有 JSON，损坏时阻止应用启动。"""

        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    async def list_all(self) -> list[StoredProblem]:
        """加载全部题目并按区分大小写的 ID 稳定排序。"""

        async with self._lock:
            problems = await asyncio.to_thread(self._load_all_sync)
        return sorted(problems, key=lambda problem: problem.id)

    async def get(self, problem_id: str) -> StoredProblem | None:
        """按题目 ID 读取并校验 JSON，不存在时返回 None。"""

        path = self._path_for_id(problem_id)
        async with self._lock:
            return await asyncio.to_thread(self._read_if_exists_sync, path, problem_id)

    async def create(self, problem: StoredProblem) -> bool:
        """仅当 ID 尚不存在时原子创建题目，返回是否成功创建。"""

        path = self._path_for_id(problem.id)
        async with self._lock:
            if await asyncio.to_thread(path.exists):
                return False
            await asyncio.to_thread(self._write_sync, path, problem)
            return True

    async def update(self, problem: StoredProblem) -> bool:
        """仅当题目存在时原子替换完整配置，返回是否找到原题。"""

        path = self._path_for_id(problem.id)
        async with self._lock:
            if not await asyncio.to_thread(path.exists):
                return False
            await asyncio.to_thread(self._write_sync, path, problem)
            return True

    async def update_payload(self, payload: ProblemPayload) -> bool:
        """原子替换公开字段，同时保留由其他步骤管理的内部字段。"""

        path = self._path_for_id(payload.id)
        async with self._lock:
            existing = await asyncio.to_thread(
                self._read_if_exists_sync, path, payload.id
            )
            if existing is None:
                return False
            updated = StoredProblem.model_validate(
                {**payload.model_dump(mode="python"), "public_cases": existing.public_cases}
            )
            await asyncio.to_thread(self._write_sync, path, updated)
            return True

    async def delete(self, problem_id: str) -> bool:
        """删除指定题目文件；不存在返回 False，其他文件错误向上传播。"""

        path = self._path_for_id(problem_id)
        async with self._lock:
            try:
                await asyncio.to_thread(path.unlink)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise ProblemStorageError("cannot delete problem") from exc
            return True

    async def reset(self) -> None:
        """只删除符合受管理哈希命名规则的 JSON，不碰目录中其他文件。"""

        async with self._lock:
            await asyncio.to_thread(self._reset_sync)

    def _initialize_sync(self) -> None:
        """同步创建目录并扫描配置；由异步入口放入工作线程。"""

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._load_all_sync()
        except ProblemStorageError:
            raise
        except OSError as exc:
            raise ProblemStorageError("cannot initialize problem storage") from exc

    def _load_all_sync(self) -> list[StoredProblem]:
        """同步读取目录内所有 JSON，并校验文件名与内容 ID 一致。"""

        problems: list[StoredProblem] = []
        seen_ids: set[str] = set()
        try:
            paths = list(self.directory.glob("*.json"))
        except OSError as exc:
            raise ProblemStorageError("cannot list problems") from exc

        for path in paths:
            problem = self._read_sync(path)
            if path.name != self._filename_for_id(problem.id):
                raise ProblemStorageError("problem filename does not match its id")
            if problem.id in seen_ids:
                raise ProblemStorageError("duplicate problem id in storage")
            seen_ids.add(problem.id)
            problems.append(problem)
        return problems

    def _read_if_exists_sync(
        self, path: Path, expected_id: str
    ) -> StoredProblem | None:
        """同步读取单题并确认文件内容没有被替换成另一个 ID。"""

        try:
            problem = self._read_sync(path)
        except FileNotFoundError:
            return None
        if problem.id != expected_id:
            raise ProblemStorageError("problem id does not match requested id")
        return problem

    @staticmethod
    def _read_sync(path: Path) -> StoredProblem:
        """解析一个 JSON 文件；拒绝符号链接，防止读取题目目录外文件。"""

        try:
            if path.is_symlink():
                raise ProblemStorageError("problem file must not be a symbolic link")
            with path.open("r", encoding="utf-8") as file:
                raw: Any = json.load(file)
            return StoredProblem.model_validate(raw)
        except FileNotFoundError:
            raise
        except ProblemStorageError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise ProblemStorageError("invalid problem file") from exc

    def _write_sync(self, path: Path, problem: StoredProblem) -> None:
        """先完整写临时文件，再原子替换目标；失败时清理临时文件。"""

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=".problem-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    problem.model_dump(mode="json"),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
        except (OSError, TypeError, ValueError) as exc:
            raise ProblemStorageError("cannot write problem") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError:
                    # 原始写入异常更有定位价值；遗留文件下次不会被当作题目读取。
                    pass

    def _reset_sync(self) -> None:
        """同步删除受管理文件；任一删除失败即报告存储错误。"""

        try:
            for path in self.directory.glob("*.json"):
                if MANAGED_FILENAME.fullmatch(path.name):
                    path.unlink()
        except OSError as exc:
            raise ProblemStorageError("cannot reset problem storage") from exc

    def _path_for_id(self, problem_id: str) -> Path:
        """把任意题目 ID 映射到固定目录中的安全文件路径。"""

        return self.directory / self._filename_for_id(problem_id)

    @staticmethod
    def _filename_for_id(problem_id: str) -> str:
        """使用完整 SHA-256 十六进制摘要生成不可穿越目录的文件名。"""

        digest = hashlib.sha256(problem_id.encode("utf-8")).hexdigest()
        return f"{digest}.json"
