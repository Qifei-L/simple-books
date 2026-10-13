# Simple Books

轻量、自托管的记账应用。前端基于 Refine 官方 **auth-antd** 模板，使用 Ant Design 白色界面；后端使用 FastAPI、FastAPI Users、Python Accounting 和 PostgreSQL。

## 第一版范围

- 一个账套，USD 本位币，Admin / Accountant / Viewer 三个固定角色。
- 科目表、科目余额、账户流水 API、日记账及反向冲销。
- 未知入账挂到权益类 Suspense；查明后追加重新分类分录，保留原记录。
- 客户发票、供应商账单、客户收款、供应商付款、部分核销。
- 明细填写描述、科目、金额和可选数量，不需要商品 Item。
- 利润表、资产负债表、试算平衡表、现金流量表；日期选择、CSV 导出、浏览器打印。
- 管理员创建用户、调整角色、停用用户；用户修改密码。
- 会话存于数据库，浏览器使用 HttpOnly Cookie；退出、修改密码、角色变化会撤销会话。
- 过账/核销接口强制 Idempotency-Key；写入事务及审计记录。

**这是可运行的第一版，不是已经审计的生产会计系统。** 暂未实现税金页面、发票 PDF、贷项通知单、发票/账单冲销、银行流水导入、银行自动对账、附件、备份任务、期间关闭界面和跨年报表。所有创建交易操作直接过账，暂不支持草稿。报表日期限定同一自然年。账龄及发票到期日分析后续添加。多币种和多账套不在当前版本范围。

## 本地运行

Python 3.12、Node.js 22。SQLite 仅用于本地演示/测试；正式部署使用 PostgreSQL。

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

export ADMIN_EMAIL=your-email@example.com
export ADMIN_PASSWORD='set-a-unique-password-at-least-12-characters'
export COOKIE_SECURE=false
# 可选：export DATABASE_URL='postgresql+psycopg://user:password@localhost/books'
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

PowerShell 使用 `$env:ADMIN_EMAIL='...'` 等设置环境变量。启动时仅创建空白 Demo Company 和基础科目，不自动生成业务交易。管理员仅在该邮箱不存在时创建，已有密码不会被启动变量覆盖。

另开终端：

```sh
cd web
npm ci
npm run dev
```

打开终端显示的 Vite 地址，登录后开始录入。开发代理将 /api 请求转发到本机 8000。API 文档：`http://127.0.0.1:8000/docs`。

## Docker / Railway

Docker 使用多阶段构建，网页编译产物由 FastAPI 提供，无需独立 Gateway、Redis 或 Worker。

```sh
# 将 .env.example 复制为 .env，填写 ADMIN_EMAIL、ADMIN_PASSWORD、DB_PASSWORD
docker compose up --build
```

Docker Compose 包含应用和 PostgreSQL，打开 `http://localhost:8000`。

Railway：连接本仓库创建应用，再添加 PostgreSQL。设置：

- DATABASE_URL：引用 PostgreSQL 内网连接 URL。
- ADMIN_EMAIL / ADMIN_PASSWORD：自己的管理员邮箱与唯一密码（至少 12 字符）。
- COOKIE_SECURE=true（公网 HTTPS 必须开启）。
- COMPANY_NAME：账套名称。

Dockerfile 接受 Railway 的 PORT；健康检查 /health。不默认部署或配置备份。正式使用前自行启用并测试数据库备份、恢复，并先核对样例账务。应用仅运行一个进程；数据库写入以账套行锁串行化，确保流水编号和重复提交检查一致。

## 角色

| 角色 | 权限 |
|---|---|
| Admin | 全部账务操作及用户/角色管理、审计日志 |
| Accountant | 科目、过账、冲销、核销、分类、报表 |
| Viewer | 查看、导出报表及流水 |

全部角色共用同一个账套。后端执行权限检查，前端菜单/按钮仅作提示。没有公开注册、OAuth、邮箱发送或自助找回密码页面。

## API 调用

先 POST `/api/auth/login`，表单字段 username、password，保存响应 Cookie。写入请求均需要 `X-Requested-With: SimpleBooks`；过账、核销、分类、冲销还需要 `Idempotency-Key`。同一个逻辑操作在失败重试时复用相同 key 和内容；相同 key 用于不同内容返回 409。

- GET /api/accounts、/api/transactions、/api/accounts/{id}/statement
- POST /api/journals、/api/documents、/api/assignments
- POST /api/suspense、/api/suspense/{id}/resolve
- POST /api/journals/{id}/reverse
- GET /api/reports/{income|balance|trial|cashflow}?start=YYYY-MM-DD&end=YYYY-MM-DD
- GET /api/export/transactions、/api/export/reports/{name}
- GET /api/admin/audit（管理员）

认证使用可撤销数据库会话，无需向网页暴露 API 密钥。跨站请求防护使用 Strict SameSite Cookie 和写入请求校验头；应用不开放跨域 CORS。角色调整会撤销被修改用户所有会话。

FastAPI Users 的用户/令牌适配器使用 SQLAlchemy Core，避免 Python Accounting 全局 ORM Session 事件影响认证表。账务在独立 AccountingSession 中执行，但共用一个 PostgreSQL 数据库。AtomicSession 将库内部的 commit 延迟为 flush，直到完整 API 操作成功才统一提交，避免分录已入账而审计/重复提交状态未保存。

## 验证

```sh
python -m pytest -q
cd web && npm run build
```

GitHub Actions 使用 PostgreSQL 16 验证未知入账/分类、部分核销与超额核销回滚、重复提交、借贷平衡/冲销、角色限制、会话撤销；另一个 job 检查前端类型和构建。测试仅允许重置 `books_test` PostgreSQL 数据库或本地 `test-books.db`，不要指向真实账套。

前端依赖锁定在 package-lock.json。path-to-regexp 覆盖到修复版本 8.4+，避免上游后台布局依赖带入已知漏洞版本。

## 上游及许可

来源和模板改动说明见 NOTICE.md。MIT 许可见 LICENSE。FastAPI Users 目前处于维护模式；本项目只使用其现有用户管理和会话能力。
