# OJ 大作业：开发规划与项目纲要

本仓库用于实现课程 OJ 大作业。规划依据 [`oj/index.md`](oj/index.md)、[`oj/api.md`](oj/api.md)、各 Step 实验说明及 [`oj/requirements.md`](oj/requirements.md) 制定。目标是在验收前交付一个可运行、可演示、接口行为一致的小型 Online Judge，并预留 AI 智能命题扩展。

## 当前实现状态

截至 2026-09-05，基础设施、Step 4 用户认证、Step 1 题目管理、Step 2 评测控制和 Step 3 评测管理已经完成；Step 3 当前位于未提交工作区，评测日志和 Streamlit 页面尚未实现：

| 阶段 | 状态 | 基线提交 | 已验证内容 |
| --- | --- | --- | --- |
| P0 基础设施 | 已完成 | `f3df18e` | 应用工厂、异步 SQLite、迁移、统一响应、健康检查、测试 reset |
| Step 4 用户管理 | 已完成 | `896261e` | 注册、Session 登录/登出、bcrypt、角色权限、初始管理员 |
| Step 1 题目管理 | 已完成 | `cce992e` | JSON CRUD、字段默认值、原子写入、启动校验、权限控制 |
| Step 2 评测控制 | 已完成 | `1cb223d` | Python/C++、语言注册、异步提交、AC/WA/RE/CE/TLE/MLE |
| Step 3 评测管理 | 已完成、待提交 | `1cb223d` 后工作区 | 组合筛选、分页、详情权限和原 ID 重判 |
| Step 5/6 | 未开始 | - | 日志审计、Streamlit 前端 |
| Advance | 可选、未开始 | - | AI 配置、任务进度、取消和费用统计 |

当前完整测试基线是 **78 passed, 2 warnings**。两条 warning 来自 FastAPI/Starlette `TestClient` 的上游弃用提示，不影响现有功能。Step 3 新增覆盖包括分页参数组合、用户/题目/状态筛选、列表字段裁剪、越权优先级、原 ID 重判、旧测试点清理和用户统计修正。

下一步是 Step 5，提供测试点日志查询、题目日志可见性和访问审计。实现时复用已有 case_results，但不能通过列表或普通详情绕过 Step 5 权限读取测试点明细。

### 当前已知边界

- 题目文件的并发锁仅在单个应用进程内生效；当前阶段不支持多个 Uvicorn worker 同时写同一道题。
- `POST /api/reset/` 只允许测试环境使用，会清空测试数据并重建初始管理员，不能作为生产管理接口。
- 当前已实现提交创建、列表、详情和重判；测试点日志、公开策略和访问审计接口仍不可用。
- 评测隔离是适合课程验收的进程工作目录、进程组清理和资源监控，不等同于容器或虚拟机安全边界；不要把服务直接暴露给不受信任的公网用户。
- 默认管理员凭据只用于课程初始验收；部署到真实环境前必须增加安全的改密或初始化流程。

### 创建环境并运行

以下命令均在仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/python -m uvicorn app.main:app --reload
```

第一条创建项目专用 Python 环境；第二条只向 `.venv` 安装依赖；第三条运行测试；第四条启动开发服务器。启动后访问 `http://127.0.0.1:8000/health`，预期得到：

```json
{"code":200,"msg":"success","data":{"status":"healthy","database":"ok"}}
```

复制 `.env.example` 为 `.env` 可覆盖配置。支持的变量如下：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `OJ_ENVIRONMENT` | `development` | 运行环境：`development`、`test` 或 `production` |
| `OJ_DATABASE_PATH` | `data/oj.db` | SQLite 数据库文件路径 |
| `OJ_PROBLEMS_PATH` | `data/problems` | 每题一个 JSON 的题目配置目录 |
| `OJ_JUDGE_WORKSPACE_PATH` | `.judge-tmp` | 用户源码、可执行文件所在的临时隔离目录 |
| `OJ_LOG_LEVEL` | `INFO` | Python 日志级别 |
| `OJ_TEST_RESET_ENABLED` | `false` | 是否启用测试 reset；还必须同时处于 `test` 环境 |
| `OJ_SESSION_COOKIE_NAME` | `oj_session` | 浏览器保存 Session 令牌的 Cookie 名 |
| `OJ_SESSION_TTL_SECONDS` | `86400` | Session 有效秒数，默认 24 小时 |

运行时数据库、`.env`、虚拟环境、日志和评测临时文件均已加入 `.gitignore`。不要把密码、Session、模型密钥或用户代码写入环境模板和日志。

