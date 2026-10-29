# 青鸟 AI 获客工作台

证据驱动的客户采集、需求判断、人工审核和持久化发送队列。当前为独立测试环境，**真实发送默认关闭且全局暂停**。

- 前端：React / TypeScript；后端：FastAPI / SQLAlchemy。
- 数据：PostgreSQL；调度：Redis / Celery；单一应用镜像。
- 已实现：HN 官方公开来源、规则筛选/去重、客户档案、商务联系方式证据、可配置 AI 分析、事实模板草稿、版本绑定审核、队列、拒收/暂停/人工接管、模拟发送、SMTP 适配器、成本预占和审计。
- 未接通：真实模型服务、SMTP 账号、收件同步、转化回传。模拟结果不表示真实发送；SMTP 已提交不表示送达。

维护入口：[AGENTS.md](AGENTS.md) → [.agent/state.json](.agent/state.json) → [.agent/latest-run.md](.agent/latest-run.md)。

[产品与验收](docs/product-plan.md) · [渠道证据](docs/channels.md) · [部署说明](docs/deployment.md)

## 本地验证

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements.lock
Set-Location backend
..\.venv\Scripts\python -m pytest -q
Set-Location ../frontend
npm ci
npm run build
```

## 服务器试运行

复制 `.env.example` 为服务器 `.env`，生成不同的运维/人工审核令牌与数据库密码；不提交密钥。首次执行 `python scripts/prepare-staging.py` 可生成隔离配置，随后 `docker compose build web`、`docker compose up -d`。

Web 默认绑定 `127.0.0.1:18080`。测试服务器可在私有 `.env` 设置 `HUOKE_BIND_ADDRESS=0.0.0.0`，通过 `http://117.72.35.216:18080` 访问；公网入口仍需要审核令牌登录，开发任务不得代用户批准消息。Windows 用户也可运行 `./scripts/open-workbench.ps1 -CopyReviewToken` 使用 SSH 隧道访问。

独立 PostgreSQL 并发集成测试通过 `TEST_DATABASE_URL` 指向专用、可重建的 `huoke_test` 数据库运行。测试不会向真实收件人发送消息。
