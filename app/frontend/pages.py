"""Streamlit 页面：收集输入并编排 API 调用，不直接访问后端文件或数据库。"""

from collections.abc import Mapping
from typing import Any

import streamlit as st

try:
    from .client import ApiClient, ApiError
    from .forms import (
        build_language_payload,
        build_problem_payload,
        normalize_case_text,
        should_poll,
        submission_query,
    )
    from .state import AUTH_KEY, get_api_client, login, logout
except ImportError:  # Streamlit 直接执行 app/frontend/app.py 时使用同目录模块。
    from client import ApiClient, ApiError
    from forms import (
        build_language_payload,
        build_problem_payload,
        normalize_case_text,
        should_poll,
        submission_query,
    )
    from state import AUTH_KEY, get_api_client, login, logout


def _client() -> ApiClient:
    """取得当前浏览器会话独享的 API 客户端。"""

    return get_api_client(st.session_state)


def _show_error(error: ApiError) -> None:
    """统一展示客户端已经脱敏的错误提示。"""

    st.error(error.message)


def _render_cases(cases: Any, *, heading: str = "样例") -> None:
    """逐条展示输入输出，使真实换行不受表格固定行高限制。

    ``cases`` 来自后端题目详情或 AI 结果。展示层只读取映射中的字符串，
    不修改原始对象；异常行会被忽略，避免不完整响应导致整个页面崩溃。
    """

    rows = (
        [row for row in cases if isinstance(row, Mapping)]
        if isinstance(cases, list)
        else []
    )
    st.markdown(f"**{heading}**")
    if not rows:
        st.info(f"暂无{heading}。")
        return
    for index, row in enumerate(rows, start=1):
        st.caption(f"{heading} {index}")
        input_column, output_column = st.columns(2)
        input_text = normalize_case_text(str(row.get("input", "")))
        output_text = normalize_case_text(str(row.get("output", "")))
        input_column.markdown("输入")
        input_column.code(input_text or "（空）", language=None)
        output_column.markdown("输出")
        output_column.code(output_text or "（空）", language=None)


def _require_user() -> dict[str, Any] | None:
    """返回当前公开身份；直接运行子页面时阻止未登录用户继续请求。"""

    user = st.session_state.get(AUTH_KEY)
    if not isinstance(user, dict):
        st.warning("请先在账户页面登录。")
        return None
    return user


def account_page() -> None:
    """提供注册、登录、资料刷新和登出入口。"""

    st.title("账户")
    user = st.session_state.get(AUTH_KEY)
    if isinstance(user, dict):
        st.caption(f"已登录：{user.get('username', '')} · {user.get('role', '')}")
        try:
            profile = _client().get(f"/api/users/{user['user_id']}")
            if isinstance(profile, dict):
                st.session_state[AUTH_KEY] = {**user, **profile}
                left, middle, right = st.columns(3)
                left.metric("提交次数", profile.get("submit_count", 0))
                middle.metric("通过题数", profile.get("resolve_count", 0))
                right.metric("加入日期", profile.get("join_time", ""))
        except ApiError as error:
            _show_error(error)
        if st.button("退出登录", icon=":material/logout:", type="primary"):
            confirmed = logout(st.session_state)
            if not confirmed:
                st.warning("本地登录状态已清除；后端 Session 可能在有效期结束后才失效。")
            st.rerun()
        return

    login_tab, register_tab = st.tabs(["登录", "注册"])
    with login_tab:
        with st.form("login_form"):
            username = st.text_input("用户名", max_chars=40)
            password = st.text_input("密码", type="password")
            submitted = st.form_submit_button(
                "登录", icon=":material/login:", type="primary"
            )
        if submitted:
            try:
                login(st.session_state, username, password)
                st.rerun()
            except ApiError as error:
                _show_error(error)
    with register_tab:
        with st.form("register_form", clear_on_submit=True):
            new_username = st.text_input("用户名", max_chars=40, key="register_name")
            new_password = st.text_input("密码", type="password", key="register_password")
            registered = st.form_submit_button("创建账户", icon=":material/person_add:")
        if registered:
            try:
                _client().post(
                    "/api/users/",
                    json={"username": new_username, "password": new_password},
                )
                st.success("注册成功，请使用新账户登录。")
            except ApiError as error:
                _show_error(error)


