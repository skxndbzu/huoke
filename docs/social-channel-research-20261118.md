# 国内社交渠道开源项目核查（2026-11-18）

目标：从国内开发者的实际需求中找客户，尽量保留可联系的账号和原始证据；由所有者审核每条具体消息后联系。群成员身份、公开评论入口或技术讨论本身都不代表可私聊或有采购意愿。

本轮通过 GitHub REST API、默认分支提交、README、许可证和部分接口源码核查 20 个仓库。下文日期为北京时间；“活跃”指可见维护活动，不代表已在我们的账号上通过测试。未安装或运行第三方项目，未读取个人/群聊，未发送消息。所有新渠道仍是**待验证**。精简机器证据见 [channel-research-evidence-20261118.json](channel-research-evidence-20261118.json)。

## 选型结果

| 平台 | 项目与近期维护证据 | 已查明的能力 | 对本项目的结论 |
| --- | --- | --- | --- |
| QQ | [LuckyLilliaBot / LLBot](https://github.com/LuckyLilliaDev/LuckyLilliaBot)，旧 LLOneBot 地址重定向；默认分支 11-11 合并消息接收修复，仓库 push 11-17 | 当前源码仍有 OneBot11、Milky、Satori 目录；Milky 私聊/群聊接收事件、私聊/群聊发送接口存在 | **优先做协议适配验证**。GPL-2.0，README 要求 Node ≥24；Windows/Linux/macOS 运行文件存在。具体 QQ 版本、登录、依赖与私聊可达性尚未实测，不能承诺可联系任意群成员 |
| QQ 备选 | [Lagrange.Core V2 / Lagrange.Milky](https://github.com/LagrangeDev/Lagrange.Core)，11-17 实际消息代码修复 | C#/.NET，Milky 接收事件、私聊/群聊发送、部分历史查询；已读等功能未实现 | 技术上匹配，但当前递归源码树未找到 LICENSE/LICENCE/COPYING、API license 为空，**使用许可需澄清**。Milky README 的 HTTP 勾选与存在的 HTTP handler 源码不一致，需要实测，不能沿用旧 V1/OneBot 教程 |
| 微信 | [weixin-wechat-wxauto-ocr-xiaoli](https://github.com/wens6515/weixin-wechat-wxauto-ocr-xiaoli)，11-08 v3.5.2；11-02 修复主动发送/会话定位 | Windows 10+、微信 4.x、Python 3.12，截图+OCR 读取可见聊天、识别私聊/群聊、操作界面发送 | **本地只读原型候选**，MIT；不能读取屏幕外完整历史，窗口不可最小化，后台源码与完整桌面应用有区别。作者仍在验证新群聊模式。必须关闭其自动回复/AI 自主发送，不能直接运行整套聊天机器人 |
| 微信备选 | [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica)，11-15 合并群聊示例、导出修复 | README 声称支持微信 4.1.12+，本地数据库解密读取、增量监听，UIA/OCR 发送；作者列出的验证版本含 4.2.0.13 | 有代码维护，但作者称维护时间有限；Apache-2.0 与 README“仅个人学习研究”“禁止批量营销”等范围声明并存。还涉及数据库密钥读取及客户端内存修改，**不作为直接商用部署选择**；这些不是本轮实测能力 |
| 小红书 | [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)，11-11 发布提及功能修复，11-06 搜索验证跳转修复 | 登录、搜索、笔记/评论、用户主页；可发评论、回复评论、发布内容；API/MCP | **优先验证需求采集**，Apache-2.0，可在 Windows/Linux 运行，需账号登录。未找到私信能力声明；评论回复不能写成私信接管。默认只开放读取给获客模型 |
| 抖音 | [F2](https://github.com/Johnserf-Seed/f2)，10-06 默认分支更新；[TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)，11-03 依赖更新、10-29 文档更新 | F2 有作品/用户数据能力，但 README 把搜索视频/用户和作品评论标为蓝色“未来实现”；TikTokDownloader 宣称支持搜索/评论，但已说明签名加密参数不再维护 | **未找到可直接承诺可用的完整获客接管方案**。F2 为 Apache-2.0；TikTokDownloader 为 GPL-3.0 且 README 对商业推广另有书面授权要求。二者不能当作已验证的私信框架 |
| 快手 | [KS-Downloader](https://github.com/JoeanAmier/KS-Downloader)，10-26 依赖更新、09-16 延时逻辑优化；[MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)，11-17 实际代码修复 | KS-Downloader 主要下载作品/保存作品信息，不是关键词获客或私信系统；MediaCrawler 宣称支持快手搜索、详情、评论等采集 | KS-Downloader GPL-3.0，README 对商业推广另有书面授权要求。MediaCrawler 明确禁止商业用途。**暂不直接嵌入商业获客系统**，更不能把下载器算成聊天接管 |
| Linux.do | [RSSHub](https://github.com/DIYgod/RSSHub)，11-18 实际路由维护；[linuxdo-idea-ui](https://github.com/czm15053/linuxdo-idea-ui)，11-17 UI 代码更新 | RSSHub 有通用 Discourse posts.rss 路由；linuxdo-idea-ui 是网页皮肤及原生交互增强 | **优先验证站点允许的公开订阅**。RSSHub AGPL-3.0；是否可访问 Linux.do 必须另外实测。界面增强为 MIT，但不是后台采集/私信 API；“企业微信外观”也不是企业微信接入 |
| NodeSeek | [tyrad/nodeseek / NS Connect](https://github.com/tyrad/nodeseek)，11-18 CI/Xcode 发布维护 | MIT 的 iOS 客户端，文档列出帖子浏览、搜索、回复、通知；登录依赖站点网页登录 | 可以参考正常操作与数据结构，**不能直接装到 Linux 当采集服务**。本轮未证实站内私信 API，也未在 RSSHub 当前树中找到 nodeseek 路由 |
| V2EX | [V2EX Polish](https://github.com/coolpace/V2EX_Polish)，11-17 发布与功能维护；RSSHub 11-18 活跃 | Polish 为浏览器扩展，帖子/评论展示、回复操作、通知；RSSHub 存在 latest/hot 主题路由 | Polish 实际 LICENSE 为 MIT（API 返回 Other）；都不是任意用户私信通道。项目已有官方 Atom 连接器，本地可读、服务器网络仍受阻，**优先本地采集执行器，避免重复建设** |

## 不能沿用旧推荐的项目

- **NapCatQQ 已出现结束声明**：[11-12 提交](https://github.com/NapNeko/NapCatQQ/commit/105f38e3ecb1428c147e0f9165f65f854233d988)，前一提交为 `Keep README only`，当前 README 只剩 Ending。未归档和高 Star 数都不能证明继续维护。
- **wxauto 主仓库不是现成的微信 4.x 商业方案**：[当前仓库](https://github.com/cluic/wxauto)仍标注 3.9.x，默认分支最近提交为 04-13 的 README 更新，并明确禁止生产/商业用途。不要仅凭 Apache 标识忽略仓库的范围声明。
- **WeChatFerry 主仓库已归档**：[lich0821/WeChatFerry](https://github.com/lich0821/WeChatFerry)，API `archived=true`，最新默认分支提交为 03-21。`wechatferry/wechatferry` 是另一 JS 包装仓库，最新提交在 2025-07，不能混同为近期活跃主项目。
- **MediaCrawler 活跃但商业用途受限**：README 明确“禁止用于商业用途”；支持小红书、抖音、快手等内容采集，不等于支持这些平台的私信。
- [RabiRoute](https://github.com/vb2250158/RabiRoute) 11-18 有真实维护，但个人微信在自身矩阵中仍标为实验性原型，QQ 验证依赖 NapCat；不能拿它证明各渠道已稳定接通。
- [NoneBot2](https://github.com/nonebot/nonebot2) 近期维护活跃，但它是机器人应用框架，不提供 QQ 账号登录传输本身。现有 FastAPI 项目未必需要再引入完整机器人框架。
- [Discourse](https://github.com/discourse/discourse) 是活跃论坛引擎，不代表我们拥有 Linux.do 的管理员 API 权限。

## 关键源码核对

- LLBot：[私聊/群聊事件转换](https://github.com/LuckyLilliaDev/LuckyLilliaBot/blob/de602971f850c3965a9b348e7d1f3e723eb1eba7/src/milky/transform/event.ts)、[消息接口](https://github.com/LuckyLilliaDev/LuckyLilliaBot/blob/de602971f850c3965a9b348e7d1f3e723eb1eba7/src/milky/api/message.ts)。README 删除了部分协议介绍，但这些接口仍在当前源码中，因此不能仅凭 README 判定接口被删除，也不能仅凭源码存在宣布登录可用。
- Lagrange：[发送私聊](https://github.com/LagrangeDev/Lagrange.Core/blob/e1bd4baa16fa86ef000154e3eecb7816a29b63a7/Lagrange.Milky/Api/Handlers/Message/SendPrivateMessageHandler.cs)调用 `SendFriendMessage`，不是任意陌生群成员必达接口；[消息接收](https://github.com/LagrangeDev/Lagrange.Core/blob/e1bd4baa16fa86ef000154e3eecb7816a29b63a7/Lagrange.Milky/Events/Converters/MessageReceiveConverter.cs)与 [Milky 功能表](https://github.com/LagrangeDev/Lagrange.Core/blob/e1bd4baa16fa86ef000154e3eecb7816a29b63a7/Lagrange.Milky/README.md)已核对。
- 小红书：[搜索验证状态修复](https://github.com/xpzouying/xiaohongshu-mcp/commit/3517d63afe7ed0299559a8007060d57b1a20ae6b)、[发布提及修复](https://github.com/xpzouying/xiaohongshu-mcp/commit/375f1e317540ca4b3cdbf1205ae47578049e92ee)。最近提交不只有贡献者名单机器人。
- RSSHub：[Discourse 路由](https://github.com/DIYgod/RSSHub/blob/67eb7bc97b01ecc4478f757608dc8540da411d50/lib/routes/discourse/posts.ts)读取配置站点 `/posts.rss`，可选 User-Api-Key；[V2EX 路由](https://github.com/DIYgod/RSSHub/blob/67eb7bc97b01ecc4478f757608dc8540da411d50/lib/routes/v2ex/topics.ts)读取官方 JSON latest/hot。安装 RSSHub 本身不会消除服务器到 V2EX 的网络故障。

## 接入到获客项目的实现顺序和验收

1. **先做通用本地执行器与 QQ 适配原型**。以模拟事件/模拟发送验证接口，再在所有者登录的客户端上只读验证明确选定的群与个人会话。优先考察 LLBot；不开启自动加好友、入群、群管理或自动回复。记录客户端/适配器版本、登录状态、最后同步、失败原因。许可/运行依赖未解决前不把候选打包成已接通。
2. **微信并行保留独立能力边界**。OCR 候选只可承诺可见窗口采集，先验证同名会话、发送者定位、重复消息及漏读；不能以昵称直接合并用户或选择收件人。账号/会话身份无法可靠核实时只生成待办，保留人工打开原聊天的入口。微信登录需在执行器机器完成，服务器不能继承本地状态。
3. **小红书做读取适配；国内开发者论坛做公开来源适配**。优先 API 选型、模型接入、计费/稳定性需求关键词，固定小样本与频率，缓存和去重后再分析。遇到登录/验证码/限制记待办，不绕过。V2EX 先复用已有解析器和本地可读链路；Linux.do/NodeSeek 的实际可访问性单独验收。
4. **抖音、快手放后面**。当前候选的搜索、评论、商用范围或签名维护存在实质缺口；先取得能验证的公开内容来源，不靠下载量充当客户获取量。

执行器仅上报选定范围内的消息，使用平台、登录账号、会话 ID、消息 ID、发送者 ID、时间、原始证据与游标去重；没有稳定消息 ID 时，标记能力降级并用保守指纹去重。账号登录数据保留在执行器本地，服务端只存必要的需求证据。群消息关联的是具体发言者，群号不是客户联系方式；陌生人会话的可达性未知时不能入发送队列。

分析沿用国内目标置信判断与实际需求价值两个维度：优先明确可联系者，高价值缺联系方式者保留“需补联系入口”。不推测邮箱/微信号，不抓提交邮箱，不把中文消息直接判成中国客户。

工作台需要展示“来源会话/发言者 → 原话与需求 → 联系入口及核实状态 → 建议消息 → 审核 → 发送结果”，支持按群和关键词选择采集范围、忽略非业务会话、退回理由、人工接管和一键打开原帖/聊天。展示接入状态和最后同步，不能把 OCR 轮询显示成完整实时同步。

任何私信、群回复、小红书评论都经过既有审核队列；确认绑定客户、平台账号、会话/收件人、正文、附件与版本。模型没有直接发送工具。执行前再检查批准有效、总暂停、拒收与人工接管。外部调用超时进入“结果未知”，先核对平台记录，不能自动重复发送；平台缺回执时不显示送达/已读。只有拥有稳定且已核实的收件人定位时才启用对应发送能力。

测试需覆盖模拟重复事件、断线游标恢复、同名会话不误合并、审核后内容/账号/会话变化失效、暂停/拒收、未知发送不重发；实际客户不作为测试收件人。代码原型、账号实测与线上接通分别记录。

## 本轮运行与成本

服务器获客 Web 健康、公网 `/healthz` 返回 200，staging、`real_send_enabled=false`，镜像仍为 `huoke:20261118-rate100`。本轮是资料与源码核查，不改变线上程序、不重建镜像、不增加模型分析速率或预算。之前的 100 条/滚动小时上限继续有效。

本轮没有通过获客模型 API 发起新分析，没有新服务订阅；后台既有分析仍可能自然发生。实际供应商账单及本轮 Codex 开发费用未知，不能记为零。尚无新增客户、联系方式覆盖率或转化改善的实测证据。
