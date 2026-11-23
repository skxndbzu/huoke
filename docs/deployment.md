# 部署与发布

## Windows 公开采集进程恢复

在运行采集器的 Windows 用户会话中执行 `scripts/install-public-executor-recovery.ps1`，安装名为 `Huoke Public Collector Recovery` 的任务。任务使用当前用户的交互会话和普通权限，在登录后及每隔五分钟检查专用 SSH 隧道、NodeSeek、V2EX 与 GitHub Discussions。采集间隔继续由各执行器负责。重复安装更新同名任务。电脑需要保持在线且用户已登录。

此电脑用 `-FeedProxy http://127.0.0.1:7897` 安装。该参数仅用于 NodeSeek 和 V2EX 的公开来源请求。系统代理关闭时仍可使用此入口，前提是本地代理在运行。工作台经项目专用 SSH 隧道访问。

可在任务计划程序暂停这个任务，或者用 `Disable-ScheduledTask -TaskName 'Huoke Public Collector Recovery'` 暂停自动恢复。计划停止采集进程前先停用恢复任务。再次运行安装脚本可启用恢复。

## Windows NodeSeek 补采试点

NodeSeek 在成功读取并上传非空官方 RSS 后等待 10 分钟。读取失败、空订阅或停用时按原每小时检查。上游返回 `Retry-After` 时等待至少其指定时间。工作台传输失败仍复用待上传批次，按 30 秒重试。

Windows 只重启 NodeSeek 执行器，服务器沿用下方版本。回滚时恢复 `e60441d` 中的 `scripts/social-executor.py` 并仅重启 NodeSeek 执行器。八种模拟执行器路径和 `Retry-After` 日期解析通过。

## 当前发布 `20261123-supplier-submissions-r1`

初筛识别显式广告标签和正文开头的模型 API 站点收录投稿。未处理的同类历史记录可从「全部市场（含历史）」查看。99 项定向和 149 项隔离 PostgreSQL 测试、390px 工作台及线上运维接口验证通过。回滚镜像为 `huoke:20261121-provider-evaluation-r1`。数据库和私有配置备份为 `backups/20261123-before-supplier-submissions.dump` 和 `backups/20261123-before-supplier-submissions.secrets.tar`。

## 上次发布 `20261121-provider-evaluation-r1`

GitHub 默认查询将「API 求助」一组换为「API 盲测」。现用默认查询自动迁移，已有自定义组继续保存。初筛收录标题明确比较多家模型 API 且正文给出评测指标的帖子。61 项定向测试、146 项隔离 PostgreSQL 测试、隔离库两次真实采集通过。回滚镜像为 `huoke:20261121-model-call-triage-r2`，发布前数据库和私有配置备份为 `backups/20261121-before-provider-evaluation.dump` 与 `backups/20261121-before-provider-evaluation.secrets.tar`。如撤回试点，回滚镜像后在渠道配置恢复「API 求助」一组，保留后续自定义配置。

## 上次发布 `20261121-model-call-triage-r2`

初筛识别常见模型调用失败，并要求只出现「中转鸡」等 VPS 称呼的帖子有其他模型证据。采集缓存记录规则版本，下次正常采集时重筛缓存过的内容，并保留正文未变的既有 AI 分析。57 项定向测试、143 项隔离 PostgreSQL 测试、真实 GitHub 采集与 NodeSeek 原帖回放通过。即时回滚镜像为 `huoke:20261121-model-call-triage-r1`。发布前数据库和私有配置备份为 `backups/20261121-before-model-call-triage-r2.dump` 与 `backups/20261121-before-model-call-triage-r2.secrets.tar`。

## 上次发布 `20261121-github-issue-state-r2`

GitHub 现有采集任务每轮最多核对 5 条国内待跟进线索，同一原帖至少间隔一天。新增 `github_issue_states` 表记录平台状态与核对时间，客户池用两日内的关闭、不可用和链接换帖结果调整同一联系优先级内的顺序。链接换帖及 HTTP 410 隐藏原帖跳转入口。44 项定向、138 项隔离 PostgreSQL 测试和 390px 界面验证通过。发布前数据库和私有配置备份为 `backups/20261121-before-github-issue-state-r2.dump` 与 `backups/20261121-before-github-issue-state-r2.secrets.tar`。即时回滚镜像为 `huoke:20261121-github-issue-state-r1`，如需撤回整个功能可用 `huoke:20261121-domestic-demand-r1`，保留新增表。

