# 部署与发布

## 已确认边界

用户授权服务器使用、日常开发测试和推送 GitHub。尚未确认生产发布策略，首次只部署独立 staging。真实发送 REAL_SEND_ENABLED=false，数据库默认全局 paused=true。任何涉及权限、审核边界、计费或数据删除的生产改动需要单独确认。不得修改 bluebirdapi.com。

服务器：117.72.35.216，admin，Ubuntu 22.04，16 GB RAM，检查时系统盘可用 79 GB，Docker 29.1.3。SSH 使用已授权的本机用户密钥，密码不保存在仓库。原有 cpr-postgresql18/cpr-redis8 和其他目录不属于本次获客部署，不修改。新服务使用 huoke-staging Compose 项目、新数据卷、私有网络；数据库和 Redis 不发布宿主端口。

获客根目录 `/home/admin/huoke-staging`，环境文件 `.env` 权限 600，发布源码在 `releases/`。Web 仅绑定服务器 127.0.0.1:18080，通过 SSH 隧道访问；有独立域名及 TLS 配置后才开放正式入口。Bluebird 业务域名不用于未经确认的部署改动。

## 常规命令

Windows：`py -3.12 -m venv .venv`，`.venv\Scripts\python -m pip install -r backend/requirements.lock`；在 backend 运行 `..\.venv\Scripts\python -m pytest -q`。前端在 frontend 运行 `npm ci`、`npm run build`。

后端启动前先 `python -m app.db` 初始化表，必须配置不同的 OPERATOR_TOKEN/REVIEWER_TOKEN。数据库升级后续使用显式迁移；create_all 只负责新表，不负责修改旧列。

服务器：`docker compose --env-file /home/admin/huoke-staging/.env build web`，随后 `docker compose --env-file /home/admin/huoke-staging/.env up -d`。运行前确认工作目录为所选 release。Docker Hub 在本次检查中超时，预备使用公开 ECR Docker 官方镜像副本，镜像摘要应记录在本轮状态。

每次发布先跑测试、构建版本镜像；备份数据库（只导出，不删除）；健康检查、实际来源同步、队列保护检查通过后记录发布版本。失败则恢复上一版本镜像，保留数据卷。首次没有上一版本，不宣称已有回滚验证。

网络慢时的离线构建：本地 `pip download --only-binary=:all: --platform manylinux_2_28_x86_64 --platform manylinux_2_24_x86_64 --platform manylinux2014_x86_64 --python-version 312 --implementation cp --abi cp312 --no-deps -r backend/requirements.lock -d .local/wheels` 下载锁定版本的 Linux wheels。前端构建后 `git add` 所有本轮代码，运行 `python scripts/package-release.py`。包仅含 Git 跟踪源文件、构建后的 web、wheels 和 SHA256 清单，不含 `.env`。上传后校验归档 SHA256，在独立 release 解压，用 `docker build -f deploy/Dockerfile.offline -t huoke:<release> .` 构建。SSH 使用 ServerAliveInterval=20，避免长构建会话闲置断开。

## 所有者访问

运行 `scripts/open-workbench.ps1` 建立 SSH 隧道。脚本由用户主动运行时可复制审核令牌到剪贴板用于登录；开发维护任务不得用它代替所有者批准。

真实邮件配置：SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/MODE；用户名和主机必须读取既有配置或由用户提供。先只做 TLS 与认证检查，不发测试邮件给客户。SMTP 真实发送需启用开关、所有者核实商务联系方式、逐条批准内容并恢复队列。

`scripts/configure-smtp.py` 在服务器从 stdin 接收七个 SMTP 字段（含 SMTP_FORCE_LOGIN），原子更新私有 `.env`，不会改写审核令牌或启用发送。当前配置按只读查到的 Bluebird SMTP 设置复制；密钥从 Windows DPAPI 存储经 SSH stdin 传递，不放命令行参数或日志。`python -m app.smtp_check` 仅建立验证证书的 TLS 会话并认证，不发送 MAIL/RCPT/DATA，不需要真实发送开关。认证结果写入 channel_runs，绑定当前配置指纹，24 小时后或配置变化后显示待复核；认证成功不代表邮件提交或送达。不要在无人值守任务里反复认证以免触发邮箱风控，只有配置变化、失败调查或具体需要时运行。

模型配置：LLM_BASE_URL（含 /v1）、LLM_API_KEY、LLM_MODEL、输入/输出每百万 Token 美元单价。没有价格不调用；缺失用量记录为未决。不要将 Bluebird 管理员密码当模型 API 密钥。

## 恢复及限制

- 容器 restart=unless-stopped，Celery worker 单进程起步，beat 单实例。
- 发送中超过五分钟的条目进入 unknown；不自动重发。
- 抓取运行租约 15 分钟，过期记中断，下次可恢复；已经成功处理的来源 ID 保留。
- 维护互斥使用 Git common directory 文件 OS 锁，跨 worktree、进程退出即释放；拿不到锁不修改或部署。
- 目前尚需：自动数据库备份、正式 TLS 域名、磁盘/服务/账号告警、生产发布确认；不能宣称达到生产可靠性。

2026-10-27 实测：镜像 huoke:20261027-mvp 启动并通过健康检查；Celery 可响应，真实来源任务成功。单次备份已恢复到独立 huoke_restore_check_20261027，核对 6 条客户。发布文件摘要见 .agent/state.json。后台 SSH 隧道启动曾被自动审批拦截，所有者可手动运行访问脚本。
