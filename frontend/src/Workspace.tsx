import React, { useEffect, useState } from "react";
type R = Record<string, any>;
type API = (path: string, body?: unknown, method?: string) => Promise<any>;
const stages: R = {
  new: "未联系",
  contacted: "已联系",
  conversation: "沟通中",
  trial: "试用中",
  paid: "已付费",
  retained: "持续使用",
  declined: "已拒绝",
  closed: "已结束",
};
const quals: R = { pending: "待核实", valid: "有效需求", invalid: "不匹配" };
const purchaseStages: Record<string, string> = {
  explicit_need: "明确需求",
  exploring: "选型调研",
};
const issueStates: Record<string, string> = {
  open: "原帖开放",
  closed: "原帖已关闭",
  gone: "原帖不可用",
  mismatch: "原链接指向另一条 Issue",
  unavailable: "原帖状态待核实",
};
const issueReasons: Record<string, string> = {
  completed: "GitHub 标为已完成",
  not_planned: "GitHub 标为不计划处理",
  duplicate: "GitHub 标为重复",
};
const issueLabel = (issue: R) =>
  issue.stale ? "原帖状态待复查" : issueStates[issue.state];
const contactLabels: R = {
  verified_contact: "已核实可联系",
  unverified_candidate: "有待核实联系方式",
  public_discussion: "仅有公开讨论入口",
  no_path: "暂无联系路径",
};
const issueLinkUnavailable = (lead: R) =>
  ["gone", "mismatch"].includes(lead.issue_state?.state);
const contactLabel = (lead: R) =>
  lead.contact_status === "public_discussion" && issueLinkUnavailable(lead)
    ? "公开讨论入口不可用"
    : contactLabels[lead.contact_status];
const marketScopes: R = {
  domestic: "国内目标（达到阈值）",
  unknown: "待 AI / 市场未知",
  below_threshold: "AI 低于阈值",
  outside_target: "人工排除市场",
  all: "全部市场（含历史）",
};
const defaultMarket = (bucket: string) =>
  bucket === "market"
    ? "unknown"
    : ["reply", "followup", "opportunity", "market_review"].includes(bucket)
      ? "all"
      : "domestic";
const kinds: R = {
  note: "备注",
  outbound: "手动联系",
  inbound: "客户回复（手工录入）",
  conversation: "有效沟通",
  registered: "已注册",
  first_call: "首次成功调用",
  paid: "已付费",
  retained: "持续使用",
};
const channels: R = {
  wechat: "微信",
  qq: "QQ",
  email: "邮件",
  github: "GitHub",
  community: "社区",
  phone: "电话",
  other: "其他",
};
const fmt = (s: string) =>
  s
    ? new Date(s).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "未设置";
const localInput = (s: string) => {
  const d = s ? new Date(s) : new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
const urlLink = (url: string, text = "查看原文 ↗") =>
  /^https?:\/\//i.test(url) ? (
    <a href={url} target="_blank" rel="noreferrer">
      {text}
    </a>
  ) : (
    <span>暂无链接</span>
  );
const options = (items: R) =>
  Object.entries(items).map(([value, label]) => (
    <option key={value} value={value}>
      {label}
    </option>
  ));
const requestId = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");

function MarketPanel({
  lead,
  owner,
  busy,
  onSave,
}: {
  lead: R;
  owner: boolean;
  busy: boolean;
  onSave: (data: R) => Promise<void>;
}) {
  const m = lead.market;
  const [status, setStatus] = useState("unknown");
  const [reason, setReason] = useState("");
  const [sourceUrl, setSourceUrl] = useState(lead.url);
  useEffect(() => {
    setStatus(m?.manual ? m.status : "unknown");
    setReason(m?.manual ? m.reason : "");
    setSourceUrl(m?.manual ? m.evidence?.[0]?.url || lead.url : lead.url);
  }, [lead.id, m?.version]);
  return (
    <section className="market-evidence">
      <h3>目标市场：{m?.label || "市场待核实"}</h3>
      <p>
        <strong>
          国内目标置信分数：
          {m?.score == null ? "未知 / 尚未评估" : `${m.score} / 100`}
        </strong>{" "}
        · 纳入阈值 {m?.threshold ?? 70} 分
      </p>
      <p>{m?.reason}</p>
      {(m?.evidence || []).map((e: R, i: number) => (
        <blockquote key={i}>
          {e.quote} {e.url === lead.url && issueLinkUnavailable(lead)
            ? "原帖链接不可用" : urlLink(e.url, "证据来源 ↗")}
        </blockquote>
      ))}
      <p className="muted">
        判断服务对象是否为国内用户、企业或团队，不推断国籍。中文交流只是初步线索；英语帖子也可能来自国内团队。需求价值和是否允许联系另行核实。
      </p>
      {owner && (
        <details>
          <summary>核实或纠正目标市场</summary>
          <label>
            市场判断
            <select
              aria-label="市场判断"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              {options({
                unknown: "证据不足，待核实",
                confirmed_cn: "有证据确认国内服务对象",
                outside_target: "有证据确认非目标市场",
                auto: "恢复自动 AI 判断",
              })}
            </select>
          </label>
          <label>
            业务地区依据
            <textarea
              aria-label="业务地区依据"
              placeholder="例如团队官网注明服务中国企业。不要仅凭昵称或头像判断。"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <label>
            依据链接
            <input
              aria-label="市场依据链接"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
            />
          </label>
          <button
            disabled={busy || reason.trim().length < 5}
            onClick={() =>
              onSave({
                version: m?.version || 0,
                status,
                reason,
                source_url: sourceUrl,
              })
            }
          >
            保存市场判断
          </button>
        </details>
      )}
    </section>
  );
}