### 已实现接口与分层

- `GET /health`：检查应用与 SQLite 是否可用。
- `POST /api/reset/`：仅当 `OJ_ENVIRONMENT=test` 且 `OJ_TEST_RESET_ENABLED=true` 时可用；其他环境返回 404。重置会清理 Session Cookie 并重建初始管理员；正式管理员鉴权将在该测试接口需要开放到非测试环境时补充。
- `POST /api/users/`、`POST /api/auth/login`、`POST /api/auth/logout`：注册、登录与登出。
- `GET /api/users/{user_id}`：本人或管理员查询用户资料。
- `GET /api/users/`、`POST /api/users/admin`、`PUT /api/users/{user_id}/role`：管理员用户管理。
- `app/api/` 只负责请求和响应编排，`app/services/` 放业务规则，`app/repositories/` 负责持久化。
- `GET/POST /api/languages/`：查询或由登录用户注册安全的语言命令模板。
- `POST/GET /api/submissions/`、`GET /api/submissions/{submission_id}`、`PUT /api/submissions/{submission_id}/rejudge`：提交、筛选分页、详情轮询和管理员重判。
- FastAPI lifespan 在服务接收请求前执行迁移；`schema_migrations` 保证同一迁移只执行一次。
- 数据库事务成功时提交，异常时回滚；路由等待 `aiosqlite` 时不会用同步磁盘调用阻塞事件循环。

排查方法：启动失败时先检查依赖是否安装在 `.venv`；健康检查返回 500 时检查数据库目录是否可写；测试 reset 返回 404 时检查两个测试配置是否同时启用。错误响应不会返回内部异常文本，可结合服务端记录的请求 ID 和异常类型定位。

### Session 与用户权限

系统启动时幂等创建 `admin/admintestpassword`。登录成功后，浏览器 Cookie 保存随机原始令牌，SQLite 只保存令牌的 SHA-256 摘要、用户 ID 和过期时间；密码经过 SHA-256 预处理后使用 bcrypt 慢哈希，数据库和普通响应均不保存明文密码。Cookie 设置 `HttpOnly` 和 `SameSite=Lax`，生产环境额外启用 `Secure`。

用户名去除首尾空白后保存，并使用 `casefold` 键实现大小写无关登录和唯一性。管理员不能通过角色接口修改自己的角色，该操作返回 409，避免唯一管理员误操作后使系统失去管理入口。用户被改为 `banned` 时会删除其全部 Session；其已有请求随后返回 401，再次登录返回 403。

认证请求的数据流为：路由读取 Cookie → 认证服务计算令牌摘要 → Repository 联查 Session 与用户 → 权限依赖判断角色 → 路由返回不含密码的公开字段。未登录是 401，已经登录但角色不足是 403。

### Step 1 题目管理

题目接口为 `GET/POST /api/problems/` 和 `GET/PUT/DELETE /api/problems/{problem_id}`。所有操作都需要登录，新增、查看和编辑允许普通用户执行，删除只允许管理员执行。列表只返回 `id/title`，详情返回完整题目配置。

题目 ID、标题、描述、输入输出说明和约束必须是非空字符串；`samples`、`testcases` 必须各有至少一项，每项必须提供字符串 `input/output`。测试点文本允许为空，以支持无输入或空输出题目。可选字符串缺省返回 `""`，标签缺省返回 `[]`。

JSON 内未设置 `time_limit/memory_limit` 时保留 `null`，供 Step 2 继承语言默认限制；Step 1 详情接口按 API 文档展示 `3.0` 秒和 `128` MB。`public_cases` 是 Step 5 管理的内部字段，普通题目编辑会保留它且不会在 Step 1 响应中公开。

磁盘文件名是题目 ID 的 SHA-256 摘要，用户输入不能构造目录路径。写入先完成同目录临时文件，再使用原子替换发布；文件操作通过工作线程执行，避免阻塞 FastAPI 事件循环。应用启动会校验全部 JSON，损坏配置会阻止启动，而不会静默漏掉题目。

手动验收可在 `/docs` 依次执行：登录 → 新增题目 → 查看列表 → 查看详情 → 编辑 → 管理员删除。预期重复 ID 返回 409、路径 ID 与正文 ID 不一致返回 400、不存在返回 404、普通用户删除返回 403、匿名请求返回 401。

### Step 2 评测控制

