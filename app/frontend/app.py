"""Streamlit 启动入口：根据后端返回的登录角色构造原生多页面导航。"""

import streamlit as st

# Streamlit 把脚本当作顶层文件执行，而 pytest 会按包导入；两种入口需要不同语法。
try:
    from .pages import (
        account_page,
        ai_problems_page,
        audits_page,
        problems_page,
        submissions_page,
        users_page,
    )
    from .state import AUTH_KEY
except ImportError:  # 直接执行 app/frontend/app.py 时没有 Python 包上下文。
    from pages import (
        account_page,
        ai_problems_page,
        audits_page,
        problems_page,
        submissions_page,
        users_page,
    )
    from state import AUTH_KEY


def main() -> None:
    """配置工作台并运行当前身份可见的页面；后端仍会逐请求鉴权。"""

    st.set_page_config(page_title="课程 OJ", page_icon=":material/code:", layout="wide")
    user = st.session_state.get(AUTH_KEY)
    pages: dict[str, list[st.Page]] = {
        "账户": [st.Page(account_page, title="账户", icon=":material/account_circle:")]
    }
    # 页面入口始终存在，避免匿名用户访问 Streamlit 页面路由时得到页面层 404；
    # 页面内部仍由 _require_user 和后端 API 分别执行提示与真正鉴权。
    pages["在线评测"] = [
        st.Page(problems_page, title="题目", icon=":material/menu_book:"),
        st.Page(submissions_page, title="提交", icon=":material/code:"),
    ]
    pages["智能命题"] = [
        st.Page(ai_problems_page, title="AI 命题", icon=":material/auto_awesome:")
    ]
    if isinstance(user, dict):
        if user.get("role") == "admin":
            pages["管理"] = [
                st.Page(users_page, title="用户管理", icon=":material/group:"),
                st.Page(audits_page, title="访问审计", icon=":material/policy:"),
            ]
    navigation = st.navigation(pages, position="sidebar", expanded=True)
    navigation.run()


main()