function MarketThreshold({
  api,
  owner,
  onSaved,
}: {
  api: API;
  owner: boolean;
  onSaved: () => Promise<void>;
}) {
  const [policy, setPolicy] = useState<R | null>(null),
    [value, setValue] = useState(70),
    [message, setMessage] = useState(""),
    [saving, setSaving] = useState(false);
  useEffect(() => {
    api("/workspace/market-policy")
      .then((p) => {
        setPolicy(p);
        setValue(p.threshold);
      })
      .catch((e) => setMessage(e.message));
  }, []);
  return (
    <section className="market-policy">
      <label>
        国内目标纳入阈值（0–100 分）
        <input
          aria-label="国内目标纳入阈值"
          type="number"
          min="0"
          max="100"
          value={value}
          disabled={!owner || saving}
          onChange={(e) => setValue(Number(e.target.value))}
        />
      </label>
      {owner && (
        <button
          className="secondary"
          disabled={
            !policy ||
            saving ||
            !Number.isInteger(value) ||
            value < 0 ||
            value > 100
          }
          onClick={async () => {
            setSaving(true);
            setMessage("");
            try {
              const p = await api(
                "/workspace/market-policy",
                { version: policy!.version, threshold: value },
                "PUT",
              );
              setPolicy(p);
              await onSaved();
              setMessage("阈值已生效，复用已有分数，不重复消耗模型 Token");
            } catch (e) {
              setMessage((e as Error).message);
              const p = await api("/workspace/market-policy");
              setPolicy(p);
            } finally {
              setSaving(false);
            }
          }}
        >
          保存筛选阈值
        </button>
      )}
      <p className="muted">
        AI
        分数达到阈值才自动纳入国内目标；没有分数的记录不会因降低阈值而入选。分数不是校准概率，人工确认可覆盖
        AI 判断。
      </p>
      {message && <p role="status">{message}</p>}
    </section>
  );
}

export function Today({
  api,
  openQueue,
}: {
  api: API;
  openQueue: (bucket: string) => void;
}) {
  const [data, setData] = useState<R | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    const load = () =>
      api("/workspace/metrics")
        .then(setData)
        .catch((e) => setError(e.message));
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);
  return (
    <>
      <div className="stats actionable">
        {[
          ["review", "值得看看", "核实原文，标记有效需求"],
          ["market_review", "选型需求待核实地区", "查看已有选型证据、地区仍待确认的线索"],
          ["research", "待补联系方式", "需求有效，但还没有核实的联系路径"],
          [
            "high_value",
            "高价值待补联系方式",
            "AI 高匹配、高优先级，缺少邮箱或社交账号",
          ],
          ["contact", "待你联系", "需求有效、联系路径已核实"],
          ["reply", "待你回复", "手工录入的未处理回复"],
          ["followup", "到期跟进", "按你设置的时间提醒"],
        ].map(([key, title, desc]) => (
          <button key={key} onClick={() => openQueue(key)}>
            <span>{title}</span>
            <strong>{data?.counts[key] ?? "—"}</strong>
            <small>{desc} →</small>
          </button>
        ))}
      </div>
      <section className="panel">
        <h2>获客进展：先看有效需求</h2>
        <div className="stats">
          {[["total", "收集的候选"], ["pending", "待市场分析"], ["ai_assessed", "已做市场分析"],
            ["domestic", "筛选后的国内线索"], ["domestic_accounts", "对应独立平台账号"], ["domestic_with_contact", "国内线索有联系方式"]].map(([key, label]) => (
            <div key={key}><span>{label}</span><strong>{data?.acquisition?.[key] ?? "—"}</strong></div>
          ))}
        </div>
        <p>
          待分析不是筛选失败。同一平台账号的多条需求不算多个客户。系统优先处理接口选型、服务问题和应用开发需求，每小时最多分析 100 条，约每 36 秒调度一条；实际数量受有效候选、模型响应及费用预算限制。另每小时最多补查五位作者的公开资料和个人 README，不消耗模型 Token。公开联系方式仍需你核实，普通技术任务留待人工查看。
        </p>
        <button onClick={() => openQueue("opportunity")}>
          查看优先调查需求（{data?.acquisition?.priority_pending ?? "—"}）
        </button>{" "}
        <button className="secondary" onClick={() => openQueue("market")}>
          核实目标市场（{data?.counts.market ?? "—"}）
        </button>
      </section>
      <section className="panel">
        <h2>你的日常获客流程</h2>
        <p>
          系统每小时采集公开需求，去重并整理证据。你核实需求、查看商务联系方式、编辑招呼语，然后在相应平台联系，并记录结果和下次跟进时间。
        </p>
        <p className="muted">
          微信、QQ
          和邮箱回复尚未自动同步；请在客户档案录入实际沟通。记录“手动联系”不会通过系统发送消息。
        </p>
      </section>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </>
  );
}

