import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Row = Record<string, any>;
const names: Row = {
  pending_review: "待审核",
  approved: "已确认",
  queued: "排队中",
  sending: "发送中",
  submitted: "已提交",
  sent: "发送成功",
  failed: "失败",
  unknown: "结果未知",
  cancelled: "已取消",
  simulated: "模拟完成",
};
const date = (v: string) =>
  v
    ? new Date(v).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "尚无记录";
const badge = (text: string) => <span className="badge">{text}</span>;
const link = (url: string, label = "查看原文 ↗") =>
  /^https?:\/\//.test(url) ? (
    <a href={url} target="_blank" rel="noreferrer">
      {label}
    </a>
  ) : (
    <span>链接不可用</span>
  );

function App() {
  const [token, setToken] = useState(""),
    [entry, setEntry] = useState(""),
    [page, setPage] = useState("今日工作台");
  const [overview, setOverview] = useState<Row | null>(null),
    [leads, setLeads] = useState<Row[]>([]),
    [messages, setMessages] = useState<Row[]>([]),
    [activity, setActivity] = useState<Row>({ events: [], runs: [] });
  const [error, setError] = useState(""),
    [note, setNote] = useState(""),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<Row | null>(null),
    [search, setSearch] = useState("");
  const [edit, setEdit] = useState<Row | null>(null),
    [contact, setContact] = useState({
      address: "",
      source_url: "",
      basis: "",
    });
  const [feedback, setFeedback] = useState(""),
    [takeover, setTakeover] = useState(false),
    [review, setReview] = useState<Row | null>(null);
  async function api(
    path: string,
    body?: unknown,
    method = "POST",
    authToken = token,
  ) {
    const r = await fetch("/api" + path, {
      method: body === undefined ? "GET" : method,
      headers: {
        Authorization: `Bearer ${authToken}`,
        "Content-Type": "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok)
      throw Error(
        typeof data.detail === "string"
          ? data.detail
          : JSON.stringify(data.detail),
      );
    return data;
  }
  async function refresh(authToken = token) {
    const [o, l, m, a] = await Promise.all([
      api("/overview", undefined, "GET", authToken),
      api("/leads", undefined, "GET", authToken),
      api("/messages", undefined, "GET", authToken),
      api("/activity", undefined, "GET", authToken),
    ]);
    setOverview(o);
    setLeads(l);
    setMessages(m);
    setActivity(a);
  }
  async function act(fn: () => Promise<unknown>, success = "已保存") {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await fn();
      await refresh();
      setNote(success);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }
  async function openLead(id: number) {
    const l = await api("/leads/" + id);
    setSelected(l);
    setFeedback(l.feedback);
    setTakeover(l.human_takeover);
    setEdit(null);
  }
  useEffect(() => {
    if (!token) return;
    const interval = setInterval(() => {
      refresh().catch((e) => setError(e.message));
    }, 15000);
    return () => clearInterval(interval);
  }, [token]);
  const owner = overview?.role === "reviewer";
  if (!overview)
    return (
      <main className="login">
        <div className="brand-mark">青</div>
        <p className="eyebrow">BLUEBIRD / ACQUISITION</p>
        <h1>
          让每次联系
          <br />
          都有据可依。
        </h1>
        <p>从真实需求开始，逐条审核，再建立有价值的对话。</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            refresh(entry)
              .then(() => {
                setToken(entry);
                setEntry("");
              })
              .catch((e) => setError(e.message))
              .finally(() => setBusy(false));
          }}
        >
          <label>
            工作台访问令牌
            <input
              type="password"
              autoComplete="off"
              value={entry}
              onChange={(e) => setEntry(e.target.value)}
              required
              placeholder="输入运维或人工审核令牌"
            />
          </label>
          <button disabled={busy}>进入工作台 →</button>
        </form>
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
        <small>独立测试环境 · 访问令牌仅保存在本页内存</small>
      </main>
    );
  const navigate = (p: string) => {
    setPage(p);
    setSelected(null);
    setEdit(null);
    setReview(null);
  };
  return (
    <div className="shell">
      <aside>
        <div className="logo">
          <b>青</b>
          <div>
            青鸟获客<small>BLUEBIRD WORKSPACE</small>
          </div>
        </div>
        <div className="workspace">
          试运行空间 <span>01</span>
        </div>
        <nav>
          {["今日工作台", "客户池", "消息审核", "渠道与任务", "成本与配置"].map(
            (p, i) => (
              <button
                key={p}
                className={page === p ? "active" : ""}
                onClick={() => navigate(p)}
              >
                <span>{["◈", "◎", "▤", "⇄", "◷"][i]}</span>
                {p}
                {p === "消息审核" && (
                  <em>
                    {
                      messages.filter((m) => m.status === "pending_review")
                        .length
                    }
                  </em>
                )}
              </button>
            ),
          )}
        </nav>
        <div className="side-bottom">
          <span className="dot" />
          小规模验证阶段
          <p>
            真实需求优先
            <br />
            发送由你决定
          </p>
          <button
            className="quiet"
            onClick={() => {
              setToken("");
              setOverview(null);
            }}
          >
            退出工作台 ↗
          </button>
        </div>
      </aside>
      <div className="main">
        <header>
          <span>
            获客空间 <i>/</i> {page}
          </span>
          <div>
            {badge(owner ? "所有者审核" : "运维查看")}
            <span className="muted">北京时间 · 15 秒刷新</span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <p className="eyebrow">ACQUISITION OPERATIONS</p>
              <h1>{page}</h1>
              <p className="muted">
                {page === "今日工作台"
                  ? "把注意力留给有证据的需求。"
                  : page === "消息审核"
                    ? "完整内容、明确对象、逐条确认。"
                    : "以实际记录为准，持续验证获客效果。"}
              </p>
            </div>
            <button
              className={overview.paused ? "secondary" : "danger"}
              disabled={busy || (!owner && overview.paused)}
              onClick={() =>
                act(() => api("/control/pause", { paused: !overview.paused }))
              }
            >
              {overview.paused ? "恢复已审核队列" : "暂停全部发送"}
            </button>
          </div>
          <div className="notice">
            <span>◉</span>
            <div>
              <strong>
                {overview.real_send_enabled
                  ? "真实发送已配置"
                  : "当前为测试环境，真实发送关闭"}
              </strong>
              <p>
                {overview.paused ? "全局队列暂停。" : "队列运行中。"}{" "}
                模拟结果单独标记；SMTP 接受不代表送达。
              </p>
            </div>
          </div>
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
          {page === "今日工作台" && (
            <>
              <div className="stats">
                {[
                  [
                    "候选客户",
                    overview.counts.leads,
                    "待验证需求，不代表有效商机",
                  ],
                  [
                    "等待审核",
                    overview.message_statuses.pending_review || 0,
                    "每条内容都需要你确认",
                  ],
                  [
                    "真实已提交",
                    overview.message_statuses.submitted || 0,
                    "不推断送达或已读",
                  ],
                  [
                    "模型分析调用",
                    overview.cost.model_calls,
                    "规则初筛不消耗模型 Token",
                  ],
                ].map(([label, value, desc]) => (
                  <section key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                    <small>{desc}</small>
                  </section>
                ))}
              </div>
              <div className="columns">
                <section className="panel">
                  <div className="section-title">
                    <h2>待办与接入</h2>
                    {badge("逐步验证")}
                  </div>
                  {[
                    [
                      "01",
                      "核实真实需求",
                      "打开候选客户原文，判断是否需要模型 API。",
                    ],
                    [
                      "02",
                      "补全商务联系证据",
                      "仅添加公开商务渠道，地址需人工核实。",
                    ],
                    [
                      "03",
                      "连接模型分析",
                      "待模型名称、密钥、单价配置及小样本验证。",
                    ],
                    [
                      "04",
                      "开启真实发送",
                      "先完成账号验证与发布审核，再逐条批准消息。",
                    ],
                  ].map(([n, t, d]) => (
                    <div className="todo" key={n}>
                      <span>{n}</span>
                      <div>
                        <h3>{t}</h3>
                        <p>{d}</p>
                      </div>
                    </div>
                  ))}
                </section>
                <section className="panel">
                  <div className="section-title">
                    <h2>最近采集</h2>
                    <button className="text" onClick={() => navigate("客户池")}>
                      客户池 ↗
                    </button>
                  </div>
                  {leads.slice(0, 4).map((l) => (
                    <button
                      className="lead-mini"
                      key={l.id}
                      onClick={() => {
                        navigate("客户池");
                        act(() => openLead(l.id), "");
                      }}
                    >
                      <small>{l.source}</small>
                      <h3>{l.title}</h3>
                      <span>{date(l.published_at)}</span>
                    </button>
                  ))}
                  {leads.length === 0 && (
                    <Empty
                      title="等待第一批真实线索"
                      text="在渠道与任务中运行一次小规模采集；没有匹配内容时不会生成虚假线索。"
                    />
                  )}
                </section>
              </div>
              <div className="columns">
                <section className="panel">
                  <h2>统一收件箱</h2>
                  <Empty
                    title="收件同步尚未接通"
                    text="待确认邮箱及同步权限。当前没有回复统计，不以模拟内容填充。"
                  />
                </section>
                <section className="panel">
                  <h2>转化进展</h2>
                  <div className="funnel">
                    线索 → 沟通 → 注册 → 调用 → 付费 → 留存
                  </div>
                  <p className="muted">
                    业务平台事件未接入，暂不计算转化率或客户毛利。
                  </p>
                </section>
              </div>
            </>
          )}
          {page === "客户池" && !selected && (
            <section className="panel">
              <div className="section-title">
                <h2>
                  需求候选 <small>{leads.length} 条</small>
                </h2>
                <input
                  className="search"
                  placeholder="搜索标题或需求"
                  aria-label="搜索客户"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>客户 / 需求证据</th>
                      <th>判断方式</th>
                      <th>来源时间</th>
                      <th>状态</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {leads
                      .filter((l) =>
                        (l.title + l.text)
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                      )
                      .map((l) => (
                        <tr key={l.id}>
                          <td>
                            <strong>{l.title}</strong>
                            <small>
                              {l.author} · {l.source}
                            </small>
                          </td>
                          <td>
                            {badge(
                              l.analysis.method === "ai"
                                ? "AI 已分析"
                                : "规则初筛",
                            )}
                            <small>
                              采购阶段：
                              {l.analysis.purchase_stage === "unknown"
                                ? "未知"
                                : l.analysis.purchase_stage}
                            </small>
                          </td>
                          <td>{date(l.published_at)}</td>
                          <td>{l.status}</td>
                          <td>
                            <button
                              className="text"
                              onClick={() => act(() => openLead(l.id), "")}
                            >
                              查看 →
                            </button>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
              {!leads.length && (
                <Empty
                  title="还没有匹配的候选客户"
                  text="采集结果会在这里出现。"
                />
              )}
            </section>
          )}
          {page === "客户池" && selected && (
            <>
              <button className="text" onClick={() => setSelected(null)}>
                ← 返回客户池
              </button>
              <div className="columns detail">
                <section className="panel">
                  <p className="eyebrow">
                    {selected.source} / {selected.author}
                  </p>
                  <h2>{selected.title}</h2>
                  <p>
                    {link(selected.url)} · 发布时间{" "}
                    {date(selected.published_at)}
                  </p>
                  <pre className="source">{selected.text}</pre>
                  <div className="section-title">
                    <h2>需求判断</h2>
                    {badge(
                      selected.analysis.method === "ai"
                        ? "AI 分析"
                        : "规则初筛，非 AI",
                    )}
                  </div>
                  <p>{selected.analysis.summary}</p>
                  <p className="muted">
                    未知：{selected.analysis.unknowns?.join("、")}
                  </p>
                  <p>建议：{selected.analysis.next_action}</p>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await api(`/leads/${selected.id}/analyze`, {});
                        await openLead(selected.id);
                      })
                    }
                  >
                    使用已配置模型分析
                  </button>
                  <hr />
                  <label>
                    人工反馈 / 退回原因
                    <textarea
                      value={feedback}
                      onChange={(e) => setFeedback(e.target.value)}
                      placeholder="记录误判、缺失证据或后续跟进信息"
                    />
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={takeover}
                      onChange={(e) => setTakeover(e.target.checked)}
                    />
                    人工接管，阻止系统发送
                  </label>
                  <button
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await api(`/leads/${selected.id}/feedback`, {
                          text: feedback,
                          human_takeover: takeover,
                          status: selected.status,
                        });
                        await openLead(selected.id);
                      })
                    }
                  >
                    保存反馈
                  </button>
                </section>
                <section className="panel">
                  <h2>商务联系方式</h2>
                  <p className="muted">
                    联系方式与客户价值分开核实。未经核实不能批准发送。
                  </p>
                  {selected.contacts.map((c: Row) => (
                    <div className="contact" key={c.id}>
                      <strong>{c.address}</strong>
                      <p>{link(c.source_url, "联系方式证据 ↗")}</p>
                      <p>{c.basis}</p>
                      {badge(c.verified ? "人工已核实" : "未核实")}
                      <div className="actions">
                        {!c.verified && (
                          <button
                            disabled={busy || !owner}
                            className="secondary"
                            onClick={() =>
                              act(async () => {
                                await api(`/contacts/${c.id}/verify`, {});
                                await openLead(selected.id);
                              })
                            }
                          >
                            确认公开商务用途
                          </button>
                        )}
                        <button
                          disabled={busy}
                          className="secondary"
                          onClick={() =>
                            act(async () => {
                              await api(`/leads/${selected.id}/draft`, {
                                contact_id: c.id,
                                channel: "simulation",
                              });
                              navigate("消息审核");
                            }, "已生成模拟渠道草稿")
                          }
                        >
                          生成模拟草稿
                        </button>
                        <button
                          disabled={busy}
                          className="text"
                          onClick={() =>
                            act(async () => {
                              await api(`/leads/${selected.id}/draft`, {
                                contact_id: c.id,
                                channel: "smtp",
                              });
                              navigate("消息审核");
                            }, "已生成邮件草稿，尚未批准")
                          }
                        >
                          生成邮件草稿
                        </button>
                        <button
                          className="text danger-text"
                          onClick={() =>
                            act(
                              () =>
                                api("/suppressions", {
                                  address: c.address,
                                  reason: "所有者手动拒收",
                                }),
                              "已加入拒收名单",
                            )
                          }
                        >
                          加入拒收名单
                        </button>
                      </div>
                    </div>
                  ))}
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      act(async () => {
                        await api(`/leads/${selected.id}/contacts`, contact);
                        setContact({ address: "", source_url: "", basis: "" });
                        await openLead(selected.id);
                      });
                    }}
                  >
                    <label>
                      邮箱
                      <input
                        type="email"
                        required
                        value={contact.address}
                        onChange={(e) =>
                          setContact({ ...contact, address: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      公开来源链接
                      <input
                        type="url"
                        required
                        value={contact.source_url}
                        onChange={(e) =>
                          setContact({ ...contact, source_url: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      商务使用依据
                      <textarea
                        required
                        minLength={5}
                        value={contact.basis}
                        onChange={(e) =>
                          setContact({ ...contact, basis: e.target.value })
                        }
                      />
                    </label>
                    <button disabled={busy}>添加为待核实联系方式</button>
                  </form>
                </section>
              </div>
            </>
          )}
          {page === "消息审核" && (
            <>
              <section className="panel">
                <div className="section-title">
                  <h2>逐条审核队列</h2>
                  {badge("附件暂不支持")}
                </div>
                {messages.map((m) => (
                  <article className="message" key={m.id}>
                    <div className="section-title">
                      <div>
                        <h3>{m.subject}</h3>
                        <p>
                          {m.recipient}{" "}
                          <span className="muted">
                            · 客户 #{m.lead_id} · v{m.version}
                          </span>
                        </p>
                      </div>
                      {badge(names[m.status] || m.status)}
                    </div>
                    <dl>
                      <dt>渠道 / 账号</dt>
                      <dd>
                        {m.channel === "simulation"
                          ? "模拟渠道，无外部消息"
                          : `SMTP / ${m.account || "未配置"}`}
                      </dd>
                      <dt>附件</dt>
                      <dd>无</dd>
                      <dt>发送结果</dt>
                      <dd>{m.result || "尚未发送"}</dd>
                    </dl>
                    <pre>{m.body}</pre>
                    {m.block_reason && (
                      <p className="warning">{m.block_reason}</p>
                    )}
                    <div className="actions">
                      <button
                        className="secondary"
                        disabled={busy}
                        onClick={() =>
                          act(async () => {
                            const l = await api("/leads/" + m.lead_id);
                            setReview({
                              ...m,
                              lead: l,
                              contact: l.contacts.find(
                                (c: Row) => c.id === m.contact_id,
                              ),
                            });
                            setEdit(null);
                          }, "")
                        }
                      >
                        查看客户与审核内容
                      </button>
                      {![
                        "sending",
                        "submitted",
                        "sent",
                        "failed",
                        "unknown",
                        "cancelled",
                        "simulated",
                      ].includes(m.status) && (
                        <>
                          <button
                            className="secondary"
                            onClick={() => {
                              setEdit({ ...m });
                              setReview(null);
                            }}
                          >
                            编辑
                          </button>
                          <button
                            className="text"
                            disabled={busy}
                            onClick={() =>
                              act(() =>
                                api(`/messages/${m.id}/cancel`, {
                                  reason: "人工跳过",
                                }),
                              )
                            }
                          >
                            跳过 / 取消
                          </button>
                        </>
                      )}
                      {m.status === "approved" && (
                        <button
                          disabled={busy || overview.paused}
                          onClick={() =>
                            act(
                              () => api(`/messages/${m.id}/queue`, {}),
                              "已进入持久化发送队列",
                            )
                          }
                        >
                          将已确认版本加入队列
                        </button>
                      )}
                    </div>
                  </article>
                ))}
                {!messages.length && (
                  <Empty
                    title="还没有待审核消息"
                    text="先在客户档案添加有来源的联系方式，再生成草稿。AI 和模板都不会自动批准。"
                  />
                )}
              </section>
              {edit && (
                <div className="modal" role="dialog" aria-label="编辑草稿">
                  <section className="panel">
                    <h2>编辑草稿 · v{edit.version}</h2>
                    <p className="warning">保存后原批准失效，必须重新审核。</p>
                    <label>
                      主题
                      <input
                        value={edit.subject}
                        maxLength={200}
                        onChange={(e) =>
                          setEdit({ ...edit, subject: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      完整正文
                      <textarea
                        rows={8}
                        value={edit.body}
                        onChange={(e) =>
                          setEdit({ ...edit, body: e.target.value })
                        }
                      />
                    </label>
                    <div className="actions">
                      <button
                        disabled={busy}
                        onClick={() =>
                          act(async () => {
                            await api(
                              `/messages/${edit.id}`,
                              {
                                version: edit.version,
                                subject: edit.subject,
                                body: edit.body,
                                contact_id: edit.contact_id,
                                channel: edit.channel,
                                attachments: [],
                              },
                              "PUT",
                            );
                            setEdit(null);
                          })
                        }
                      >
                        保存新版本
                      </button>
                      <button
                        className="secondary"
                        onClick={() => setEdit(null)}
                      >
                        关闭
                      </button>
                    </div>
                  </section>
                </div>
              )}
              {review && (
                <div className="modal" role="dialog" aria-label="完整消息审核">
                  <section className="panel">
                    <h2>完整审核 · 客户 #{review.lead_id}</h2>
                    <h3>{review.lead.title}</h3>
                    <p>{link(review.lead.url, "原始需求 ↗")}</p>
                    <pre className="source">{review.lead.text}</pre>
                    <p>建议依据：{review.lead.analysis.summary}</p>
                    <p>
                      联系方式：{review.recipient} ·{" "}
                      {link(review.contact?.source_url || "", "来源 ↗")}
                    </p>
                    <p>
                      {review.contact?.basis} ·{" "}
                      {review.contact?.verified ? "已核实" : "未核实"}
                    </p>
                    <p>
                      渠道：{review.channel} / 账号：
                      {review.account || "未配置"} / 附件：无 / v
                      {review.version}
                    </p>
                    <h3>{review.subject}</h3>
                    <pre>{review.body}</pre>
                    {review.block_reason && (
                      <p className="warning">{review.block_reason}</p>
                    )}
                    <div className="actions">
                      <button
                        disabled={
                          busy ||
                          !owner ||
                          !!review.block_reason ||
                          review.status !== "pending_review"
                        }
                        onClick={() =>
                          act(async () => {
                            await api(`/messages/${review.id}/approve`, {
                              version: review.version,
                              content_hash: review.content_hash,
                            });
                            setReview(null);
                          }, "已确认这个具体内容版本；尚未加入发送队列")
                        }
                      >
                        确认此收件人及完整内容
                      </button>
                      <button
                        className="secondary"
                        onClick={() => setReview(null)}
                      >
                        返回
                      </button>
                    </div>
                  </section>
                </div>
              )}
            </>
          )}
          {page === "渠道与任务" && (
            <>
              <section className="panel">
                <div className="section-title">
                  <div>
                    <p className="eyebrow">SOURCE 01</p>
                    <h2>Hacker News · Ask</h2>
                  </div>
                  {badge(
                    activity.runs.some((r: Row) => r.status === "success")
                      ? "读取已验证"
                      : "读取待验证",
                  )}
                </div>
                <p>
                  官方公开
                  API，小批量需求筛选。英文社区仅用于验证采集流程，国内客户适配尚待评估。
                </p>
                <div className="capabilities">
                  读取公开帖子 ✓ <span>商务联系方式：人工核实</span>
                  <span>主动联系 / 回复：不支持</span>
                </div>
                <p className="muted">
                  每小时最多读取一批新条目；已处理 ID
                  命中缓存。发布时间保留，不把旧需求当作新需求。
                </p>
                <button
                  disabled={busy}
                  onClick={() =>
                    act(
                      () => api("/sources/hackernews/sync", {}),
                      "采集已排队，结果会在任务记录中更新",
                    )
                  }
                >
                  运行一次小规模采集
                </button>{" "}
                <a
                  href="https://github.com/HackerNews/API"
                  target="_blank"
                  rel="noreferrer"
                >
                  官方资料 ↗
                </a>
              </section>
              <section className="panel">
                <h2>真实任务记录</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>开始时间</th>
                        <th>渠道</th>
                        <th>状态</th>
                        <th>实际处理结果</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activity.runs.map((r: Row) => (
                        <tr key={r.id}>
                          <td>{date(r.started_at)}</td>
                          <td>{r.channel}</td>
                          <td>{r.status}</td>
                          <td>
                            新增 {r.stats.new_leads ?? "—"} · 过滤{" "}
                            {r.stats.filtered ?? "—"} · 缓存{" "}
                            {r.stats.cache_hits ?? "—"}
                            <small>{r.error}</small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="panel">
                <h2>审计事件</h2>
                {activity.events.map((e: Row) => (
                  <div className="event" key={e.id}>
                    <span>{date(e.created_at)}</span>
                    <strong>{e.kind}</strong>
                    <small>
                      #{e.entity} {JSON.stringify(e.detail)}
                    </small>
                  </div>
                ))}
              </section>
            </>
          )}
          {page === "成本与配置" && (
            <div className="columns">
              <section className="panel">
                <h2>运行成本与限额</h2>
                <div className="cost-number">
                  ${overview.cost.estimated_usd.toFixed(6)}
                  <small>按供应商 Token 用量及配置价格估算</small>
                </div>
                <p>
                  费用未决预占：$
                  {overview.cost.unresolved_reserved_usd.toFixed(6)}
                </p>
                <p>
                  每日试运行上限：${overview.cost.daily_budget} / 每月：$
                  {overview.cost.monthly_budget}
                </p>
                <p className="muted">
                  用户允许的
                  $10,000/月为最高上限，不是消费目标。价格缺失时不发起模型调用。
                </p>
                <hr />
                <p>开发迭代费用：{overview.cost.development_cost}</p>
                <p>客户 API 供给成本：另行核算，当前未接入。</p>
                <p>有效商机成本 / 转化率：样本和业务事件不足，暂无结论。</p>
              </section>
              <section className="panel">
                <h2>已确认产品事实</h2>
                <p>{overview.product.description}</p>
                <p className="muted">
                  依据：{overview.product.evidence} · v
                  {overview.product.version}
                </p>
                <h3>仍待确认</h3>
                <p>{overview.product.unknown.join("、")}</p>
                <hr />
                <h2>接入状态</h2>
                {Object.entries(overview.integrations).map(([key, value]) => (
                  <div className="config-row" key={key}>
                    <span>
                      {
                        {
                          ai: "AI 分析",
                          smtp: "邮件发送",
                          inbox: "收件同步",
                          conversion: "转化事件",
                        }[key]
                      }
                    </span>
                    {badge(
                      value === "not_configured"
                        ? "待配置"
                        : value === "not_connected"
                          ? "未接通"
                          : "已配置，待实测",
                    )}
                  </div>
                ))}
                <p className="muted">
                  密钥通过服务器环境配置管理，工作台不会返回密钥。
                </p>
              </section>
            </div>
          )}
          <footer>
            青鸟获客 / 证据驱动的商务联系{" "}
            <span>试运行 v0.1 · 不确定信息保留为未知</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty">
      <span>◇</span>
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