## 上次发布 `20261121-domestic-demand-r1`

国内客户池及待核实队列在同一联系优先级内按 AI 采购阶段排序。列表和详情展示已知的阶段标签。36 项定向测试、137 项隔离 PostgreSQL 测试、390px 界面及线上真实排序通过。回滚镜像为 `huoke:20261121-nodeseek-window-r1`，数据库和私有配置备份分别为 `backups/20261121-before-domestic-demand.dump` 与 `backups/20261121-before-domestic-demand.secrets.tar`。

## 上次发布 `20261121-nodeseek-window-r1`

NodeSeek 渠道卡显示成功采集批次中已知原帖发布时间的最早值和最晚值。筛除及缓存命中计入批次，有缺失时间时显示可统计条数。数据存在现有运行记录的 JSON 中。24 项定向测试、136 项隔离 PostgreSQL 测试、390px 官方 RSS 回放及 39 个线上文件摘要验证通过。数据库备份为 `backups/20261121-before-nodeseek-window.dump`，私有配置备份为 `backups/20261121-before-nodeseek-window.secrets.tar`，回滚镜像为 `huoke:20261121-screening-history-r1`。

## 上次发布 `20261121-screening-history-r1`

渠道与任务页展示近 30 天样本复核汇总及最近 12 条有判断的记录。修改和撤回即时反映到汇总。沿用现有事件表。隔离 PostgreSQL 135 项测试与手机界面验证通过。回滚镜像 `huoke:20261121-screening-feedback-r1`，备份 `backups/20261121-before-screening-history.dump`。

## 上次发布 `20261121-screening-feedback-r1`

所有者可在公开来源筛除样本上保存、撤回复核判断，写入现有审计记录。135 项隔离 PostgreSQL 测试、前端构建、390px 保存和撤回验证通过。无结构变更。回滚 `huoke:20261121-analysis-context-r1`，备份 `backups/20261121-before-screening-feedback.dump`。

## 上次发布 `20261121-analysis-context-r1`

模型保留原上下文并补入遗漏的规则原句，同时压缩无语义的 JSON 空白。从已有 40 次分析回放验证 138 段引文完整和补齐 4 段。实际调用费用待后续测量。隔离 PostgreSQL 134 项测试通过，线上健康检查正常。回滚镜像 `huoke:20261121-roundup-filter-r1`，备份 `backups/20261121-before-analysis-context.dump`。

## 上次发布 `20261121-roundup-filter-r1`

需求规则排除明确标记的资源推荐和投稿。132 项隔离 PostgreSQL 测试通过，线上三条真实标题回放与预期相符。无结构变更。回滚 `huoke:20261121-source-cooldown-r1`，备份 `backups/20261121-before-roundup-filter.dump`，保留调度卷。

## 上次发布 `20261121-source-cooldown-r1`

服务器公开采集在定时触发碰到数据库冷却，且剩余时间不超过两分钟时，允许在冷却完成后补跑一次。数据库仍按平台限制一小时最多一次访问。网络失败、限流、长冷却沿用原行为。独立 Redis 与真实 Celery worker 用模拟来源验证了三秒冷却后约四秒补跑，隔离 PostgreSQL 129 项通过。持续使用独立调度卷。回滚镜像 `huoke:20261121-screening-review-r1`，备份 `backups/20261121-before-source-cooldown.dump`。

## 上次发布 `20261121-screening-review-r1`

公开来源筛除样本按不同原因抽取，后续空批或全缓存批次保留最近的样本时间与来源。线上已核对四个公开来源真实返回记录。127 项隔离 PostgreSQL 测试和 390px 界面验证通过。回滚镜像 `huoke:20261121-beat-state-r1`，备份 `backups/20261121-before-screening-review.dump`。继续保留 Celery beat 的专用数据卷。

## 上次发布 `20261121-beat-state-r1`

Celery beat 使用命名卷 `huoke-staging_beat_state` 持久保存 `/var/lib/huoke/beat/celerybeat-schedule`。通过 Dockerfile 初始化 UID 10001 可写目录。保留单实例，发布和回滚不清理该卷。首次迁移在正常停止 beat 后复制旧调度文件，并在新卷初始化，避免发布从头计时。

