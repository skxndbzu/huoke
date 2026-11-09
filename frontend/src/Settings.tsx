import React, { useEffect, useState } from "react";
type R = Record<string, any>;
type API = (path: string, body?: unknown, method?: string) => Promise<any>;
export function OwnerAccount({
  api,
  owner,
  onLogout,
}: {
  api: API;
  owner: boolean;
  onLogout: () => void;
}) {
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [current, setCurrent] = useState(""),
    [configured, setConfigured] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/auth/status")
      .then((d) => setConfigured(d.password_configured))
      .catch((e) => setError(e.message));
  }, []);
  if (!owner) return null;
  return (
    <section className="panel">
      <h2>账号与密码</h2>
      <p>
        首次用审核令牌登录后，在这里设置你自己的账号密码。以后不必去服务器查令牌。登录会话最长
        12 小时，密码修改后旧会话失效。
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            await api("/auth/setup", {
              username,
              password,
              current_password: current,
            });
            setPassword("");
            setCurrent("");
            onLogout();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          用户名
          <input
            autoComplete="username"
            minLength={3}
            maxLength={64}
            pattern="[a-zA-Z0-9_.\-]+"
            required
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        {configured && (
          <label>
            当前密码
            <input
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </label>
        )}
        <label>
          新密码（至少 12 个字符）
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button disabled={busy}>保存并重新登录</button>
      </form>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <p className="muted">
        当前 IP 入口使用 HTTP，连接未加密。也可继续通过 SSH 隧道访问本地入口。
      </p>
    </section>
  );
}

const initial = {
  name: "",
  base_url: "",
  model: "gpt-6.1-sol",
  api_key: "",
  input_price: 2,
  output_price: 10,
  cached_price: 0.1,
  price_source:
    "https://developers.openai.com/api/docs/pricing （2026-11-10 标准价格；作为估算，非服务商账单）",
  allow_http: false,
  version: 0,
};
export function ModelSettings({ api, owner }: { api: API; owner: boolean }) {
  const [providers, setProviders] = useState<R[]>([]),
    [edit, setEdit] = useState<R | null>(null),
    [error, setError] = useState(""),
    [note, setNote] = useState(""),
    [busy, setBusy] = useState(false),
    [models, setModels] = useState<string[]>([]);
  const load = () => api("/providers").then(setProviders);
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, []);
  async function action(fn: () => Promise<any>, msg: string) {
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
  return (
    <section className="panel">
      <div className="section-title">
        <h2>获客分析模型</h2>
        {owner && (
          <button
            onClick={() => {
              setEdit({ ...initial });
              setModels([]);
            }}
          >
            添加模型服务
          </button>
        )}
      </div>
      <p>
        先保存服务，验证模型列表，再启用。系统每小时最多分析 2
        条尚未分析的候选；预算仍为单次 $1、每日 $5、每月
        $100。调用失败或费用未知不会自动重试。
      </p>
      {providers.map((p) => (
        <div className="provider" key={p.id}>
          <div className="section-title">
            <h3>
              {p.name} · {p.model}
            </h3>
            <span className="badge">{p.active ? "当前使用" : "未启用"}</span>
          </div>
          <p>
            {p.base_url} · 密钥{p.key_configured ? "已安全保存" : "未配置"}
          </p>
          <p>
            每百万 Token：输入 ${p.input_price} / 缓存输入 ${p.cached_price} /
            输出 ${p.output_price}
          </p>
          <small>{p.price_source}</small>
          <p>
            接口验证：{p.probe_status} · 分析：
            {p.analysis_verified ? "真实样本已验证" : "尚未验证"}
          </p>
          {owner && (
            <div className="actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={() => {
                  setEdit({ ...p, api_key: "" });
                  setModels([]);
                }}
              >
                编辑
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    const d = await api("/providers/" + p.id + "/probe", {});
                    setModels(d.models);
                    if (d.status !== "models_verified")
                      throw Error("验证结果：" + d.status);
                  }, "模型列表验证成功，尚不代表推理成功")
                }
              >
                验证模型列表
              </button>
              <button
                disabled={
                  busy || p.active || p.probe_status !== "models_verified"
                }
                onClick={() =>
                  action(
                    () => api("/providers/" + p.id + "/activate", {}),
                    "已切换后续分析使用的服务",
                  )
                }
              >
                启用此配置
              </button>
            </div>
          )}
        </div>
      ))}
      {!providers.length && <p className="muted">尚未保存动态模型服务。</p>}
      {edit && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            action(async () => {
              const {
                name,
                base_url,
                model,
                api_key,
                input_price,
                output_price,
                cached_price,
                price_source,
                allow_http,
                version,
              } = edit;
              await api(
                edit.id ? "/providers/" + edit.id : "/providers",
                {
                  name,
                  base_url,
                  model,
                  api_key,
                  input_price: Number(input_price),
                  output_price: Number(output_price),
                  cached_price: Number(cached_price),
                  price_source,
                  allow_http,
                  version,
                },
                edit.id ? "PUT" : "POST",
              );
              setEdit(null);
            }, "已保存。配置变更后需要重新验证并启用。");
          }}
        >
          <h3>{edit.id ? "编辑模型服务" : "新增模型服务"}</h3>
          <label>
            名称
            <input
              required
              value={edit.name}
              onChange={(e) => setEdit({ ...edit, name: e.target.value })}
            />
          </label>
          <label>
            API 地址（包含 /v1）
            <input
              required
              type="url"
              placeholder="https://service.example/v1"
              value={edit.base_url}
              onChange={(e) => setEdit({ ...edit, base_url: e.target.value })}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={edit.allow_http}
              onChange={(e) =>
                setEdit({ ...edit, allow_http: e.target.checked })
              }
            />
            允许使用 HTTP 上游（不加密）
          </label>
          <label>
            模型 ID
            <input
              required
              list="available-models"
              value={edit.model}
              onChange={(e) => setEdit({ ...edit, model: e.target.value })}
            />
            <datalist id="available-models">
              {models.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </datalist>
          </label>
          <label>
            {edit.id ? "替换密钥（留空保留原值）" : "API 密钥"}
            <input
              type="password"
              autoComplete="new-password"
              required={!edit.id}
              value={edit.api_key}
              onChange={(e) => setEdit({ ...edit, api_key: e.target.value })}
            />
          </label>
          <div className="filters">
            {[
              ["input_price", "输入单价"],
              ["cached_price", "缓存输入单价"],
              ["output_price", "输出单价"],
            ].map(([k, t]) => (
              <label key={k}>
                {t}（美元 / 百万 Token）
                <input
                  type="number"
                  min={k === "cached_price" ? 0 : 0.000001}
                  step="any"
                  required
                  value={edit[k]}
                  onChange={(e) => setEdit({ ...edit, [k]: e.target.value })}
                />
              </label>
            ))}
          </div>
          <label>
            价格依据
            <input
              required
              value={edit.price_source}
              onChange={(e) =>
                setEdit({ ...edit, price_source: e.target.value })
              }
            />
          </label>
          <p className="muted">
            切换模型时请核对对应价格；表单默认值只适用于 gpt-6.1-sol 标准价格。
          </p>
          <div className="actions">
            <button disabled={busy}>保存配置</button>
            <button
              type="button"
              className="secondary"
              onClick={() => setEdit(null)}
            >
              取消
            </button>
          </div>
        </form>
      )}
      {models.length > 0 && !edit && (
        <details>
          <summary>服务返回的模型列表</summary>
          <p>{models.join("、")}</p>
        </details>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {note && <p className="success">{note}</p>}
    </section>
  );
}