def _problem_editor(
    *, prefix: str, initial: Mapping[str, Any] | None = None, editing: bool = False
) -> None:
    """渲染完整题目表单，并在提交时创建或替换题目。"""

    data = dict(initial or {})
    samples = [
        {**row, "input": normalize_case_text(str(row.get("input", ""))),
         "output": normalize_case_text(str(row.get("output", "")))}
        for row in (data.get("samples") or [{"input": "", "output": ""}])
    ]
    testcases = [
        {**row, "input": normalize_case_text(str(row.get("input", ""))),
         "output": normalize_case_text(str(row.get("output", "")))}
        for row in (data.get("testcases") or [{"input": "", "output": ""}])
    ]
    stored_time_limit = data.get("time_limit")
    stored_memory_limit = data.get("memory_limit")
    with st.form(f"{prefix}_problem_form"):
        first, second = st.columns([1, 2])
        problem_id = first.text_input("题目 ID", value=str(data.get("id", "")), disabled=editing)
        title = second.text_input("标题", value=str(data.get("title", "")))
        description = st.text_area("题目描述", value=str(data.get("description", "")), height=130)
        input_description = st.text_area(
            "输入说明", value=str(data.get("input_description", "")), height=90
        )
        output_description = st.text_area(
            "输出说明", value=str(data.get("output_description", "")), height=90
        )
        constraints = st.text_area("数据范围", value=str(data.get("constraints", "")), height=80)
        st.subheader("样例")
        sample_rows = st.data_editor(
            samples,
            num_rows="dynamic",
            use_container_width=True,
            # 后缀用于丢弃旧版本把换行编码成字面量 ``\\n`` 的 widget 状态。
            key=f"{prefix}_samples_multiline_v2",
            column_config={"input": "输入", "output": "输出"},
        )
        st.subheader("测试点")
        testcase_rows = st.data_editor(
            testcases,
            num_rows="dynamic",
            use_container_width=True,
            key=f"{prefix}_testcases_multiline_v2",
            column_config={"input": "输入", "output": "预期输出"},
        )
        optional_left, optional_right = st.columns(2)
        hint = optional_left.text_area("提示", value=str(data.get("hint", "")))
        source = optional_right.text_input("来源", value=str(data.get("source", "")))
        author = optional_left.text_input("作者", value=str(data.get("author", "")))
        difficulty = optional_right.text_input("难度", value=str(data.get("difficulty", "")))
        tags = st.text_input("标签（英文逗号分隔）", value=",".join(data.get("tags") or []))
        limit_left, limit_right = st.columns(2)
        inherit_time = limit_left.checkbox(
            "时间限制继承语言默认值",
            value=not editing or stored_time_limit is None,
        )
        time_limit = limit_left.number_input(
            "时间限制（秒）",
            min_value=0.01,
            value=float(stored_time_limit if stored_time_limit is not None else 3.0),
            disabled=inherit_time,
        )
        inherit_memory = limit_right.checkbox(
            "内存限制继承语言默认值",
            value=not editing or stored_memory_limit is None,
        )
        memory_limit = limit_right.number_input(
            "内存限制（MB）",
            min_value=1,
            value=int(stored_memory_limit if stored_memory_limit is not None else 128),
            disabled=inherit_memory,
        )
        if editing:
            st.caption("详情接口不公开限制是否继承；本次保存请明确选择继承或固定值。")
        saved = st.form_submit_button(
            "保存题目", icon=":material/save:", type="primary"
        )
    if not saved:
        return
    values = {
        "id": data.get("id", problem_id) if editing else problem_id,
        "title": title,
        "description": description,
        "input_description": input_description,
        "output_description": output_description,
        "constraints": constraints,
        "hint": hint,
        "source": source,
        "author": author,
        "difficulty": difficulty,
        "tags": tags,
        "time_limit": time_limit,
        "memory_limit": memory_limit,
    }
    payload = build_problem_payload(
        values,
        sample_rows.to_dict("records") if hasattr(sample_rows, "to_dict") else sample_rows,
        testcase_rows.to_dict("records") if hasattr(testcase_rows, "to_dict") else testcase_rows,
        inherit_time_limit=inherit_time,
        inherit_memory_limit=inherit_memory,
    )
    if not payload["samples"] or not payload["testcases"]:
        st.error("样例和测试点都至少需要一行。")
        return
    try:
        if editing:
            path_id = ApiClient.path_segment(str(payload["id"]))
            _client().put(f"/api/problems/{path_id}", json=payload)
        else:
            _client().post("/api/problems/", json=payload)
        st.success("题目已保存。")
    except ApiError as error:
        _show_error(error)


