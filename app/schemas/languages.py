"""定义动态语言注册请求的严格校验规则。"""

import re
import shlex
from string import Formatter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


LANGUAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,39}$")
FILE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,10}$")
ALLOWED_FIELDS = {"src", "exe"}


def parse_command_template(template: str) -> tuple[list[str], set[str]]:
    """把命令模板解析成参数并返回占位符；格式错误交给请求校验处理。"""

    try:
        arguments = shlex.split(template, posix=True)
        fields = {
            field_name
            for _, field_name, _, _ in Formatter().parse(template)
            if field_name is not None
        }
    except (ValueError, KeyError) as exc:
        raise ValueError("invalid command template") from exc
    if not arguments or not fields <= ALLOWED_FIELDS:
        raise ValueError("invalid command template")
    # 首个参数只能是 PATH 中的程序名，编译产物则必须使用完整 {exe} 占位符。
    if "/" in arguments[0] and arguments[0] != "{exe}":
        raise ValueError("command executable must not contain a path")
    if any(character in template for character in ("\n", "\r", "\x00")):
        raise ValueError("invalid command template")
    return arguments, fields


class LanguagePayload(BaseModel):
    """接收一个可安全拆分、无需 shell 执行的语言配置。"""

    name: str
    file_ext: str
    compile_cmd: str | None = None
    run_cmd: str
    time_limit: float = Field(default=1.0, gt=0)
    memory_limit: int = Field(default=128, gt=0)

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> Any:
        """规范化名称并限制其为适合 API 和数据库键的短标识。"""

        if not isinstance(value, str):
            return value
        value = value.strip()
        if not LANGUAGE_NAME.fullmatch(value):
            raise ValueError("invalid language name")
        return value

    @field_validator("file_ext", mode="before")
    @classmethod
    def validate_file_extension(cls, value: Any) -> Any:
        """仅允许简单扩展名，防止源码文件名逃离评测目录。"""

        if not isinstance(value, str):
            return value
        value = value.strip()
        if not FILE_EXTENSION.fullmatch(value):
            raise ValueError("invalid file extension")
        return value

    @field_validator("compile_cmd", "run_cmd", mode="before")
    @classmethod
    def trim_commands(cls, value: Any) -> Any:
        """去除命令两端空白，空编译命令按解释型语言处理。"""

        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("time_limit", mode="before")
    @classmethod
    def validate_time_limit(cls, value: Any) -> Any:
        """资源限制只接受 JSON 数字，拒绝 bool 和字符串隐式转换。"""

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("time_limit must be a number")
        return float(value)

    @field_validator("memory_limit", mode="before")
    @classmethod
    def validate_memory_limit(cls, value: Any) -> Any:
        """内存限制以整数 MB 表示。"""

        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("memory_limit must be an integer")
        return value

    @model_validator(mode="after")
    def validate_command_placeholders(self) -> "LanguagePayload":
        """确保解释型和编译型语言引用各自实际存在的文件。"""

        _, run_fields = parse_command_template(self.run_cmd)
        if self.compile_cmd is None:
            if "src" not in run_fields:
                raise ValueError("interpreted run command must contain {src}")
        else:
            _, compile_fields = parse_command_template(self.compile_cmd)
            if not {"src", "exe"} <= compile_fields or "exe" not in run_fields:
                raise ValueError("compiled commands must contain source and executable")
        return self
