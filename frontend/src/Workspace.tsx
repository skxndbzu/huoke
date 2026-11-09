import React, { useEffect, useState } from "react";
type R = Record<string, any>;
type API = (path: string, body?: unknown, method?: string) => Promise<any>;
const stages: R = {
  new: "待联系",
  contacted: "已联系",
  conversation: "沟通中",
  trial: "试用中",
  paid: "已付费",
  retained: "持续使用",
  declined: "已拒绝",
  closed: "已结束",
};
const quals: R = { pending: "待核实", valid: "有效需求", invalid: "不匹配" };
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
          ["contact", "待你联系", "你认可、尚未联系的客户"],
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
    [queue, setQueue] = useState(bucket);
  const [selected, setSelected] = useState<R | null>(null),
    [work, setWork] = useState<R>({}),
    [started, setStarted] = useState(Date.now());
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
          bucket: queue,
          offset: String(offset),
        }),
    );
    setItems(d.items);
    setTotal(d.total);
    setSources(d.sources);
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [q, source, qual, stage, queue, offset]);
  useEffect(() => {
    setQueue(bucket);
    setSelected(null);
    setOffset(0);
  }, [bucket]);
  async function open(id: number) {
    const d = await api("/workspace/leads/" + id);
    setSelected(d);
    setWork(d.work);
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
          <div className="filters">
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
                setOffset(0);
              }}
            >
              {options({
                "": "全部客户",
                review: "待核实",
                contact: "待联系",
                reply: "待回复",
                followup: "到期跟进",
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
                  <span className="badge">{quals[l.work.qualification]}</span>
                  <span className="badge">{stages[l.work.stage]}</span>
                  <span>
                    {l.analysis.method === "ai" ? "AI 已分析" : "规则初筛"}
                  </span>
                </div>
                <small>
                  原文发布：{fmt(l.published_at)} · 跟进：
                  {fmt(l.work.next_followup)}
                </small>
              </button>
            ))}
          </div>
          {!items.length && (
            <p className="empty">
              当前筛选下暂无客户。可切换“全部客户”，或在渠道与任务中检查采集状态。
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
              <p>
                {urlLink(selected.url)} · 发布时间 {fmt(selected.published_at)}
              </p>
              <p className="muted">
                {selected.analysis.method === "ai"
                  ? "AI 需求分析"
                  : "规则初筛，尚未由 AI 判断"}{" "}
                · 采购阶段：
                {selected.analysis.purchase_stage === "unknown"
                  ? "未知"
                  : selected.analysis.purchase_stage}
              </p>
              <p>{selected.analysis.summary}</p>
              <h3>需求证据</h3>
              {(selected.analysis.evidence || []).map(
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
                联系方式与客户价值分别核实。复制话术不代表已发送，系统发送仍须逐条审核。
              </p>
              {selected.points.map((p: R) => (
                <div className="contact-evidence" key={p.id}>
                  <strong>
                    {p.kind}：{p.value}
                  </strong>
                  <p>{p.evidence}</p>
                  <small>
                    {p.verified ? "所有者已核实" : "尚未核实"} ·{" "}
                    {p.origin === "public_source" ? "公开原文提取" : "手工添加"}{" "}
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
                  未找到明确的公开商务联系方式。可先查看原帖或项目；不猜测邮箱，不按昵称合并身份。
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
              <p>{urlLink(selected.url, "打开原帖 / 项目入口 ↗")}</p>
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
                "有联系方式",
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
                <td>{c.with_contact}</td>
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