def problems_page() -> None:
    """展示题目列表、详情、新增、编辑及管理员删除/日志公开操作。"""

    user = _require_user()
    if user is None:
        return
    st.title("题目")
    try:
        problems = _client().get("/api/problems/")
    except ApiError as error:
        _show_error(error)
        return
    problem_list = problems if isinstance(problems, list) else []
    browse_tab, create_tab, edit_tab = st.tabs(["浏览", "新增", "编辑"])
    with browse_tab:
        if not problem_list:
            st.info("暂无题目。")
        else:
            st.dataframe(problem_list, use_container_width=True, hide_index=True)
            labels = {f"{item['id']} · {item['title']}": item["id"] for item in problem_list}
            selected_label = st.selectbox("查看详情", list(labels), key="problem_browse_select")
            selected_id = labels[selected_label]
            try:
                detail = _client().get(f"/api/problems/{ApiClient.path_segment(selected_id)}")
                st.subheader(f"{detail['id']} · {detail['title']}")
                st.write(detail.get("description", ""))
                info_left, info_right = st.columns(2)
                info_left.markdown(f"**输入说明**\n\n{detail.get('input_description', '')}")
                info_right.markdown(f"**输出说明**\n\n{detail.get('output_description', '')}")
                st.markdown(f"**数据范围**\n\n{detail.get('constraints', '')}")
                _render_cases(detail.get("samples", []))
                st.caption(
                    f"时间 {detail.get('time_limit')} 秒 · 内存 {detail.get('memory_limit')} MB · "
                    f"难度 {detail.get('difficulty') or '未设置'}"
                )
            except ApiError as error:
                _show_error(error)
            if user.get("role") == "admin":
                action_left, action_middle, action_right = st.columns([1, 1, 2])
                if action_left.button("公开日志", icon=":material/visibility:"):
                    try:
                        _client().put(
                            f"/api/problems/{ApiClient.path_segment(selected_id)}/log_visibility",
                            json={"public_cases": True},
                        )
                        st.success("测试点日志已设为登录用户可见。")
                    except ApiError as error:
                        _show_error(error)
                if action_middle.button("设为私有", icon=":material/visibility_off:"):
                    try:
                        _client().put(
                            f"/api/problems/{ApiClient.path_segment(selected_id)}/log_visibility",
                            json={"public_cases": False},
                        )
                        st.success("测试点日志已设为私有。")
                    except ApiError as error:
                        _show_error(error)
                confirm = action_right.checkbox("确认删除该题", key="confirm_problem_delete")
                if action_right.button(
                    "删除题目", icon=":material/delete:", disabled=not confirm
                ):
                    try:
                        _client().delete(f"/api/problems/{ApiClient.path_segment(selected_id)}")
                        st.success("题目已删除。")
                        st.rerun()
                    except ApiError as error:
                        _show_error(error)
    with create_tab:
        _problem_editor(prefix="create")
    with edit_tab:
        if not problem_list:
            st.info("请先新增题目。")
        else:
            edit_labels = {
                f"{item['id']} · {item['title']}": item["id"] for item in problem_list
            }
            edit_label = st.selectbox(
                "选择题目", list(edit_labels), key="problem_edit_select"
            )
            edit_id = edit_labels[edit_label]
            try:
                initial = _client().get(
                    f"/api/problems/{ApiClient.path_segment(edit_id)}"
                )
                _problem_editor(prefix=f"edit_{edit_id}", initial=initial, editing=True)
            except ApiError as error:
                _show_error(error)