SQLite 迁移 3 保存语言配置，迁移 4 保存 submissions 和内部 case results；启动及测试 reset 会幂等恢复 Python/C++ 默认语言。语言命令先使用 `shlex` 拆成参数数组，再通过 `asyncio.create_subprocess_exec` 执行，不经过 shell。Python 默认运行命令为 `python3 {src}`，C++ 使用 C++14 编译后执行 `{exe}`。

提交路由写入 `pending` 后用 `asyncio.create_task` 启动后台评测，因此 HTTP 请求无需等待用户程序。服务持有任务强引用；应用关闭或测试 reset 时取消任务，运行器随后杀死整个进程组并清理临时目录。墙钟时间由异步循环限制，进程树 RSS 由 `psutil` 监控，Linux `prlimit` 另设 CPU、地址空间、文件大小和文件描述符硬上限；每点 10 分。WA、RE、CE、TLE、MLE 都是正常完成的判题结论，因此 submission 为 `success`；只有评测基础设施失败才为 `error`。

输出比较仅忽略每行末尾空白和整个输出末尾的多余换行，行首空格、内部空行和额外提示语仍参与比较。题目没有填写限制时继承语言默认值。Step 2 详情只返回总分、编译信息、运行摘要和任务错误，不返回源码或测试点明细。

可在登录并创建题目后提交并轮询：

```bash
curl -b cookies.txt -H 'Content-Type: application/json' \
  -d '{"problem_id":"P1001","language":"python","code":"a,b=map(int,input().split());print(a+b)"}' \
  http://127.0.0.1:8000/api/submissions/
curl -b cookies.txt http://127.0.0.1:8000/api/submissions/1
```

第一次响应应包含 `pending`；随后详情变为 `success` 并显示 `score/counts`。一分钟内第四次提交返回 429，匿名访问返回 401，其他普通用户读取已有提交返回 403。

### Step 3 评测管理

提交列表必须提供 `user_id` 或 `problem_id` 至少一个一级条件，并可继续按 `pending/success/error` 筛选。`page/page_size` 都不提供时查全部；仅提供 `page_size` 时查第一页；仅提供 `page` 返回 400。普通用户只能查询自己的记录，管理员可跨用户查询。结果按 submission ID 排序，pending/error 摘要只含 ID 和状态，success 额外包含 `score/counts`。

管理员重判使用原 submission ID、归属、语言和源码，并读取题目当前测试点与限制。切回 pending 的事务会删除旧 case results、清空汇总字段，并在必要时撤销该用户唯一的 AC 题目计数；后台评测完成后再按新结果恢复统计。重判不增加 `submit_count`，pending 记录重复重判返回 409，题目或语言已不存在时返回 404 且保留旧结果。

可使用以下请求验证列表和重判：

```bash
curl -b cookies.txt 'http://127.0.0.1:8000/api/submissions/?problem_id=P1001&page_size=10'
curl -b admin-cookies.txt -X PUT http://127.0.0.1:8000/api/submissions/1/rejudge
```

列表应返回 `total/submissions`；重判立即返回原 ID 和 pending，随后详情重新变为 success 或 error。

## 1. 交付目标与边界

### 课程交付与硬性限制

- 作业总分 50 分：实验功能验收 40 分、代码规范 5 分、实验报告 5 分，最终按总评 30% 折算；基础六个 Step 各 5 分，AI 进阶模块 10 分。
- 代码须在 9 月 10 日课前完成并提交最后一次 commit；报告于当日 23:59 前提交。未参加线下验收时实验功能部分记零分，原则上不接受补交，每人最多一次补交机会。
- 报告 5 分由系统功能与设计（2 分）、关键实现与难点（2 分）、成果展示与边界测试（1 分）构成；总结与建议、AI 使用说明虽不单独计分，使用 AI/Vibe Coding 时仍需说明工具链、工作流和代码比例。代码、报告、演示内容必须一致，禁止抄袭，避免提交大文件。
- 推荐 Python 3.10、GCC 9+ 与 C++14；最终自动评测采用 Linux 风格命令。macOS 应兼容相关 Linux 指令，Windows 建议使用 WSL。

### 必须完成（基础模块 30 分）

1. **题目管理（Step 1）**：题目 JSON 配置加载、字段校验、列表/详情、新增、编辑、删除。
2. **评测控制（Step 2）**：异步执行用户代码，至少支持 Python，扩展 C++；输出比对、编译/运行错误、TLE/MLE 资源限制；支持语言动态注册和列表查询。
3. **评测管理（Step 3）**：提交记录详情和列表，按用户/题目/状态筛选及分页，管理员重新评测。
4. **用户管理（Step 4）**：注册、登录、登出、Session、初始管理员、用户信息/列表、角色变更（`user`/`admin`/`banned`）。
5. **评测日志（Step 5）**：逐测试点评测明细、公开可见性、访问审计；普通用户只能访问授权内容，管理员可查看全部。
6. **前端交互（Step 6）**：使用 Streamlit 实现用户、题目、提交/评测三组页面，所有数据通过 FastAPI API 交换。