隔离环境两次启动验证同一卷保留计数、最后运行时间与剩余等待时间。线上迁移前后 Gitee/GitHub 的调度时间一致，Web/PostgreSQL 健康，worker 正常响应。后端、测试、网页 39 个摘要与此前通过 126 项测试的版本一致。回滚 `huoke:20261121-discussions-r1`，数据库与调度备份分别为 `backups/20261121-before-beat-state.dump` 和 `backups/20261121-before-beat-state.schedule`。

2026-11-21 12:29 原时间段自然触发 GitHub 和 Gitee，均成功写入运行记录。

## Windows 公开采集自动重连

Windows 采集器的轮询等待以 30 秒为上限分段核对实际时间。模拟系统挂起后时钟跳跃和回拨均通过，不修改 Windows 电源设置。进程在系统恢复后按到期时间继续下一轮。

`scripts/start-public-executors.ps1` 在当前 Windows 会话保留一个专用 SSH 守护进程。隧道中断后重连，并检查 18081 端口归属。每个平台仍只运行一个采集器。工作台网络中断时 30 秒后重试，未确认的上传沿用同一批缓存，成功后恢复正常采集间隔。来源平台本身的错误沿用原轮询间隔。启动时读取运维凭据失败也会重试，凭据仅用于项目专用隧道。

2026-11-21 受控终止专用 SSH 后，守护进程在 11.6 秒内恢复了工作台健康接口。重新启动三条公开采集器后均成功上传，GitHub Discussions 一次工作台协议错误自动恢复。`.local/social-executor/tunnel-watch.log` 记录重连过程。Windows 重启后按原方式启动采集器。

## 上次发布 `20261121-discussions-r1`

GitHub Discussions 小范围公开订阅通过 Windows 本地执行器验证，使用 `scripts/start-public-executors.ps1 -Platform github_discussions`。无需 GitHub 登录，电脑在线时每小时一轮，GitHub Issues 仍走服务器。首次读取 50 条，全部规则筛除。隔离 PostgreSQL 126 项测试、前端构建、线上 39 个文件摘要及公开健康接口验证通过。回滚镜像 `huoke:20261121-v2ex-profile-r1`，备份 `backups/20261121-before-discussions.dump`。本轮无数据库结构变更。

## 上次发布 `20261121-v2ex-profile-r1`

Windows V2EX 执行器每轮从近 90 天通过需求初筛的 V2EX 线索最多补查三个公开作者资料。成功或非暂时可用结果缓存一天。同一作者避免重复请求。服务端核对采集配置版本及线索作者，保留现有客户和审核记录。`scripts/start-public-executors.ps1 -Platform v2ex` 可单独恢复 V2EX。

工作台在 V2EX 详情显示作者资料和该资料自行声明的 GitHub 入口。明确公开的联系行可成为待核实候选。已有市场分析不自动重跑，后续正常分析只加入公开资料中相关的所在地字段。无数据库结构变更。125 项隔离 PostgreSQL 测试、前端构建及 390px 客户详情测试通过。回滚镜像 `huoke:20261121-source-health-r1`，备份 `backups/20261121-before-v2ex-profile.dump`。

## 上次发布 `20261121-source-health-r1`

Windows V2EX 执行器同日调整采集节点为 `deepseek` 和 `programmer`，并已重启验证成功。每轮仍最多 60 条。此调整由本地执行器运行，服务器沿用当前镜像，旧 V2EX 服务器任务配置照常保留。

渠道卡为 NodeSeek、V2EX 和 Linux.do 分别显示服务器与本地历史。新运行记录在现有 JSON 字段中标明路径，V2EX 服务器沿用既有任务历史。最近一次成功的计数和样本随路径展示，距上次成功超过两小时标记过期。无结构变更。

线上验证 NodeSeek 与 V2EX 均显示服务器连接失败和本地补采成功，Linux.do 显示服务器失败、本地尚无自动读取记录。124 项隔离 PostgreSQL 测试通过，前端构建、390px 状态展示及 39 个线上文件摘要核对通过。发布前备份 `backups/20261121-before-source-health.dump`，回滚镜像 `huoke:20261121-gitee-r1`。

## 上次发布 `20261121-gitee-r1`

Gitee 公开 Issue 搜索已加入 `huoke-staging` 的现有 Celery beat，每小时最多一轮。线上已通过运维接口启用并由 worker 成功执行。无数据库结构变更。备份为 `/home/admin/huoke-staging/backups/20261121-before-gitee.dump`，回滚镜像 `huoke:20261118-social-r2`。执行回滚后复查健康和真实发送开关，保留新增渠道配置与数据。