def _render_ai_result(detail: Mapping[str, Any]) -> None:
    """展示已校验的 AI 题目，并允许进入人工审阅后的正式保存流程。"""

    result = detail.get("result")
    if not isinstance(result, dict):
        return
    st.subheader(f"{result.get('id', '')} · {result.get('title', '')}")
    st.write(result.get("description", ""))
    preview_left, preview_right = st.columns(2)
    preview_left.markdown(f"**输入说明**\n\n{result.get('input_description', '')}")
    preview_right.markdown(f"**输出说明**\n\n{result.get('output_description', '')}")
    _render_cases(result.get("samples", []))
    _render_cases(result.get("testcases", []), heading="测试点")
    st.caption(
        f"难度 {result.get('difficulty') or '未设置'} · "
        f"样例 {len(result.get('samples') or [])} · "
        f"测试点 {len(result.get('testcases') or [])}"
    )
    if st.button("载入审阅表单", icon=":material/edit_document:", type="primary"):
        # 草稿只保存在当前 Streamlit 会话；正式持久化仍调用题目 CRUD API。
        st.session_state["ai_problem_draft"] = result
        st.success("已载入下方审阅表单，请核对题面和测试点后保存。")
    draft = st.session_state.get("ai_problem_draft")
    if isinstance(draft, dict):
        _problem_editor(
            prefix=f"ai_{detail.get('task_id', 'draft')}_{detail.get('current_round', 1)}",
            initial=draft,
            editing=detail.get("result_action") == "update",
        )


