"""封装密码慢哈希和 Session 令牌摘要等安全原语。"""

import asyncio
import hashlib
import secrets

import bcrypt


def _password_material(password: str) -> bytes:
    """把任意长度 UTF-8 密码压缩成固定长度后再交给 bcrypt。"""

    return hashlib.sha256(password.encode("utf-8")).digest()


async def hash_password(password: str) -> str:
    """在线程中生成 bcrypt 哈希，避免慢计算阻塞事件循环。"""

    hashed = await asyncio.to_thread(
        bcrypt.hashpw, _password_material(password), bcrypt.gensalt()
    )
    return hashed.decode("ascii")


async def verify_password(password: str, password_hash: str) -> bool:
    """在线程中验证密码；损坏的数据库哈希按验证失败处理。"""

    try:
        return await asyncio.to_thread(
            bcrypt.checkpw,
            _password_material(password),
            password_hash.encode("ascii"),
        )
    except (ValueError, UnicodeError):
        return False


def new_session_token() -> str:
    """生成具有足够熵且适合放入 Cookie 的原始 Session 令牌。"""

    return secrets.token_urlsafe(32)


def session_token_hash(token: str) -> str:
    """生成数据库存储用摘要，使数据库泄漏时原始 Cookie 不直接暴露。"""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