123 项隔离 PostgreSQL 测试、前端构建与 390px 浏览器验证通过。线上 39 个应用、测试和静态文件摘要匹配，Web/PostgreSQL 健康，Celery worker 正常响应，未认证接口返回 401，真实发送关闭。

本地 NodeSeek 和 V2EX 进程已用更新的执行器恢复补采。仅公开订阅读取采用 Windows 当前的代理配置。私有桥接接口和工作台 SSH 隧道继续直连。电脑休眠或进程停止后，可使用 `scripts/start-public-executors.ps1` 恢复。

## 上次发布 `20261118-social-r2`

2026-11-18 社交只读采集版已发布到现有 staging，仍使用 `http://117.72.35.216:18080`。镜像 `huoke:20261118-social-r2`，SHA256 `7dfd0dd823b0dc328317406b66db83fa9d170ecfa75d5df9d3368fd717ce4f71`。仅新增 `capture_evidence` 表，init create_all 创建，无删除或旧列变更。发布前备份 `backups/20261118-before-social.dump`，r2 前另备份 `backups/20261118-before-social-r2.dump`，权限 600。

最终后端与 122 项 PostgreSQL 测试通过的镜像源文件相同；前端构建/隔离浏览器测试通过。上线后 39 个后端/测试/静态文件 SHA256 匹配；Web/PostgreSQL 健康、worker pong、公网健康 200、未登录 API 401，消息数 0，REAL_SEND_ENABLED=false。回滚将私有环境 HUOKE_IMAGE 恢复 `huoke:20261118-rate100`，使用对应 release 的 Compose 启动并复查健康，保留新增表与数据。没有执行回滚测试。

本地 NodeSeek/V2EX 补采由 `scripts/start-public-executors.ps1` 建立专用 18081 SSH 隧道并启动隐藏进程，每小时一次，需要 Windows 在线；未新增开机任务。读取失败保留状态，上传成功才推进游标；同平台 OS 锁避免重叠。辅助脚本仅读取 OPERATOR_TOKEN，不操作审核令牌。QQ 服务缺上游授权未运行，微信停用，小红书因 Windows 兼容问题停止。更多限制和恢复命令见 [social-connectors.md](social-connectors.md)。

## 已确认边界

用户授权服务器使用、日常开发测试和推送 GitHub。尚未确认生产发布策略，首次只部署独立 staging。真实发送 REAL_SEND_ENABLED=false，数据库默认全局 paused=true。任何涉及权限、审核边界、计费或数据删除的生产改动需要单独确认。不得修改 bluebirdapi.com。

服务器：117.72.35.216，admin，Ubuntu 22.04，16 GB RAM，检查时系统盘可用 79 GB，Docker 29.1.3。SSH 使用已授权的本机用户密钥，密码不保存在仓库。原有 cpr-postgresql18/cpr-redis8 和其他目录不属于本次获客部署，不修改。新服务使用 huoke-staging Compose 项目、新数据卷、私有网络；数据库和 Redis 不发布宿主端口。

获客根目录 `/home/admin/huoke-staging`，环境文件 `.env` 权限 600，发布源码在 `releases/`。Web 默认绑定服务器 127.0.0.1:18080，通过 SSH 隧道访问。若所有者明确要求测试环境公网访问，可在服务器私有 `.env` 设置 `HUOKE_BIND_ADDRESS=0.0.0.0`，入口为 `http://117.72.35.216:18080`；该入口只用于测试，仍须审核令牌登录，正式环境应改用独立域名和 TLS。Bluebird 业务域名不用于未经确认的部署改动。

## 常规命令

Windows：`py -3.12 -m venv .venv`，`.venv\Scripts\python -m pip install -r backend/requirements.lock`；在 backend 运行 `..\.venv\Scripts\python -m pytest -q`。前端在 frontend 运行 `npm ci`、`npm run build`。

后端启动前先 `python -m app.db` 初始化表，必须配置不同的 OPERATOR_TOKEN/REVIEWER_TOKEN。数据库升级后续使用显式迁移；create_all 只负责新表，不负责修改旧列。

服务器：`docker compose --env-file /home/admin/huoke-staging/.env build web`，随后 `docker compose --env-file /home/admin/huoke-staging/.env up -d`。运行前确认工作目录为所选 release。Docker Hub 在本次检查中超时，预备使用公开 ECR Docker 官方镜像副本，镜像摘要应记录在本轮状态。