def _render_ai_task(task_id: str) -> None:
    """轮询任务并分别呈现阶段、用量、取消、结果和追问入口。"""

    try:
        detail = _client().get(f"/api/ai/problem-tasks/{ApiClient.path_segment(task_id)}")
    except ApiError as error:
        _show_error(error)
        return
    if not isinstance(detail, dict):
        st.error("AI 任务响应缺少状态信息。")
        return
    status = str(detail.get("status", ""))
    active = status in {"pending", "running"}
    st.session_state["active_ai_task_running"] = active
    progress = detail.get("progress") if isinstance(detail.get("progress"), dict) else {}
    usage = detail.get("usage") if isinstance(detail.get("usage"), dict) else {}
    status_left, status_middle, status_right = st.columns(3)
    status_left.metric("任务状态", status or "未知")
    status_middle.metric("当前轮次", detail.get("current_round", 1))
    status_right.metric(
        "累计费用", f"{usage.get('cost', 0):.8f} {usage.get('currency', 'USD')}"
    )
    percent = int(progress.get("percent", 0))
    st.progress(max(0, min(100, percent)), text=str(progress.get("message", "")))
    token_left, token_middle, token_right = st.columns(3)
    token_left.metric("输入 Token", usage.get("input_tokens", 0))
    token_middle.metric("输出 Token", usage.get("output_tokens", 0))
    token_right.metric("统计来源", usage.get("source", "estimated"))
    st.caption(f"价格按每 {usage.get('price_unit', 1000000)} Token 计价。")

    if active and st.button("中断任务", icon=":material/stop_circle:"):
        try:
            _client().put(
                f"/api/ai/problem-tasks/{ApiClient.path_segment(task_id)}/cancel"
            )
            st.session_state["active_ai_task_running"] = False
            st.rerun()
        except ApiError as error:
            _show_error(error)
    if status == "failed" and detail.get("error_info"):
        st.error(f"命题任务失败：{detail['error_info']}")
    rounds = detail.get("rounds")
    if isinstance(rounds, list):
        with st.expander("轮次与用量", expanded=False):
            st.dataframe(
                [
                    {
                        "轮次": item.get("round"),
                        "要求": item.get("requirement"),
                        "状态": item.get("status"),
                        "Token": (item.get("usage") or {}).get("total_tokens", 0),
                        "费用": (item.get("usage") or {}).get("cost", 0),
                    }
                    for item in rounds if isinstance(item, dict)
                ],
                use_container_width=True,
                hide_index=True,
            )
    if status == "completed":
        _render_ai_result(detail)
        with st.form("ai_continue_form", clear_on_submit=True):
            next_mode_label = st.radio(
                "下一轮目标",
                ["修改当前题", "另出一道新题"],
                horizontal=True,
                help="修改当前题会保持 ID；另出新题会强制使用不同且未占用的 ID。",
            )
            follow_up = st.text_area("继续修改要求", max_chars=4000)
            continued = st.form_submit_button(
                "开始下一轮", icon=":material/refresh:", type="primary"
            )
        if continued:
            wants_new_problem = any(
                keyword in follow_up for keyword in ("另出", "新题", "重新出题", "不同题目")
            )
            if wants_new_problem and next_mode_label == "修改当前题":
                st.warning("你的要求包含新题意图；请将“下一轮目标”切换为“另出一道新题”后再提交。")
                return
            try:
                _client().post(
                    f"/api/ai/problem-tasks/{ApiClient.path_segment(task_id)}/rounds",
                    json={
                        "requirement": follow_up,
                        "mode": "revise" if next_mode_label == "修改当前题" else "new",
                    },
                )
                st.session_state.pop("ai_problem_draft", None)
                st.session_state["active_ai_task_running"] = True
                st.rerun()
            except ApiError as error:
                _show_error(error)


def ai_problems_page() -> None:
    """提交命题要求，持续观察任务，并把结果回填到题目 CRUD。"""

    if _require_user() is None:
        return
    st.title("AI 智能命题")
    try:
        config = _client().get("/api/ai/model-config")
        problems = _client().get("/api/problems/")
    except ApiError as error:
        _show_error(error)
        return
    if not isinstance(config, dict) or not config.get("configured"):
        st.warning("AI 模型尚未由部署环境完整配置。")
        return
    st.caption(
        f"模型 {config.get('model')} · {config.get('provider_url')} · "
        f"密钥已配置：{'是' if config.get('api_key_configured') else '否'}"
    )
    problem_items = problems if isinstance(problems, list) else []
    choices = {"生成新题": ""}
    choices.update(
        {f"改编 {item['id']} · {item['title']}": item["id"] for item in problem_items}
    )
    with st.form("ai_create_form"):
        mode = st.selectbox("任务方式", list(choices))
        requirement = st.text_area(
            "命题要求",
            max_chars=4000,
            height=180,
            placeholder="知识点、预期难度、输入规模、测试重点和其他约束",
        )
        created = st.form_submit_button(
            "开始命题", icon=":material/auto_awesome:", type="primary"
        )
    if created:
        payload: dict[str, Any] = {"requirement": requirement}
        if choices[mode]:
            payload["problem_id"] = choices[mode]
        try:
            result = _client().post("/api/ai/problem-tasks/", json=payload)
            st.session_state["active_ai_task_id"] = result["task_id"]
            st.session_state["active_ai_task_running"] = True
            st.session_state.pop("ai_problem_draft", None)
            st.rerun()
        except ApiError as error:
            _show_error(error)

    task_id = str(st.session_state.get("active_ai_task_id", ""))
    if task_id:
        run_every = "1s" if st.session_state.get("active_ai_task_running") else None

        @st.fragment(run_every=run_every)
        def poll_ai_task() -> None:
            """仅重跑任务区域；终态后完整 rerun 会停止定时器。"""

            was_running = bool(st.session_state.get("active_ai_task_running"))
            _render_ai_task(task_id)
            if was_running and not st.session_state.get("active_ai_task_running"):
                st.rerun()

        poll_ai_task()


