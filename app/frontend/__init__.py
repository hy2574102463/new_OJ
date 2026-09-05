"""Streamlit 前端包，只通过 FastAPI 访问业务数据，不接触后端存储层。"""

from app.frontend.client import ApiClient, ApiError

__all__ = ["ApiClient", "ApiError"]
