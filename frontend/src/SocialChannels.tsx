import React, { useEffect, useState } from 'react';
type R = Record<string, any>;
type API = (path: string, body?: unknown, method?: string) => Promise<any>;
const labels: R = {not_connected:'尚未连接',success:'最近读取成功',imported:'已导入，非自动接通',running:'采集中',failed:'读取失败',unavailable:'执行器不可用',login_required:'等待登录',verification_required:'需要平台验证',interrupted:'上轮中断',config_changed:'配置已变化'};
const errors: R = {upstream_auth_token_required:'QQ 执行器要求项目方签发的授权令牌，QQ 号本身不能代替授权',windows_browser_helper_blocked:'Windows 安全软件阻止了小红书的浏览器辅助程序，已停止运行',ConnectError:'服务器网络未连接成功，可使用已验证的本地补采',HTTP_403:'平台要求完成正常访问验证',HTTP_429:'平台暂时限流，请等待',account_mismatch:'执行器登录账号与采集配置不一致',conversation_mismatch:'读取到的会话与所选范围不一致，已拒绝采集'};
const time = (s: string) => s ? new Date(s).toLocaleString('zh-CN') : '尚无';
export function SocialChannels({api}: {api: API}) {
  const [channels, setChannels] = useState<R[]>([]), [edit, setEdit] = useState<R|null>(null),
    [selected, setSelected] = useState<R|null>(null), [busy, setBusy] = useState(false),
    [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [input, setInput] = useState({url:'',author:'',title:'',text:''});
  const load = () => api('/social/channels').then(setChannels);
  useEffect(()=>{load().catch(e=>setError(e.message)); const id=setInterval(()=>load().catch(e=>setError(e.message)),15000);return ()=>clearInterval(id)},[]);
  useEffect(()=>{document.getElementById('social-editor')?.scrollIntoView({block:'start',behavior:'smooth'})},[edit?.platform,selected?.platform]);
  async function act(fn:()=>Promise<string>) {
    setBusy(true);setError('');setNotice('');
    try {const message=await fn();await load();setNotice(message)} catch(e){setError((e as Error).message)} finally{setBusy(false)}
  }
  return <section aria-label="社交渠道接入">
    <h2>国内社交渠道</h2>
    <p>先按关键词、需求规则和缓存去重，再进入现有 AI 分析队列。此批连接器仅采集；你核实后维护联系，自动发送尚未接通。</p>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status" className="success">{notice}</p>}
    <div className="columns">{channels.map(c=><article className="panel" key={c.platform}>
      <div className="section-title"><h3>{c.name}</h3><span className="badge">{labels[c.status] || c.status}</span></div>
      <p>{c.description}</p>
      <p>采集{c.enabled?'已启用':'已停用'} · {c.stats.capture_mode==='executor'?'本地补采（电脑需在线）':c.mode==='feed'?'服务器每小时读取':c.mode==='executor'?'需要执行器在线':'仅授权内容导入'}</p>
      <p>最后自动读取成功：{time(c.last_success)}<br/>最近尝试：{time(c.last_attempt)}</p>
      {c.error && <p className="error">{errors[c.error] || c.error}。{c.status==='verification_required'?'请在平台正常完成验证，系统不会绕过。':c.status==='login_required'?'请在对应执行器上完成账号登录。':errors[c.error]?'':'请检查执行器或网络连接。'}</p>}
      <p>最近一轮：读取 {c.stats.listed ?? 0} · 筛除 {c.stats.filtered ?? 0} · 去重 {c.stats.cache_hits ?? 0} · 新候选 {c.stats.new_leads ?? 0}</p>
      {!!c.stats.filtered_samples?.length && <details><summary>抽查筛除样本（最多 5 条）</summary>{c.stats.filtered_samples.map((x:R,i:number)=><p key={i}>{x.title} · {{older_than_90_days:'超过 90 天',excluded_keyword:'命中排除词',no_keyword:'未命中关键词',no_demand_evidence:'未发现明确需求'}[x.reason as string] || x.reason}{x.url && <> · <a href={x.url} target="_blank" rel="noreferrer">原文 ↗</a></>}</p>)}<p>如发现漏判，可调整关键词或将原文作为反馈保留；筛除不等于确认没有商业价值。</p></details>}
      <div className="actions">
        <button className="secondary" disabled={busy} onClick={()=>{setEdit({...c,queryText:c.queries.join('\n'),excludeText:c.exclude.join('\n'),conversationText:c.conversations.join('\n')});setSelected(null)}}>配置 {c.name}</button>
        {c.mode==='feed' && <button disabled={busy || !c.enabled || new Date(c.next_allowed_at).getTime()>Date.now()} onClick={()=>act(async()=>{await api('/social/channels/'+c.platform+'/sync',{});return '任务已排队；请查看实际读取结果'})}>立即读取 {c.name}</button>}
        {!['qq','wechat'].includes(c.platform) && <button className="text" disabled={busy || !c.enabled} onClick={()=>{setSelected(c);setEdit(null);setInput({url:'',author:'',title:'',text:''})}}>导入 {c.name} 原文</button>}
      </div>
      <details><summary>接入方式与当前限制</summary><p>{c.adapter} · <a href={c.repo} target="_blank" rel="noreferrer">上游项目 ↗</a></p>
        {c.mode==='executor' && <p>执行器需要在已登录的平台机器运行；配置采集范围后启动。微信只读可见消息，QQ 每分钟最多读取每会话最近 30 条，活跃群可能漏读。</p>}
        <p>平台登录、读取成功、发现有效需求是不同状态。导入不代表自动渠道已接通；没有发送/送达验证。</p>
      </details>
    </article>)}</div>
    {edit && <section id="social-editor" className="panel" aria-label={'配置 '+edit.name}>
      <h3>配置 {edit.name}</h3><form onSubmit={e=>{e.preventDefault();act(async()=>{
        const lines=(x:string)=>x.split('\n').map(x=>x.trim()).filter(Boolean);
        await api('/social/channels/'+edit.platform,{version:edit.version,enabled:edit.enabled,queries:lines(edit.queryText),exclude:lines(edit.excludeText),account:edit.account,conversations:lines(edit.conversationText)},'PUT');setEdit(null);return '配置已保存；执行器下轮会重新读取范围'
      })}}>
        <label className="check"><input type="checkbox" checked={edit.enabled} onChange={e=>setEdit({...edit,enabled:e.target.checked})}/>启用此渠道采集或导入</label>
        <label>需求关键词（每行一组，最多 3 组；任一命中后再筛选）<textarea required value={edit.queryText} onChange={e=>setEdit({...edit,queryText:e.target.value})}/></label>
        <label>排除词（每行一个）<textarea value={edit.excludeText} onChange={e=>setEdit({...edit,excludeText:e.target.value})}/></label>
        {['qq','wechat'].includes(edit.platform) && <>
          <label>本机采集账号{edit.platform==='qq'?'（QQ号）':'标识（人工核对登录账号）'}<input value={edit.account} onChange={e=>setEdit({...edit,account:e.target.value})}/></label>
          <label>{edit.platform==='qq'?'允许读取的会话，每行 group:群号 或 friend:QQ号':'允许读取的微信会话名称，每行一个；同名会话请先区分备注'}<textarea value={edit.conversationText} onChange={e=>setEdit({...edit,conversationText:e.target.value})}/></label>
          <p>最多 10 个会话。只采集选定范围，不自动加好友或入群；微信 OCR 的身份需要进一步核实。</p>
        </>}
        <div className="actions"><button disabled={busy}>保存渠道配置</button><button type="button" className="secondary" onClick={()=>setEdit(null)}>取消</button></div>
      </form>
    </section>}
    {selected && <section id="social-editor" className="panel"><h3>导入 {selected.name} 已授权原文</h3>
      <p>用于你已获得使用授权的内容。按相同规则筛选，未命中的内容不会变成候选；不会发消息。</p>
      <form onSubmit={e=>{e.preventDefault();act(async()=>{
        const result=await api('/social/channels/'+selected.platform+'/capture',{version:selected.version,mode:'manual',items:[{...input,event_id:'manual',author:input.author || '未知'}]});
        setSelected(null);return '导入处理：新增 '+result.new_leads+'，筛除 '+result.filtered+'，去重 '+result.cache_hits;
      })}}>
        <label>平台原文链接（不含查询参数）<input type="url" required value={input.url} onChange={e=>setInput({...input,url:e.target.value})}/></label>
        <label>原文作者（未知可留空）<input maxLength={200} value={input.author} onChange={e=>setInput({...input,author:e.target.value})}/></label>
        <label>标题<input maxLength={500} value={input.title} onChange={e=>setInput({...input,title:e.target.value})}/></label>
        <label>原文正文<textarea required maxLength={12000} value={input.text} onChange={e=>setInput({...input,text:e.target.value})}/></label>
        <div className="actions"><button disabled={busy}>导入并筛选</button><button type="button" className="secondary" onClick={()=>setSelected(null)}>取消</button></div>
      </form></section>}
  </section>
}