def _render_submission_log(submission_id: str) -> None:
    """独立查询日志，使公开测试点不依赖当前用户拥有提交详情权限。"""

    try:
        log = _client().get(f"/api/submissions/{submission_id}/log")
        st.subheader("测试点日志")
        rows = log.get("details", []) if isinstance(log, dict) else []
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("暂无可显示的测试点日志。")
    except ApiError as error:
        _show_error(error)


def _render_submission_detail(submission_id: str) -> None:
    """查询并分别呈现任务状态、编译、运行、任务错误和测试点日志。"""

    was_pending = bool(st.session_state.get("active_submission_pending"))
    try:
        detail = _client().get(f"/api/submissions/{submission_id}")
    except ApiError as error:
        _show_error(error)
        # 公开日志是一项独立授权：详情 403 不代表日志接口也必然拒绝。
        if error.status_code == 403:
            _render_submission_log(submission_id)
        return
    status = detail.get("status")
    st.session_state["active_submission_pending"] = should_poll(status)
    first, second, third = st.columns(3)
    first.metric("提交 ID", detail.get("submission_id", submission_id))
    second.metric("任务状态", status or "未知")
    third.metric("分数", f"{detail.get('score', '-')} / {detail.get('counts', '-')}")
    compile_info = detail.get("compile_info")
    run_info = detail.get("run_info")
    if compile_info:
        st.subheader("编译信息")
        st.write(compile_info.get("result", ""), compile_info.get("message", ""))
    if run_info:
        st.subheader("运行信息")
        st.write(run_info.get("result", ""), run_info.get("message", ""))
    if detail.get("error_info"):
        st.error(f"评测任务错误：{detail['error_info']}")
    if status != "pending":
        _render_submission_log(submission_id)
        if was_pending:
            # fragment 的调度间隔在创建时固定；完整 rerun 会以 None 重建并停止定时器。
            st.rerun()


