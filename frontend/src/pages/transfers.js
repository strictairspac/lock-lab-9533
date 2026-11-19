import {table} from '../components/common/table.js';
import {esc} from '../utils/html.js';
import {session} from '../hooks/session.js';
import {SIGNOFF_STATES,RECON_STATES,PENDING_REASONS} from '../constants/enums.js';

const readonly=()=>session.user.role==='viewer'?'disabled':'';
const reasonText=r=>PENDING_REASONS[r]||r;

function applicationsTable(items,samples){
  return table(
    ['申请编号 / 版本','样本','申请量','已核 / 签收','待核原因'],
    items.map(t=>{
      const sample=samples.find(x=>x.id===t.sample_id);
      const overflow=t.verified_qty>t.quantity;
      const reasons=(t.pending_reasons||[]).map(r=>`${esc(r.label)} × ${r.count}`).join('；');
      return [
        `<strong>${esc(t.code)}</strong><small>v${t.version} · ${esc(t.note||'')}</small>`,
        esc(sample?.code),
        `${t.quantity} 份`,
        `<b class="quantity ${overflow?'com':''}">${t.verified_qty}</b> / ${t.signoff_qty} 份`
          +(t.pending_count?`<small>${t.pending_count} 笔待核</small>`:'<small>全部核销</small>'),
        reasons?`<span class="badge pending">${esc(reasons)}</span>`:'<span class="badge completed">无</span>',
      ];
    })
  );
}

function signoffsTable(items){
  if(!items.length) return '<div class="empty">还没有签收单。签收单可先于移交申请登记，申请补齐后自动核销。</div>';
  return table(
    ['签收单号','关联申请','签收量','已核数量','状态 / 待核原因'],
    items.map(s=>[
      `<strong>${esc(s.code)}</strong>`,
      `${esc(s.transfer_code)}${s.has_transfer?'':'<small>申请未到</small>'}`,
      `${s.quantity} 份`,
      `<b class="quantity">${s.verified_qty}</b> 份`,
      s.status==='verified'
        ? '<span class="badge completed">已核</span>'
        : `<span class="badge pending">待核</span><small>${esc(s.pending_reason_label||reasonText(s.pending_reason))}</small>`,
    ])
  );
}

function reconPanel(jobs){
  const active=jobs.filter(j=>['queued','running'].includes(j.status));
  const failed=jobs.filter(j=>j.status==='failed');
  const rows=jobs.slice(0,8).map(j=>[
    esc(j.transfer_code),
    `<span class="badge ${esc(j.status)}">${esc(RECON_STATES[j.status]||j.status)}</span>`,
    `${j.attempts} 次`,
    j.error?`<small>${esc(j.error)}</small>`:'—',
    j.status==='failed'
      ? `<button class="link" data-retry="${j.id}" ${readonly()}>重试</button>`:'',
  ]);
  return `<div class="section-top"><h2>对账任务</h2><span>${active.length?active.length+' 个进行中':'队列空闲'}${failed.length?` · ${failed.length} 个失败`:''}</span></div>`
    + (rows.length?table(['申请','状态','尝试','错误','操作'],rows):'<div class="empty">暂无对账任务。</div>');
}

export function transfersPage(items,samples,signoffs,jobs){
  const transferOptions=items.map(t=>`<option value="${t.id}">${esc(t.code)} · v${t.version} · 当前 ${t.quantity} 份</option>`).join('');
  return `<div class="split">
  <section>
    <div class="section-top"><h2>交接申请簿</h2><span>${items.length} 条 / 版本化申请</span></div>
    ${applicationsTable(items,samples)}
    <div class="notice">更正按编号认最新版本：已核数量不回退；超量签收保留原已核数量并标待核，腾出额度后自动重算。</div>
    <div class="section-top" style="margin-top:24px"><h2>签收核销台</h2><span>${signoffs.length} 笔签收单</span></div>
    ${signoffsTable(signoffs)}
    <div style="margin-top:24px">${reconPanel(jobs)}</div>
  </section>
  <section class="form-panel">
    <span class="eyebrow">NEW TRANSFER</span>
    <h2>新建流转申请</h2>
    <form id="transfer-form">
      <label>申请编号<input name="code" placeholder="shift-20261117" required></label>
      <label>样本批次<select name="sample_id">${samples.map(s=>`<option value="${s.id}">${esc(s.code)} · ${s.quantity} 份</option>`).join('')}</select></label>
      <div class="fields">
        <label>移交份数<input name="quantity" type="number" min="1" value="30" required></label>
        <label>接收实验室<input name="destination" value="理化检测室" required></label>
      </div>
      <label>交接备注<input name="note" value="09:15 交接班复核"></label>
      <button class="primary" ${readonly()}>保存流转申请 +</button>
    </form>

    <span class="eyebrow" style="margin-top:28px">REGISTER SIGNOFF</span>
    <h2>登记签收单</h2>
    <form id="signoff-form">
      <label>签收单号<input name="code" placeholder="SO-20261117-01" required></label>
      <label>关联申请编号<input name="transfer_code" placeholder="MOVE-1117-01（申请未到也可登记）" required></label>
      <label>签收份数<input name="quantity" type="number" min="1" value="10" required></label>
      <button class="primary" ${readonly()}>登记签收 +</button>
    </form>
    <p class="help">同一申请的多笔签收可乱序到达；先于申请登记的签收单在申请补齐后由对账 worker 自动核销。</p>

    <span class="eyebrow" style="margin-top:28px">CORRECT TRANSFER</span>
    <h2>更正申请（新版本）</h2>
    <form id="correction-form">
      <label>选择申请<select name="transfer_id" required>${transferOptions||'<option value="">暂无申请</option>'}</select></label>
      <div class="fields">
        <label>更正后份数<input name="quantity" type="number" min="1" value="30" required></label>
        <label>接收实验室<input name="destination" value="理化检测室" required></label>
      </div>
      <label>更正备注<input name="note" value="按最新版本调整额度"></label>
      <button class="primary" ${readonly()}>提交更正（版本 +1）</button>
    </form>
  </section>
</div>`;
}