每次发布先跑测试、构建版本镜像；备份数据库（只导出，不删除）；健康检查、实际来源同步、队列保护检查通过后记录发布版本。失败则恢复上一版本镜像，保留数据卷。首次没有上一版本，不宣称已有回滚验证。

网络慢时的离线构建：本地 `pip download --only-binary=:all: --platform manylinux_2_28_x86_64 --platform manylinux_2_24_x86_64 --platform manylinux2014_x86_64 --python-version 312 --implementation cp --abi cp312 --no-deps -r backend/requirements.lock -d .local/wheels` 下载锁定版本的 Linux wheels。前端构建后 `git add` 所有本轮代码，运行 `python scripts/package-release.py`。包仅含 Git 跟踪源文件、构建后的 web、wheels 和 SHA256 清单，不含 `.env`。上传后校验归档 SHA256，在独立 release 解压，用 `docker build -f deploy/Dockerfile.offline -t huoke:<release> .` 构建。SSH 使用 ServerAliveInterval=20，避免长构建会话闲置断开。

## 所有者访问

2026-11-10：所有者明确要求本轮功能部署并继续使用 IP，无需等待域名。新增账号登录与模型配置页属于已授权范围；不启用真实发送。首次使用审核令牌登录，在“成本与配置 / 账号与密码”设置用户名密码；服务不预设公共密码。会话 12 小时，HttpOnly/SameSite=strict；cookie 写操作核对 Origin。HTTP IP 入口尚无 TLS，SSH 隧道仍可使用。

本轮数据库仅新增表，由 init 的 create_all 创建：lead_workspaces、touchpoints、contact_points、source_policies、owner_logins、login_sessions、login_throttles、model_providers。无旧列修改或删除；发布前备份。回滚使用上一镜像并保留新增表和数据，不删除数据库。

模型密钥：服务器私有目录 `/home/admin/huoke-staging/secrets`，属主容器 UID 10001、目录 700、文件 600；通过 `HUOKE_SECRETS_DIR` 挂载到 web/worker 的 `/app/secrets`。Web 必须可写以支持所有者在页面替换密钥；部署不将密钥打入镜像。备份时需一并备份私有目录，不能只备份数据库。CLI `python -m app.configure_provider` 从 stdin 接受 JSON，输出配置 ID 与验证状态，不输出输入或密钥；不得将密钥作为命令行参数。模型列表验证不消耗推理 Token，推理验证另记实际用量。配置的价格仅用于估算服务费用。

服务器 Celery beat 每小时执行 GitHub 与 V2EX 采集；自动分析按所有者 2026-11-18 要求，每 36 秒调度一条、滚动一小时最多 100 条（见文末当前策略）。所有公开采集通过数据库冷却时间限制重复执行；HTTP 失败不会立即重试。无需本地电脑常开。模型缺失时返回 not_configured，未知费用保留预占；既有失败分析不自动重试。

运行 `scripts/open-workbench.ps1` 建立 SSH 隧道。脚本由用户主动运行时可复制审核令牌到剪贴板用于登录；开发维护任务不得用它代替所有者批准。

真实邮件配置：SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/MODE；用户名和主机必须读取既有配置或由用户提供。先只做 TLS 与认证检查，不发测试邮件给客户。SMTP 真实发送需启用开关、所有者核实商务联系方式、逐条批准内容并恢复队列。

`scripts/configure-smtp.py` 在服务器从 stdin 接收七个 SMTP 字段（含 SMTP_FORCE_LOGIN），原子更新私有 `.env`，不会改写审核令牌或启用发送。当前配置按只读查到的 Bluebird SMTP 设置复制；密钥从 Windows DPAPI 存储经 SSH stdin 传递，不放命令行参数或日志。`python -m app.smtp_check` 仅建立验证证书的 TLS 会话并认证，不发送 MAIL/RCPT/DATA，不需要真实发送开关。认证结果写入 channel_runs，绑定当前配置指纹，24 小时后或配置变化后显示待复核；认证成功不代表邮件提交或送达。不要在无人值守任务里反复认证以免触发邮箱风控，只有配置变化、失败调查或具体需要时运行。

模型配置：LLM_BASE_URL（含 /v1）、LLM_API_KEY、LLM_MODEL、输入/输出每百万 Token 美元单价。没有价格不调用；缺失用量记录为未决。不要将 Bluebird 管理员密码当模型 API 密钥。