所有后端接口使用 `async def`；JSON 响应统一为 `{code, msg, data}`，HTTP 状态码必须真实反映成功或错误（400/401/403/404/409/429/500）。

### 可选扩展（进阶 10 分）

AI 智能命题应与题目新增/编辑流程衔接，支持可配置模型（URL、名称、密钥）、异步任务进度与实际中断、Token/费用统计，并保护密钥和错误信息。工具调用不是硬性要求，但生成的题面和测试点必须可用。

Step 2 只要求保证单用户提交任务的正确性，不要求同时处理多用户提交；但评测接口仍必须异步，不能阻塞 FastAPI 事件循环。Step 6 不要求 JavaScript、HTML、CSS，必须使用 Python Streamlit，且不得绕过后端直接读写数据。

## 2. 推荐架构

采用前后端分离、分层实现：

```text
app/
  main.py                 # FastAPI/Streamlit 启动入口（可按实际拆分）
  api/                    # 路由、依赖、统一异常处理
  schemas/                # Pydantic 请求/响应模型
  services/               # 题目、评测、用户、日志、AI 业务逻辑
  repositories/           # JSON/SQLite 持久化与查询
  judge/                  # 编译器/运行器、资源限制、输出比较
  frontend/               # Streamlit 页面与 API 客户端
data/                     # 运行时数据（加入 .gitignore，不提交密钥和大文件）
tests/                    # API、权限、评测器和前端冒烟测试
```

建议使用 SQLite 保存用户、Session、提交、测试点日志、审计记录和模型配置；题目可按题目一个 JSON 文件保存。若采用其他存储，需保持 API 契约及可重置能力不变。

核心实体：`User`、`Session`、`Problem`、`Language`、`Submission`、`CaseResult`、`AccessAudit`、`AITask`。提交状态（`pending/success/error`）与测试点结果（`AC/WA/TLE/MLE/RE/CE/UNK`）分开建模。

评测规则：输出不得包含额外提示语；比较时忽略行末空格和最后一行多余换行，其余内容严格匹配。每个测试点计 10 分，总分为通过测试点数乘以 10；未明确归类的评测异常统一归为 `UNK`。题目未设置 `time_limit`/`memory_limit` 时继承语言默认值；详情接口对缺省字符串返回 `""`、列表返回 `[]` 等稳定默认值。C++ 先编译再运行，语言命令中的 `{src}`/`{exe}` 必须展开为有效路径。

## 3. 分阶段开发路线

| 阶段 | 状态 | 主要工作 | 完成判据 |
| --- | --- | --- | --- |
| P0 基础设施 | 已完成 | 环境、配置、目录、统一响应/异常、数据库迁移、日志脱敏、启动脚本 | 本地可启动，健康检查和 reset 可用 |
| P1 Step 1 | 已完成 | 题目模型、JSON 仓库、CRUD API、字段默认值和冲突校验 | 登录后可完整维护题目，非法请求返回 400/409 |
| P2 Step 2 | 已完成、待提交 | Python/C++ 运行器、输出比较、异步任务、语言注册、超时/内存监控 | 已通过自动化 AC/WA/RE/CE/TLE/MLE 与脱敏测试 |
| P3 Step 3 | 已完成、待提交 | 列表筛选分页、详情权限、管理员 rejudge | 已验证原 ID pending→success/error、字段裁剪和统计一致性 |
| P4 Step 4 | 已完成 | bcrypt 密码、Session 登录登出、角色依赖、禁用用户、用户接口 | 未登录 401、越权 403，初始 `admin/admintestpassword` 自动创建 |
| P5 Step 5 | 未开始 | CaseResult 日志、题目 `public_cases`、访问审计接口 | 本人/管理员/公开场景可见性与审计状态正确 |
| P6 Step 6 | 未开始 | Streamlit 三组页面、统一 API 客户端、会话和轮询 | 页面不绕过 API，能完成注册→建题→提交→看结果闭环 |
| P7 Advance（可选） | 未开始 | AI 配置、后台任务、进度/SSE 或轮询、取消、用量计费、结果导入题目表单 | 任务可观察、可中断，密钥不回显，费用依据可解释 |
| P8 验收 | 未开始 | 集成测试、边界/安全测试、演示数据、报告和提交检查 | 覆盖评分点，Conventional Commits，9 月 10 日前提交 commit 与报告 |