def submissions_page() -> None:
    """提供源码提交、筛选列表、状态轮询、日志详情与管理员重判。"""

    user = _require_user()
    if user is None:
        return
    st.title("提交")
    try:
        problems = _client().get("/api/problems/")
        languages = _client().get("/api/languages/")
    except ApiError as error:
        _show_error(error)
        return
    problem_ids = [item["id"] for item in problems] if isinstance(problems, list) else []
    language_names = languages.get("name", []) if isinstance(languages, dict) else []
    submit_tab, records_tab, detail_tab, language_tab = st.tabs(
        ["提交代码", "提交记录", "结果详情", "语言管理"]
    )
    with submit_tab:
        if not problem_ids or not language_names:
            st.info("提交前需要至少一道题目和一种可用语言。")
        else:
            with st.form("submit_code_form"):
                left, right = st.columns(2)
                problem_id = left.selectbox("题目", problem_ids)
                language = right.selectbox("语言", language_names)
                code = st.text_area("源代码", height=360)
                submitted = st.form_submit_button(
                    "提交评测", icon=":material/send:", type="primary"
                )
            if submitted:
                try:
                    result = _client().post(
                        "/api/submissions/",
                        json={"problem_id": problem_id, "language": language, "code": code},
                    )
                    st.session_state["active_submission_id"] = result["submission_id"]
                    st.session_state["active_submission_pending"] = True
                    st.success(f"提交 {result['submission_id']} 已进入队列。")
                except ApiError as error:
                    _show_error(error)
    with language_tab:
        registered_name = st.session_state.pop("language_registered_name", None)
        if registered_name:
            st.success(f"语言 {registered_name} 已注册，并已加入提交语言列表。")

        st.subheader("支持语言列表")
        if language_names:
            # GET 接口有意只公开名称；运行命令属于评测器内部配置，不在前端回显。
            st.dataframe(
                [{"语言名称": name} for name in language_names],
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("当前没有可用语言。")

        st.subheader("注册新语言")
        language_kind = st.segmented_control(
            "语言类型",
            options=["解释型", "编译型"],
            default="解释型",
            selection_mode="single",
            key="language_kind",
        )
        compiled = language_kind == "编译型"
        with st.form("register_language_form", clear_on_submit=True):
            identity_left, identity_right = st.columns(2)
            language_name = identity_left.text_input(
                "语言名称", placeholder="例如 ruby 或 go"
            )
            file_extension = identity_right.text_input(
                "源码扩展名", placeholder="例如 .rb 或 .go"
            )
            compile_command = ""
            if compiled:
                compile_command = st.text_input(
                    "编译命令",
                    value="g++ {src} -o {exe}",
                    key="compiled_language_compile_cmd",
                )
                run_command = st.text_input(
                    "运行命令", value="{exe}", key="compiled_language_run_cmd"
                )
            else:
                run_command = st.text_input(
                    "运行命令",
                    value="python3 {src}",
                    key="interpreted_language_run_cmd",
                )
            limit_left, limit_right = st.columns(2)
            time_limit = limit_left.number_input(
                "默认时间限制（秒）", min_value=0.01, value=1.0
            )
            memory_limit = limit_right.number_input(
                "默认内存限制（MB）", min_value=1, value=128
            )
            register_language = st.form_submit_button(
                "注册语言", icon=":material/add:", type="primary"
            )
        if register_language:
            payload = build_language_payload(
                {
                    "name": language_name,
                    "file_ext": file_extension,
                    "compile_cmd": compile_command,
                    "run_cmd": run_command,
                    "time_limit": time_limit,
                    "memory_limit": memory_limit,
                },
                compiled=compiled,
            )
            try:
                result = _client().post("/api/languages/", json=payload)
                # 完整重跑会重新执行顶部 GET，并让新语言立即进入提交下拉框。
                st.session_state["language_registered_name"] = result["name"]
                st.rerun()
            except ApiError as error:
                _show_error(error)
    with records_tab:
        with st.form("submission_filter"):
            left, middle, right = st.columns(3)
            default_user = str(user.get("user_id", "")) if user.get("role") != "admin" else ""
            filter_user = left.text_input("用户 ID", value=default_user, disabled=user.get("role") != "admin")
            filter_problem = middle.text_input("题目 ID")
            filter_status = right.selectbox("任务状态", ["全部", "pending", "success", "error"])
            use_pagination = st.checkbox("分页显示", value=True)
            page_left, page_right = st.columns(2)
            page = page_left.number_input("页码", min_value=1, value=1, disabled=not use_pagination)
            page_size = page_right.number_input("每页数量", min_value=1, value=20, disabled=not use_pagination)
            queried = st.form_submit_button("查询", icon=":material/search:")
        if queried:
            params = submission_query(
                user_id=filter_user,
                problem_id=filter_problem,
                status=filter_status,
                use_pagination=use_pagination,
                page=int(page),
                page_size=int(page_size),
            )
            if "user_id" not in params and "problem_id" not in params:
                st.error("用户 ID 和题目 ID 至少填写一项。")
            else:
                try:
                    result = _client().get("/api/submissions/", params=params)
                    st.caption(f"共 {result.get('total', 0)} 条记录")
                    st.dataframe(result.get("submissions", []), use_container_width=True, hide_index=True)
                except ApiError as error:
                    _show_error(error)
    with detail_tab:
        initial_id = str(st.session_state.get("active_submission_id", ""))
        submission_id = st.text_input("提交 ID", value=initial_id, key="detail_submission_input")
        if st.button("刷新结果", icon=":material/refresh:") and submission_id.isdecimal():
            st.session_state["active_submission_id"] = submission_id
        active_id = str(st.session_state.get("active_submission_id", ""))
        if active_id:
            run_every = "1s" if st.session_state.get("active_submission_pending") else None

            @st.fragment(run_every=run_every)
            def poll_active_submission() -> None:
                """fragment 只重跑详情区域，终态时下一次整页执行将停止定时器。"""

                _render_submission_detail(active_id)

            poll_active_submission()
            if user.get("role") == "admin" and st.button(
                "重新评测", icon=":material/replay:"
            ):
                try:
                    _client().put(f"/api/submissions/{active_id}/rejudge")
                    st.session_state["active_submission_pending"] = True
                    st.success("已使用原提交 ID 重新评测。")
                    st.rerun()
                except ApiError as error:
                    _show_error(error)


def users_page() -> None:
    """管理员查询用户、创建管理员并修改其他用户角色。"""

    user = _require_user()
    if user is None or user.get("role") != "admin":
        st.error("此页面仅管理员可用。")
        return
    st.title("用户管理")
    try:
        result = _client().get("/api/users/")
        users = result.get("users", []) if isinstance(result, dict) else []
        st.caption(f"共 {result.get('total', 0)} 个用户")
        st.dataframe(users, use_container_width=True, hide_index=True)
    except ApiError as error:
        _show_error(error)
        users = []
    create_tab, role_tab = st.tabs(["创建管理员", "修改角色"])
    with create_tab:
        with st.form("create_admin_form", clear_on_submit=True):
            username = st.text_input("用户名", key="new_admin_name")
            password = st.text_input("密码", type="password", key="new_admin_password")
            create = st.form_submit_button("创建", icon=":material/person_add:")
        if create:
            try:
                _client().post("/api/users/admin", json={"username": username, "password": password})
                st.success("管理员账户已创建。")
            except ApiError as error:
                _show_error(error)
    with role_tab:
        candidates = [item for item in users if item.get("user_id") != user.get("user_id")]
        if not candidates:
            st.info("没有可修改的其他用户。")
        else:
            labels = {f"{item['user_id']} · {item['username']}": item["user_id"] for item in candidates}
            target_label = st.selectbox("用户", list(labels))
            role = st.selectbox("新角色", ["user", "admin", "banned"])
            if st.button("更新角色", icon=":material/admin_panel_settings:"):
                try:
                    _client().put(f"/api/users/{labels[target_label]}/role", json={"role": role})
                    st.success("角色已更新。")
                    st.rerun()
                except ApiError as error:
                    _show_error(error)


def audits_page() -> None:
    """管理员按用户、题目和合法分页组合查看日志访问审计。"""

    user = _require_user()
    if user is None or user.get("role") != "admin":
        st.error("此页面仅管理员可用。")
        return
    st.title("访问审计")
    with st.form("audit_filter"):
        left, right = st.columns(2)
        user_id = left.text_input("用户 ID")
        problem_id = right.text_input("题目 ID")
        paginate = st.checkbox("分页显示", value=True, key="audit_paginate")
        page_left, page_right = st.columns(2)
        page = page_left.number_input("页码", min_value=1, value=1, disabled=not paginate, key="audit_page")
        page_size = page_right.number_input(
            "每页数量", min_value=1, value=20, disabled=not paginate, key="audit_page_size"
        )
        query = st.form_submit_button("查询", icon=":material/search:")
    if query:
        params: dict[str, str | int] = {}
        if user_id.strip():
            params["user_id"] = user_id.strip()
        if problem_id.strip():
            params["problem_id"] = problem_id.strip()
        if paginate:
            params.update(page=int(page), page_size=int(page_size))
        try:
            audits = _client().get("/api/logs/access/", params=params)
            st.dataframe(audits if isinstance(audits, list) else [], use_container_width=True, hide_index=True)
        except ApiError as error:
            _show_error(error)
