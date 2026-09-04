"""配置全局 Python 日志格式和最低输出级别。"""

import logging


def configure_logging(level: str) -> None:
    """按字符串级别初始化日志；无效级别安全回退到 INFO。"""

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