## 恢复及限制

2026-11-17 国内市场筛选发布仅新增 `lead_markets` 和 `market_policy` 表，不删除旧客户。init 创建表后，在新镜像执行 `python -m app.market`，按每批 200 条初始化历史市场待评估状态并提取原文公开联系方式；该步骤不调用模型、保留原 AI 结果和人工业务状态。已有 AI 成功分析但缺少新市场字段的记录，允许按原预算补充一次新版本分析；异常/结果未知调用不自动重试。每小时仍最多 2 条，优先有国内业务线索的记录。GitHub 原封不动的旧默认搜索词替换为国内需求关键词，用户自定义词保留。

发布前核实实际容器镜像及 release，不能仅信本地 state。此次发现线上 `20261116-contact-r1` 的联系路径筛选、公开资料查看和指标代码未进入本地 Git，已从该 release 的公开源码三方合并并保留；回滚基准应为此版本而非旧 state 的 `20261110-workflow-r2`。

- 容器 restart=unless-stopped，Celery worker 单进程起步，beat 单实例。
- 发送中超过五分钟的条目进入 unknown；不自动重发。
- 抓取运行租约 15 分钟，过期记中断，下次可恢复；已经成功处理的来源 ID 保留。
- 维护互斥使用 Git common directory 文件 OS 锁，跨 worktree、进程退出即释放；拿不到锁不修改或部署。
- 目前尚需：自动数据库备份、正式 TLS 域名、磁盘/服务/账号告警、生产发布确认；不能宣称达到生产可靠性。

2026-10-27 实测：镜像 huoke:20261027-mvp 启动并通过健康检查；Celery 可响应，真实来源任务成功。单次备份已恢复到独立 huoke_restore_check_20261027，核对 6 条客户。发布文件摘要见 .agent/state.json。后台 SSH 隧道启动曾被自动审批拦截，所有者可手动运行访问脚本。

## 2026-11-18 发布补充

需求优先与公开联系方式补查版本不改数据库结构、不删除历史线索、不改权限或审核规则。新增每小时 source.research_pending 任务，最多 5 个公开账号，独立于每小时最多 2 条 AI 分析；公开补查不调用模型。github_profiles 任务有数据库冷却/运行结果，README 成功和失败均缓存一天。

解压离线包时注意：数据库备份使用 umask 077；源码目录和静态文件需要容器 UID 10001 可读。只将 release 内 backend/web/wheels 的目录设置 755、普通文件设置 644，不修改私有 .env、secrets 或数据库备份。发布前必须以最终非 root 镜像运行独立 huoke_test 检查，避免将仅 root 可读的代码目录上线。

本轮最终发布 20261118-intent-r2，回滚基准为本轮前的 huoke:20261117-market-ai。数据库备份 /home/admin/huoke-staging/backups/20261118-before-intent.dump；无需回滚数据库。前端构建及隔离浏览器检查通过，最终镜像在独立 PostgreSQL huoke_test 中 100 项测试通过。实际发布、来源结果、费用和限制见状态摘要。

## 2026-11-18 当前自动分析速率（所有者明确要求）

自动分析上限从 2 条/小时调整为 **滚动一小时最多 100 条**。Celery beat 每 36 秒投递一个单条分析任务；积压超过 36 秒未开始的调度任务过期，避免恢复后突发堆积。单个任务保留原 180 秒软超时/240 秒硬超时，不使用一次长循环处理 100 条。

数据库 ChannelRun 的 ai_auto 记录负责小时额度和运行租约，跨 worker 重启有效；运行租约 5 分钟，到期标记 interrupted，再按既有 AnalysisCall 状态选择可安全处理的新候选。滚动一小时优先不同作者；失败/结果未知调用仍排除自动重试。预算保护未变：单次 $1、每日 $5、每月 $100。100 是速率上限，不保证候选不足、模型响应慢或预算耗尽时仍达到 100。真实发送保持关闭。

本次发布 20261118-rate100，无数据库结构变更；回滚镜像 huoke:20261118-intent-r2，发布前备份 backups/20261118-before-rate100.dump。最终镜像在独立 PostgreSQL huoke_test 中 105 项测试通过，包括 100 次上限、第 101 次阻止、滚动窗口恢复、重叠任务、过期租约和预算保护。前端已同步显示 100 条/小时及预算约束。
