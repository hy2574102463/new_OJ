"""定义语言配置在服务层和 SQLite 仓库之间传递的领域对象。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageRecord:
    """保存一种语言的文件格式、命令模板和默认资源限制。"""

    name: str
    name_key: str
    file_ext: str
    compile_cmd: str | None
    run_cmd: str
    time_limit: float
    memory_limit: int
    created_by: int | None
