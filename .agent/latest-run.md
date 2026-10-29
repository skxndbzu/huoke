# 2026-10-27 首轮：隔离测试环境上线

本地原为空目录，克隆远端 main（仅 README），独立分支 codex/initial-mvp-20261027 开发。服务器 Ubuntu 22.04 / 16 GB；保留原服务，新增隔离容器网络和数据卷。

已开发 React/FastAPI/PostgreSQL/Redis/Celery 基础闭环；版本绑定审核、幂等队列、复制草稿防重复、暂停/拒收/人工接管、未知结果不重发、成本预占与审计。31 个本地测试通过（一个 PostgreSQL 专属用例跳过）；服务器最终镜像内 32 项全部通过，包括 4 并发抢占只产生一次模拟发送。前端构建、npm 官方审计（0 已知漏洞）、运维界面与原文证据浏览通过。

真实 HN 样本：本地及服务器均读取 26 条，20 条过滤，6 条待核实候选；第二次 26 条全部命中缓存，条目请求由 26 降为 0（仍有一个列表请求）。包含讨论、教程等可能的误判，尚无人工标签，不能宣称有效客户或转化改善。没有付费模型调用，没有向客户发送任何消息；开发费用未取得计费数据，记未知。

Bluebird 只读浏览公开首页和价格页，45 个模型及分组动态价格，未作可用性/报价承诺。登录要求接受协议，等待所有者明确确认，未登录、未修改网站。SMTP 密钥已保存在用户目录 `.codex/secrets/huoke/smtp-password.dpapi.xml`，不能输出或入 Git。

每日 09/12/15/18/21 时续跑已创建，ID `automation`。维护 OS 文件锁保存在 Git common directory，进程退出自动释放。不得重复建任务。

服务器已部署 `/home/admin/huoke-staging/releases/20261027-mvp`，镜像 `huoke:20261027-mvp`；精确镜像 ID 与包 SHA256 见 state.json。离线发布解决 Docker Hub 不通、在线 pip 过慢及 SSH 闲置断开问题；包有文件 SHA256 清单，部署后的应用代码逐文件核对通过。Web healthy、PostgreSQL healthy、Celery ping 成功，应用接口发起的真实采集任务已完成。Redis、worker、beat 正常运行。日志有大小轮转；获客数据库与 Redis 未暴露宿主端口。

服务器全局 paused=true、REAL_SEND_ENABLED=false。运维令牌被实测禁止恢复发送。已导出 `/home/admin/huoke-staging/backups/20261027-initial.dump`，恢复到独立 `huoke_restore_check_20261027` 数据库并核对 6 条客户；尚未配置自动备份/监控，不宣称生产可靠性。

访问仅绑定服务器 127.0.0.1:18080。本地后台 SSH 隧道启动被自动审批拦截（未提供细化原因），未通过其他途径绕过。所有者可手动运行 `scripts/open-workbench.ps1 -CopyReviewToken`。截图 `.local/workbench-preview.png` 来自已验证的本地预览，不是外网部署入口。

下一轮：获得登录条款确认后只读核实 SMTP，配置并小样本验证模型；优先验证一个国内开发者来源并进行人工标注。还需正式域名和生产发布策略、收件同步、转化事件、自动备份与告警。观察到切换页面会保留滚动位置，记入下轮小型 UI 修复，不把计划写成已完成。

## 2026-10-27 SMTP 只读接入验证

所有者明确授权：常规查询自主完成，国内客户目标保留，真实发送暂不启用。已登录 Bluebird 控制台，只读查看系统信息、公开模型广场和邮件设置；没有点击保存、修改任何网站字段、测试发送或读取/写入其他设置。发现网站协议写明不向中国大陆地区用户提供服务，已作为业务冲突阻塞记录。

只读取得 SMTP：smtp.qq.com:587、STARTTLS、强制 AUTH LOGIN、TLS 证书校验开启。配置通过服务器私有 `.env` 安全写入，REAL_SEND_ENABLED=false、全局队列 paused=true。新增认证探测模块：仅 EHLO → STARTTLS → EHLO → AUTH LOGIN → close；不执行 MAIL、RCPT、DATA，不使用收件人或正文。

新版服务器镜像 `huoke:20261027-smtp` 已部署，应用健康；镜像内 38 项测试通过（本地 37 项，服务器 PostgreSQL 38 项）。SMTP 探测成功，messages_submitted=0，发送能力仍未验证，消息表为 0。认证失败只记录异常类型和配置指纹，不记录密码或供应商响应正文。配置变化或 24 小时后状态会回到待验证。

本轮没有客户消息、模型调用或新增费用。当前下一步是模型接入和国内来源质量标注；在网站地区政策与国内目标冲突解决前继续研究模式，禁止真实发送。

## 2026-10-29 公网测试入口变更

按所有者要求将测试服务改为可选公网绑定：compose.yml 新增 HUOKE_BIND_ADDRESS，本地默认仍为 127.0.0.1；服务器私有 .env 已设置 HUOKE_BIND_ADDRESS=0.0.0.0，发布 20261029-public 镜像已启动，服务器本机 /healthz 返回正常，真实发送仍关闭且队列暂停。

从外部 Windows 网络访问 117.72.35.216:18080 的 TCP 探测超时；服务器 Docker 已显示 0.0.0.0:18080->8000，本机和容器健康检查通过，UFW 为 inactive。推断仍有京东云安全组/上游 ACL 未放行 18080，需要所有者在云控制台允许入站 TCP 18080 后再复测。数据库备份保存为 /home/admin/huoke-staging/backups/20261029-before-public.sql.gz。

本轮没有客户消息、模型调用或新增费用。公网入口是临时 HTTP 测试入口，正式使用需域名与 TLS。