export function CustomerWorkspace({
  api,
  owner,
  bucket,
  onEmail,
}: {
  api: API;
  owner: boolean;
  bucket: string;
  onEmail: (id: number) => void;
}) {
  const [items, setItems] = useState<R[]>([]),
    [sources, setSources] = useState<string[]>([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0);
  const [q, setQ] = useState(""),
    [source, setSource] = useState(""),
    [qual, setQual] = useState(""),
    [stage, setStage] = useState(""),
    [contactFilter, setContactFilter] = useState(""),
    [queue, setQueue] = useState(bucket);
  const [market, setMarket] = useState(defaultMarket(bucket));
  const [selected, setSelected] = useState<R | null>(null),
    [work, setWork] = useState<R>({}),
    [started, setStarted] = useState(Date.now());
  const [research, setResearch] = useState<R | null>(null);
  const [body, setBody] = useState(""),
    [note, setNote] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [point, setPoint] = useState({
    kind: "wechat",
    value: "",
    source_url: "",
    evidence: "",
  });
  const [touch, setTouch] = useState({
    kind: "note",
    channel: "wechat",
    body: "",
    occurred_at: localInput(""),
    request_id: requestId(),
  });
  async function load() {
    const d = await api(
      "/workspace/leads?" +
        new URLSearchParams({
          q,
          source,
          qualification: qual,
          stage,
          contact: contactFilter,
          bucket: queue,
          market,
          offset: String(offset),
        }),
    );
    setItems(d.items);
    setTotal(d.total);
    setSources(d.sources);
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [q, source, qual, stage, contactFilter, queue, market, offset]);
  useEffect(() => {
    setQueue(bucket);
    setMarket(defaultMarket(bucket));
    setSelected(null);
    setResearch(null);
    setOffset(0);
  }, [bucket]);
  async function open(id: number) {
    const d = await api("/workspace/leads/" + id);
    setSelected(d);
    setWork(d.work);
    setResearch(null);
    setStarted(Date.now());
    setBody("");
    setNote("");
    setPoint({ kind: "wechat", value: "", source_url: d.url, evidence: "" });
    setTouch({
      kind: "note",
      channel: "wechat",
      body: "",
      occurred_at: localInput(""),
      request_id: requestId(),
    });
    window.scrollTo(0, 0);
  }
  async function action(fn: () => Promise<any>, msg = "已保存") {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await fn();
      await load();
      setNote(msg);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function reload() {
    const d = await api("/workspace/leads/" + selected!.id);
    setSelected(d);
    setWork(d.work);
  }
  async function copy(text: string) {
    if (navigator.clipboard && window.isSecureContext)
      await navigator.clipboard.writeText(text);
    else {
      const el = document.createElement("textarea");
      el.value = text;
      document.body.appendChild(el);
      el.select();
      const ok = document.execCommand("copy");
      el.remove();
      if (!ok) throw Error("浏览器不允许自动复制，请选中文字手动复制");
    }
  }
  const saveWork = async (extra: R = {}) => {
    await api(
      "/workspace/leads/" + selected!.id,
      {
        version: work.version,
        qualification: work.qualification,
        reason: work.reason,
        stage: work.stage,
        next_followup: work.next_followup,
        review_seconds: Math.min(
          3600,
          Math.max(0, Math.round((Date.now() - started) / 1000)),
        ),
        ...extra,
      },
      "PUT",
    );
    await reload();
    setStarted(Date.now());
  };
  return (
    <>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {note && (
        <div className="success" role="status">
          {note}
        </div>
      )}
      {!selected ? (
        <section className="panel">
          <div className="section-title">
            <h2>
              潜在客户 <small>{total} 条</small>
            </h2>
            <button
              className="secondary"
              disabled={busy}
              onClick={() => action(load, "已刷新")}
            >
              刷新列表
            </button>
          </div>
          <MarketThreshold api={api} owner={owner} onSaved={load} />
          <p className="muted">
            {queue === "market_review"
              ? "打开档案核对 API 需求、地区证据和联系路径。分析失败的线索可依据初筛原文核实。"
              : "列表优先展示已核实的邮箱/社交账号，其次为公开待核实联系方式，再展示高价值待补联系方式。低分和待评估记录仍保留，市场匹配与采购意愿分别核实。"}
          </p>
          <div className="filters">
            <select
              aria-label="目标市场"
              value={market}
              onChange={(e) => {
                setMarket(e.target.value);
                setQueue("");
                setOffset(0);
              }}
            >
              {options(queue === "market_review" ? { ...marketScopes, all: "地区待核实（全部）" } : marketScopes)}
            </select>
            <input
              aria-label="搜索需求"
              placeholder="搜索需求、项目或作者"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
            />
            <select
              aria-label="待办分类"
              value={queue}
              onChange={(e) => {
                setQueue(e.target.value);
                setMarket(defaultMarket(e.target.value));
                setOffset(0);
              }}
            >
              {options({
                "": "不限待办",
                opportunity: "优先调查需求",
                review: "待核实",
                research: "待补联系方式",
                high_value: "高价值待补联系方式",
                contact: "可联系",
                reply: "待回复",
                followup: "到期跟进",
                market: "核实目标市场",
                market_review: "选型需求待核实地区",
              })}
            </select>
            <select
              aria-label="来源筛选"
              value={source}
              onChange={(e) => {
                setSource(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部来源</option>
              {sources.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <select
              aria-label="需求筛选"
              value={qual}
              onChange={(e) => {
                setQual(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部需求判断</option>
              {options(quals)}
            </select>
            <select
              aria-label="联系路径筛选"
              value={contactFilter}
              onChange={(e) => {
                setContactFilter(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部联系状态</option>
              {options(contactLabels)}
            </select>
            <select
              aria-label="阶段筛选"
              value={stage}
              onChange={(e) => {
                setStage(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">全部阶段</option>
              {options(stages)}
            </select>
          </div>
          <div className="customer-cards">
            {items.map((l) => (
              <button
                className="customer-card"
                key={l.id}
                onClick={() => action(() => open(l.id), "")}
              >
                <small>
                  {l.source} · {l.author}
                </small>
                <h3>{l.title}</h3>
                <p>{l.analysis.summary?.slice(0, 200)}</p>
                <div className="tag-row">
                  {l.triage?.category === "supplier_promotion" && (
                    <span className="badge">广告或站点收录投稿</span>
                  )}
                  {l.high_value_no_contact && (
                    <span className="badge">高价值 · 待补联系方式</span>
                  )}
                  <span className="badge">
                    {l.market?.label || "市场待核实"}
                    {l.market?.score != null ? ` · ${l.market.score} 分` : ""}
                  </span>
                  <span className="badge">{quals[l.work.qualification]}</span>
                  <span className="badge">{stages[l.work.stage]}</span>
                  <span className="badge">
                    {contactLabel(l)}
                  </span>
                  {l.contact_invitation && <span className="badge">原文提到私聊</span>}
                  {l.issue_state && <span className="badge">{issueLabel(l.issue_state)}</span>}
                  <span>
                    {l.analysis.method === "ai"
                      ? purchaseStages[l.analysis.purchase_stage]
                        ? `AI · ${purchaseStages[l.analysis.purchase_stage]}`
                        : "AI 已分析"
                      : l.analysis_failed ? "AI 分析失败 · 待人工核实" : "规则初筛"}
                  </span>
                </div>
                <small>
                  {queue === "opportunity" && `${l.triage?.reason || "待核实需求"} · `}
                  原文发布：{fmt(l.published_at)} · 跟进：
                  {fmt(l.work.next_followup)}
                </small>
              </button>
            ))}
          </div>
          {!items.length && (
            <p className="empty">
              {queue === "market_review"
                ? "当前没有符合筛选条件的待核实选型需求。"
                : "当前筛选下暂无客户。可切换“全部客户”，或在渠道与任务中检查采集状态。"}
            </p>
          )}
          <div className="actions">
            <button
              className="secondary"
              disabled={!offset}
              onClick={() => setOffset(Math.max(0, offset - 50))}
            >
              上一页
            </button>
            <span>
              {total ? offset + 1 : 0}–{Math.min(offset + 50, total)} / {total}
            </span>
            <button
              className="secondary"
              disabled={offset + 50 >= total}
              onClick={() => setOffset(offset + 50)}
            >
              下一页
            </button>
          </div>
        </section>
      ) : (
        <>
          <button
            className="text"
            onClick={() => {
              setSelected(null);
              setResearch(null);
              setError("");
              setNote("");
            }}
          >
            ← 返回客户列表
          </button>
          <div className="columns detail">
            <section className="panel">
              <small>
                {selected.source} · {selected.author}
              </small>
              <h2>{selected.title}</h2>
              {selected.capture && <div className="provider">
                <h3>采集证据</h3>
                <p>方式：{({manual: '授权原文导入', server_feed: '公开订阅', server_search: '官方 API 搜索'} as Record<string, string>)[selected.capture.provenance] || '本地执行器'} · 采集时间 {fmt(selected.capture.captured_at)}</p>
                {selected.capture.conversation && <p>采集账号：{selected.capture.account}<br/>来源会话：{selected.capture.conversation}<br/>发言者：{selected.capture.sender_label} {selected.capture.sender_id && '（平台 ID '+selected.capture.sender_id+'）'}</p>}
                {selected.capture.platform === 'wechat' && <p>OCR 仅识别可见窗口，发布时间及发言者身份待核实，不能仅凭昵称联系或合并客户。</p>}
                {selected.capture.platform === 'qq' && <p>平台发送者 ID 不代表已允许联系，也不保证可以私聊。请先核实原会话及联系意愿。</p>}
              </div>}
              <p>
                {issueLinkUnavailable(selected) ? "原帖链接不可用" : urlLink(selected.url)}
                {" · 发布时间 "}{fmt(selected.published_at)}
              </p>
              <MarketPanel
                lead={selected}
                owner={owner}
                busy={busy}
                onSave={(data) =>
                  action(async () => {
                    await api(
                      "/workspace/leads/" + selected.id + "/market",
                      data,
                      "PUT",
                    );
                    await reload();
                  }, "目标市场判断已更新")
                }
              />
              <p className="muted">
                {selected.analysis.method === "ai"
                  ? `AI 需求分析 · 采购阶段 ${purchaseStages[selected.analysis.purchase_stage] || "待核实"}`
                  : selected.analysis_failed ? "AI 分析失败，请根据原文核实需求和地区" : "规则初筛，尚未由 AI 判断"}
              </p>
              <p>{selected.analysis.summary}</p>
              {selected.issue_state && (
                <p className="muted">
                  {issueLabel(selected.issue_state)}
                  {!selected.issue_state.stale && issueReasons[selected.issue_state.reason]
                    ? ` · ${issueReasons[selected.issue_state.reason]}` : ""}
                  {" · 最近核对 "}{fmt(selected.issue_state.checked_at)}
                </p>
              )}
              <h3>需求证据</h3>
              {((selected.analysis_failed ? selected.triage?.evidence : selected.analysis.evidence) || []).map(
                (e: string, i: number) => (
                  <blockquote key={i}>{e}</blockquote>
                ),
              )}
              <p>
                未知事项：
                {(selected.analysis.unknowns || []).join("、") || "请核实原文"}
              </p>
              <details>
                <summary>展开完整原文</summary>
                <pre>{selected.text}</pre>
              </details>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    await api("/leads/" + selected.id + "/analyze", {});
                    await reload();
                  }, "分析完成")
                }
              >
                分析这条需求
              </button>
              {!selected.market?.eligible && (
                <p className="muted">
                  尚未达到目标市场纳入条件；可以补充公开证据或手动分析。已分析记录优先复用结果，异常调用不盲目重试。
                </p>
              )}
              <hr />
              <h3>判断与跟进</h3>
              <label>
                是否有实际需求
                <select
                  value={work.qualification}
                  onChange={(e) =>
                    setWork({ ...work, qualification: e.target.value })
                  }
                >
                  {options(quals)}
                </select>
              </label>
              <label>
                判断依据 / 不匹配原因
                <textarea
                  value={work.reason}
                  onChange={(e) => setWork({ ...work, reason: e.target.value })}
                  placeholder="例如：明确询问计费与采购；或只是教程/同行广告"
                />
              </label>
              <label>
                沟通阶段
                <select
                  value={work.stage}
                  onChange={(e) => setWork({ ...work, stage: e.target.value })}
                >
                  {options(stages)}
                </select>
              </label>
              <label>
                下次跟进（本机时区）
                <input
                  type="datetime-local"
                  value={
                    work.next_followup ? localInput(work.next_followup) : ""
                  }
                  onChange={(e) =>
                    setWork({
                      ...work,
                      next_followup: e.target.value
                        ? new Date(e.target.value).toISOString()
                        : "",
                    })
                  }
                />
              </label>
              <div className="actions">
                <button
                  disabled={busy}
                  onClick={() => action(() => saveWork())}
                >
                  保存判断与跟进
                </button>
                <button
                  className="secondary"
                  disabled={busy || !work.next_followup}
                  onClick={() =>
                    action(
                      () => saveWork({ next_followup: "" }),
                      "本次跟进已完成，可设置下一次时间",
                    )
                  }
                >
                  完成本次跟进
                </button>
              </div>
              <p className="muted">
                选择已拒绝或已结束会停止跟进提醒，并开启人工接管，阻止系统发送。
              </p>
            </section>
            <section className="panel">
              <h2>联系客户</h2>
              <p className="muted">
                {contactLabel(selected)}
                。联系方式与客户价值分别核实。复制话术不代表已发送，系统发送仍须逐条审核。
              </p>
              {selected.contact_invitation && (
                <div className="contact-evidence">
                  <strong>原文提到私聊</strong>
                  <p>「{selected.contact_invitation.quote}」</p>
                  <p className="muted">发布于 {fmt(selected.published_at)} · 当前联系意愿待核实</p>
                  <p>{urlLink(selected.contact_invitation.url, "查看联系线索原文 ↗")}</p>
                </div>
              )}
              {selected.source_key?.startsWith("v2ex:") && selected.market?.profile?.status === "public_profile" && (
                <div className="contact-evidence">
                  <p>{urlLink(selected.market.profile.url, "V2EX 作者公开资料 ↗")}</p>
                  {selected.market.profile.github && <p>{urlLink(`https://github.com/${encodeURIComponent(selected.market.profile.github)}`, "作者填写的 GitHub 账号 ↗")}</p>}
                  {selected.market.profile.location && <p>公开资料填写的所在地为 {selected.market.profile.location}</p>}
                </div>
              )}
              {selected.source_key?.startsWith("github:") && (
                <div className="contact-evidence">
                  <button disabled={busy} onClick={() => action(async () => {
                    const result = await api(`/workspace/leads/${selected.id}/public-evidence`, {}, "POST");
                    await reload();
                    setResearch({ notice: result.notice, profile_url: `https://github.com/${selected.author}` });
                  }, "公开资料已检查，找到的联系方式已整理在下方；未找到时不会猜测账号")}>整理公开联系方式</button>{" "}
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      action(async () => {
                        const d = await api(
                          "/workspace/leads/" +
                            selected.id +
                            "/contact-research",
                        );
                        setResearch(d);
                      }, "公开资料已加载；请人工核实后再录入")
                    }
                  >
                    查 GitHub 作者公开资料
                  </button>
                  {research && (
                    <div>
                      <p>{urlLink(research.profile_url, "作者公开资料 ↗")}</p>
                      <p>
                        公开邮箱候选：{research.public_email || "接口未返回"}
                      </p>
                      <p>
                        资料所填网站：
                        {research.website_url
                          ? urlLink(research.website_url, "查看网站 ↗")
                          : "未填写"}
                      </p>
                      <p>资料所填单位：{research.company || "未填写"}</p>
                      <small>
                        以上不证明作者代表公司或同意商务联系。请查官网联系页及身份关联证据，再手工录入；不会自动添加邮箱或发送消息。
                      </small>
                    </div>
                  )}
                </div>
              )}
              {selected.points.map((p: R) => (
                <div className="contact-evidence" key={p.id}>
                  <strong>
                    {p.kind}：{p.value}
                  </strong>
                  <p>{p.evidence}</p>
                  <small>
                    {p.verified ? "所有者已核实" : "尚未核实"} ·{" "}
                    {p.origin === "public_source" ? "公开原文提取" : p.origin === "authorized_chat" ? "授权会话来源（联系许可未核实）" : "手工添加"}{" "}
                    · {fmt(p.collected_at)}
                  </small>
                  <p>{urlLink(p.source_url, "联系方式出处 ↗")}</p>
                  <div className="actions">
                    <button
                      className="secondary"
                      onClick={() =>
                        action(() => copy(p.value), "联系方式已复制")
                      }
                    >
                      复制
                    </button>
                    {owner && !p.verified && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          action(async () => {
                            await api(
                              "/workspace/points/" + p.id + "/verify",
                              {},
                            );
                            await reload();
                          })
                        }
                      >
                        核实联系方式
                      </button>
                    )}
                    {p.kind === "url" &&
                      p.verified &&
                      urlLink(p.value, "打开联系入口 ↗")}
                    {p.kind === "email" &&
                      p.verified &&
                      /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(p.value) && (
                        <a href={"mailto:" + encodeURIComponent(p.value)}>
                          打开邮件客户端 ↗
                        </a>
                      )}
                  </div>
                </div>
              ))}
              {selected.email_contacts.map((c: R) => (
                <p key={"email" + c.id}>
                  邮箱：{c.address}（{c.verified ? "已核实" : "待核实"}）{" "}
                  {urlLink(c.source_url, "出处 ↗")}
                </p>
              ))}
              {!selected.points.length && !selected.email_contacts.length && (
                <p className="muted">
                  未找到明确的公开商务联系方式。可先查看原帖、作者资料或官网联系页；公开讨论入口不等于允许营销，不猜测邮箱，不按昵称合并身份。
                </p>
              )}
              <details>
                <summary>添加公开或授权提供的联系方式</summary>
                <label>
                  类型
                  <select
                    value={point.kind}
                    onChange={(e) =>
                      setPoint({ ...point, kind: e.target.value })
                    }
                  >
                    {options({
                      wechat: "微信",
                      qq: "QQ",
                      email: "邮箱",
                      url: "联系页面",
                    })}
                  </select>
                </label>
                <label>
                  号码 / 地址
                  <input
                    value={point.value}
                    onChange={(e) =>
                      setPoint({ ...point, value: e.target.value })
                    }
                  />
                </label>
                <label>
                  证据链接
                  <input
                    value={point.source_url}
                    onChange={(e) =>
                      setPoint({ ...point, source_url: e.target.value })
                    }
                  />
                </label>
                <label>
                  公开商务联系或授权依据
                  <textarea
                    value={point.evidence}
                    onChange={(e) =>
                      setPoint({ ...point, evidence: e.target.value })
                    }
                  />
                </label>
                <button
                  disabled={busy || !point.value || point.evidence.length < 5}
                  onClick={() =>
                    action(async () => {
                      await api(
                        "/workspace/leads/" + selected.id + "/points",
                        point,
                      );
                      await reload();
                      setPoint({ ...point, value: "", evidence: "" });
                    })
                  }
                >
                  保存为待核实
                </button>
              </details>
              <hr />
              <h3>建议招呼语</h3>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    const d = await api(
                      "/workspace/leads/" + selected.id + "/suggestion",
                    );
                    setBody(d.body);
                  }, "已基于确认产品事实生成，可编辑后复制")
                }
              >
                生成建议话术
              </button>
              {body && (
                <>
                  <textarea
                    aria-label="建议招呼语"
                    rows={6}
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                  />
                  <button
                    disabled={busy}
                    onClick={() =>
                      action(
                        () => copy(body),
                        "话术已复制，请由你在目标平台决定是否发送",
                      )
                    }
                  >
                    复制话术
                  </button>
                </>
              )}
              <p>{issueLinkUnavailable(selected) ? "原帖链接不可用" : urlLink(selected.url, "打开原帖 / 项目入口 ↗")}</p>
              <button className="text" onClick={() => onEmail(selected.id)}>
                邮件草稿与逐条审核 →
              </button>
              <hr />
              <h3>记录实际沟通</h3>
              <label>
                记录类型
                <select
                  value={touch.kind}
                  onChange={(e) =>
                    setTouch({
                      ...touch,
                      kind: e.target.value,
                      request_id: requestId(),
                    })
                  }
                >
                  {options(kinds)}
                </select>
              </label>
              <label>
                沟通渠道
                <select
                  value={touch.channel}
                  onChange={(e) =>
                    setTouch({
                      ...touch,
                      channel: e.target.value,
                      request_id: requestId(),
                    })
                  }
                >
                  {options(channels)}
                </select>
              </label>
              <label>
                发生时间（本机时区）
                <input
                  type="datetime-local"
                  value={touch.occurred_at}
                  onChange={(e) =>
                    setTouch({
                      ...touch,
                      occurred_at: e.target.value,
                      request_id: requestId(),
                    })
                  }
                />
              </label>
              <label>
                内容 / 证据说明
                <textarea
                  value={touch.body}
                  onChange={(e) =>
                    setTouch({
                      ...touch,
                      body: e.target.value,
                      request_id: requestId(),
                    })
                  }
                  placeholder="记录实际联系、客户回复或业务事件，不代表平台送达回执"
                />
              </label>
              <button
                disabled={busy || !touch.body.trim() || !touch.occurred_at}
                onClick={() =>
                  action(async () => {
                    await api(
                      "/workspace/leads/" + selected.id + "/touchpoints",
                      {
                        ...touch,
                        occurred_at: new Date(touch.occurred_at).toISOString(),
                      },
                    );
                    await reload();
                    setTouch({ ...touch, body: "", request_id: requestId() });
                  }, "已记录，未通过系统发送任何消息")
                }
              >
                保存沟通记录
              </button>
            </section>
          </div>
          <section className="panel">
            <div className="section-title">
              <h2>沟通时间线</h2>
              {work.unread_reply && (
                <button
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      await api(
                        "/workspace/leads/" + selected.id + "/read",
                        {},
                      );
                      await reload();
                    }, "已标记处理")
                  }
                >
                  标记回复已处理
                </button>
              )}
            </div>
            {selected.timeline.map((t: R) => (
              <article className="timeline" key={t.id}>
                <strong>
                  {kinds[t.kind]} · {channels[t.channel]}
                </strong>
                <small>{fmt(t.occurred_at)} · 手工记录</small>
                <pre>{t.body}</pre>
              </article>
            ))}
            {!selected.timeline.length && (
              <p className="muted">
                暂无沟通记录。外部平台的回复不会自动出现在这里。
              </p>
            )}
          </section>
        </>
      )}
    </>
  );
}

export function Metrics({ api }: { api: API }) {
  const [data, setData] = useState<R | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    api("/workspace/metrics")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  return (
    <section className="panel">
      <h2>渠道质量与转化</h2>
      <p className="muted">
        注册、调用、付费和留存来自你手工录入的实际记录，计费平台尚未接通。阶段标签不自动算作转化事件；每个客户在每个事件中只计一次。
      </p>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {[
                "渠道",
                "候选",
                "已核实 / 有效",
                "有效占比",
                "已核实可联系",
                "手工回复 / 联系",
                "有效沟通",
                "注册",
                "首次调用",
                "付费",
                "留存",
                "AI 成本 / 有效客户",
              ].map((x) => (
                <th key={x}>{x}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data?.channels.map((c: R) => (
              <tr key={c.source}>
                <td>{c.source}</td>
                <td>{c.candidates}</td>
                <td>
                  {c.reviewed} / {c.valid}
                </td>
                <td>
                  {c.valid_rate === null
                    ? "待标注"
                    : (c.valid_rate * 100).toFixed(0) + "%"}
                </td>
                <td>
                  {c.with_contact}{" "}
                  <small>／候选 {c.with_candidate_contact}</small>
                </td>
                <td>
                  {c.manual_replied} / {c.manual_contacted}{" "}
                  <small>
                    {c.manual_reply_rate === null
                      ? "尚无联系记录"
                      : (c.manual_reply_rate * 100).toFixed(0) + "%"}
                  </small>
                </td>
                <td>{c.conversation}</td>
                <td>{c.registered}</td>
                <td>{c.first_call}</td>
                <td>{c.paid}</td>
                <td>{c.retained}</td>
                <td>
                  {c.ai_cost_per_valid === null
                    ? "未确定"
                    : "$" + c.ai_cost_per_valid.toFixed(4)}
                  {c.unresolved_calls > 0 && (
                    <small>{c.unresolved_calls} 次费用未决</small>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        人工核实用时：
        {data
          ? Math.round(
              data.channels.reduce(
                (a: number, c: R) => a + c.review_seconds,
                0,
              ) / 60,
            )
          : "—"}{" "}
        分钟（按打开至保存的时间估算，单次上限 60
        分钟）。开发成本、人工成本与客户毛利尚未核算，AI 成本按配置价格估算。
      </p>
    </section>
  );
}

export function DiscoverySources({ api }: { api: API }) {
  const [sources, setSources] = useState<R[]>([]),
    [edit, setEdit] = useState<R | null>(null),
    [error, setError] = useState(""),
    [note, setNote] = useState(""),
    [busy, setBusy] = useState(false);
  const load = () => api("/discovery/sources").then(setSources);
  useEffect(() => {
    load().catch((e) => setError(e.message));
    const t = setInterval(
      () => load().catch((e) => setError(e.message)),
      15000,
    );
    return () => clearInterval(t);
  }, []);
  async function action(fn: () => Promise<any>, message: string) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
      setNote(message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="columns">
        {sources.map((s) => (
          <section className="panel" key={s.channel}>
            <div className="section-title">
              <h2>{s.name}</h2>
              <span className="badge">
                {s.status === "success"
                  ? "读取已验证"
                  : s.status === "failed"
                    ? "最近读取失败"
                    : s.status === "running"
                      ? "采集中"
                      : "待验证"}
              </span>
            </div>
            <p>{s.scope}</p>
            <p>
              自动采集：{s.enabled ? "每小时启用" : "已停用"} · 最后成功：
              {fmt(s.last_success)}
            </p>
            <p>不支持主动发送、回复同步或私有资料读取。</p>
            <p>{urlLink(s.docs, "来源说明 ↗")}</p>
            {s.error && (
              <p className="error">
                失败原因：{s.error}。下次允许尝试：{fmt(s.next_allowed_at)}
              </p>
            )}
            <p>
              最近一轮：读取 {s.stats.listed ?? 0} · 缓存命中{" "}
              {s.stats.cache_hits ?? 0} · 新候选 {s.stats.new_leads ?? 0}
            </p>
            <div className="actions">
              <button
                disabled={
                  busy ||
                  !s.enabled ||
                  new Date(s.next_allowed_at).getTime() > Date.now()
                }
                onClick={() =>
                  action(
                    () => api("/discovery/sources/" + s.channel + "/sync", {}),
                    "采集任务已排队，运行结果将在此更新",
                  )
                }
              >
                立即采集
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  setEdit({
                    ...s,
                    queriesText: s.queries.join("\n"),
                    excludeText: s.exclude.join("\n"),
                  })
                }
              >
                设置关键词
              </button>
            </div>
          </section>
        ))}
      </div>
      {edit && (
        <section className="panel">
          <h2>配置 {edit.name}</h2>
          <label className="check">
            <input
              type="checkbox"
              checked={edit.enabled}
              onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })}
            />
            每小时自动采集
          </label>
          <label>
            {edit.channel === "github"
              ? "搜索词，每行一组，最多 3 组"
              : "订阅节点，每行一个：python、programmer、ai"}
            <textarea
              value={edit.queriesText}
              onChange={(e) =>
                setEdit({ ...edit, queriesText: e.target.value })
              }
            />
          </label>
          <label>
            排除词（每行一个）
            <textarea
              value={edit.excludeText}
              onChange={(e) =>
                setEdit({ ...edit, excludeText: e.target.value })
              }
            />
          </label>
          <button
            disabled={busy}
            onClick={() =>
              action(async () => {
                await api(
                  "/discovery/sources/" + edit.channel,
                  {
                    version: edit.version,
                    enabled: edit.enabled,
                    queries: edit.queriesText
                      .split("\n")
                      .map((x: string) => x.trim())
                      .filter(Boolean),
                    exclude: edit.excludeText
                      .split("\n")
                      .map((x: string) => x.trim())
                      .filter(Boolean),
                  },
                  "PUT",
                );
                setEdit(null);
              }, "配置已保存，将在下次采集生效")
            }
          >
            保存
          </button>{" "}
          <button className="secondary" onClick={() => setEdit(null)}>
            取消
          </button>
        </section>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {note && <p className="success">{note}</p>}
    </>
  );
}