开发顺序应保持依赖关系：先后端契约和权限，再评测器，最后前端；每阶段完成后打可回滚的 Conventional Commit（如 `feat(judge): add timeout monitor`）。

## 4. API 与权限检查清单

- 题目：`/api/problems/` CRUD；新增/编辑/列表/详情需登录，删除仅管理员。
- 评测：`/api/submissions/` 提交、列表、详情、`/{id}/rejudge`；提交需登录，详情本人或管理员，重判仅管理员；提交频率超限返回 429。
- 语言：`/api/languages/` 注册和查询；注册需登录，命令模板必须校验并限制执行范围。
- 用户：`/api/users/` 注册/列表、`/api/users/{id}` 信息、`/role` 角色；登录、登出位于 `/api/auth/*`。
- 日志：`/api/submissions/{id}/log`、题目日志可见性、`/api/logs/access/`；记录成功及权限拒绝访问。
- AI：模型配置、任务创建/状态/events/cancel；任务创建者或管理员可访问。

统一处理参数校验（FastAPI 默认 422 转为 400）、认证顺序（401→403→400→429→409→404→500）和安全错误信息。所有 API 响应必须包含 `{code,msg,data}`，且 `code` 与 HTTP 状态码一致。提交频率限制为 1 分钟内最多 3 次，超出返回 429。

分页和筛选约束：提交列表的 `user_id`、`problem_id` 是一级条件，不能同时为空；`page` 与 `page_size` 都为空表示查询全部，仅提供 `page_size` 表示第一页，仅提供 `page` 属于 400。未提供 `user_id` 时，管理员可查看题目下所有用户提交，普通用户只能查看自己的提交；`pending`/`error` 列表项只需返回 submission_id 和 status。`POST /api/reset/` 仅用于测试环境恢复数据，不计入 Step 6 评分；重置须清空测试数据、退出当前会话并重新创建初始管理员。

日志权限有两层：未公开时普通用户只能查看自己的日志，管理员可查看全部；题目设置 `public_cases=True` 后，所有已登录用户可查看该题测试点明细，但无权查看 Step 2/3 简单结果的用户仍不能通过其他接口读取这些结果。审计记录包含用户、题目、操作、时间和访问状态；未登录、提交不存在或参数错误的请求不要求写入审计。

## 5. 测试与验收策略

1. **单元测试**：字段校验、分页、权限依赖、密码哈希、输出标准化、费用公式。
2. **评测器测试**：Python/C++ 的 AC、WA、RE、CE、TLE、MLE；编译超时和进程清理。
3. **API 集成测试**：匿名/普通用户/管理员/禁用用户矩阵，检查 HTTP 状态码和响应结构。
4. **前端冒烟测试**：注册登录、题目 CRUD、提交并轮询结果、日志可见性。
5. **安全检查**：命令注入、路径穿越、代码/密钥日志泄露、越权读取、资源耗尽。

验收演示建议按一条主线准备：管理员登录并建题→普通用户注册登录→提交 Python/C++（含 AC、WA、RE、CE、TLE、MLE 样例）→查看提交和日志→管理员重判/改角色→（可选）AI 生成并导入题目。报告需说明架构、关键难点、测试结果及 AI 使用情况。

## 6. 运行与提交约定

具体命令以最终实现为准，至少提供 FastAPI（如 `uvicorn app.main:app --reload`）和 Streamlit（如 `streamlit run app/frontend.py`）启动方式及依赖文件。运行时数据、编译产物、模型密钥和大文件不得提交 Git；提交信息遵循 Conventional Commits。截止节点以指南为准：9 月 10 日课前完成代码并提交最后一次 commit，报告于当日 23:59 前提交。

AI 进阶输入应能表达知识点、预期难度和其他约束，结果须可用于题目新增/编辑，不能只返回脱离系统的压缩包或文本。任务状态至少区分等待、执行、完成、中断、失败；进度必须在执行期间持续展示，取消必须真实终止或阻止后台任务继续执行。模型配置至少包含提供商 URL、模型名、密钥并实际生效；输入/输出 Token 应分别统计，费用公式、计价单位及估算限制需展示。工具调用、Agent Loop、检索和脚本生成均为可选设计。

详细接口字段和异常以 [`oj/api.md`](oj/api.md) 为准；遇到歧义，先保持该契约，再在本文档记录设计决策。
