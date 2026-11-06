// Ledger dashboard — vanilla JS, no build step.
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const api = async (p, opt = {}) => {
  const r = await fetch(p, { headers: { 'content-type': 'application/json', 'X-Ledger-Req': '1' }, ...opt, body: opt.body ? JSON.stringify(opt.body) : undefined });
  return r.json();
};
const fmt = (v, d = 0) => v == null ? '—' : (v < 0 ? '−' : '') + '$' + Math.abs(v).toLocaleString('en-CA', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmt2 = v => fmt(v, 2);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const css = n => getComputedStyle($('#root')).getPropertyValue(n).trim();
const mname = (m, long) => new Date(m + '-15').toLocaleString('en-CA', long ? { month: 'long', year: 'numeric' } : { month: 'short', year: '2-digit' });
const dshort = d => { const x = new Date(d + 'T12:00'), far = Math.abs(Date.now() - x) > 300 * 864e5 || x.getFullYear() !== new Date().getFullYear() && Math.abs(Date.now() - x) > 60 * 864e5;
  return x.toLocaleDateString('en-CA', far ? { month: 'short', day: 'numeric', year: 'numeric' } : { month: 'short', day: 'numeric' }); };
const title = s => (s || '').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
const ago = iso => { if (!iso) return 'never'; const m = Math.round((Date.now() - new Date(iso.replace(' ', 'T') + 'Z')) / 60000); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
const toast = t => { const e = $('#toast'); e.textContent = t; e.classList.add('show'); setTimeout(() => e.classList.remove('show'), 2400); };
const thisMonth = new Date().toISOString().slice(0, 7);
const month = () => $('#month').value || thisMonth;

let page = 'today', VERSION = null, CATS = [], ACCTS = [], charts = {};

// ───────── navigation ─────────
function go(p) {
  page = p;
  $$('#nav a, #tabbar a').forEach(a => a.classList.toggle('on', a.dataset.p === p));
  $$('main .page').forEach(s => s.classList.toggle('hidden', s.id !== 'p-' + p));
  document.body.className = 'p-' + p;
  location.hash = p;
  window.scrollTo(0, 0);
  return load();
}
$$('#nav a, #tabbar a').forEach(a => a.onclick = () => go(a.dataset.p));
addEventListener('scroll', () => $('.top').classList.toggle('scrolled', scrollY > 4), { passive: true });
document.addEventListener('click', e => { const g = e.target.closest('[data-go]'); if (g) go(g.dataset.go); });
$('#month').onchange = () => load();
const load = () => ({ today: loadToday, spending: loadSpending, plan: loadPlan, activity: loadActivity, subs: loadSubs, system: loadSystem })[page]();

// ───────── charts ─────────
function opts(extra = {}) {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = css('--text-secondary');
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 250 }, interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false }, tooltip: {
      backgroundColor: css('--surface-1'), titleColor: css('--text-primary'), bodyColor: css('--text-secondary'), borderColor: css('--border'),
      borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true, callbacks: { label: c => ` ${c.dataset.label}: ${fmt(c.parsed.y)}` } } },
    scales: {
      x: { grid: { display: false }, border: { color: css('--border') }, ticks: { color: css('--text-muted'), maxRotation: 0, autoSkipPadding: 14 } },
      y: { beginAtZero: true, grid: { color: css('--grid') }, border: { display: false }, ticks: { color: css('--text-muted'), callback: v => fmt(v), maxTicksLimit: 6 } },
    }, ...extra };
}
function chart(id, cfg) { charts[id]?.destroy(); const el = $('#' + id); if (el) charts[id] = new Chart(el, cfg); }
const bar = (label, data, color, extra = {}) => ({ label, data, backgroundColor: color, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'bottom', maxBarThickness: 18, categoryPercentage: .7, barPercentage: .9, ...extra });

// ───────── NOW: paycheque to paycheque ─────────
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const NUM = {};  // last shown value per element, so numbers glide from old to new instead of jumping
function countTo(sel, v, f = fmt, html = false) {
  const el = $(sel); if (!el) return;
  const put = x => html ? el.innerHTML = x : el.textContent = x;
  const from = NUM[sel], to = v; NUM[sel] = v;
  if (v == null || from == null || reduceMotion || from === to) { put(v == null ? '—' : f(v)); return; }
  const t0 = performance.now(), dur = 1100;
  const step = t => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    put(f(from + (to - from) * e)); if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
const bigMoney = v => `<span class="c">$</span>${Math.round(Math.abs(v)).toLocaleString('en-CA')}`;
const dayName = d => new Date(d + 'T12:00').toLocaleDateString('en-CA', { weekday: 'long' });
const dayShort = d => new Date(d + 'T12:00').toLocaleDateString('en-CA', { weekday: 'short', month: 'short', day: 'numeric' });
const md = d => { const x = new Date(d + 'T12:00'); return `${x.getMonth() + 1}/${x.getDate()}`; };
const billName = c => c === 'Family Support' ? 'Dad' : c;
const isoToday = () => { const n = new Date(); return new Date(n - n.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); };

function countdown(next) {
  const ms = new Date(next + 'T00:00') - Date.now();
  if (ms <= 0) return 'today';
  const m = Math.floor(ms / 6e4), h = Math.floor(m / 60), d = Math.floor(h / 24);
  return d >= 1 ? `in ${d} d ${h % 24} h` : h >= 1 ? `in ${h} h ${m % 60} min` : `in ${m} min`;
}
function tickClock() {
  const now = new Date();
  const c = $('#clock'); if (c) c.textContent = now.toLocaleDateString('en-CA', { weekday: 'long', month: 'long', day: 'numeric' }) + ' · ' +
    now.toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' });
  if (TODAY?.period?.next_is_pay) $('#pd-count').textContent = countdown(TODAY.period.next);
}

let TODAY = null, SEEN_TX = null;
async function loadToday() {
  const T = await api('/api/today?month=' + thisMonth);
  TODAY = T; VERSION = T.version; fillMonths(T.months); setSync(T.last_sync); badges(T.to_review, T.health);
  const P = T.period, F = T.forecast;
  $('#headline').className = 'headline ' + T.tone; $('#hl-text').textContent = T.headline;

  // ── the one number: free until the next paycheque
  const over = P.left < 0, until = P.next_is_pay ? 'payday' : P.mode === 'month' ? 'month end' : dshort(P.next);
  $('#hero').classList.toggle('over', over);
  $('#h-eyebrow').textContent = over ? `Past what this pay leaves` : P.mode === 'month' ? 'Left this month' : `Free until ${until}`;
  $('#h-left').classList.toggle('neg', over);
  countTo('#h-left', P.left, v => (v < 0 ? '−' : '') + bigMoney(v), true);
  const usual = P.usual_day ? ` You usually spend about ${fmt(P.usual_day)} a day.` : '';
  $('#h-sub').innerHTML = over
    ? `You've spent <b>${fmt(-P.left)}</b> more than this pay leaves after bills. It comes out of the next one.`
    : `About <b>${fmt(P.per_day)} a day</b> for the ${P.days_left} day${P.days_left === 1 ? '' : 's'} until ${P.next_is_pay ? dayName(P.next) + "'s pay" : until}.${usual}`;

  // ── payday card
  const pd = $('#h-payday');
  pd.classList.toggle('none', !P.next_is_pay);
  if (P.next_is_pay) {
    $('#pd-k').textContent = P.typical_pay ? `Next pay · ~${fmt(P.typical_pay)}` : 'Next payday';
    $('#pd-date').textContent = dayShort(P.next);
    $('#pd-count').textContent = countdown(P.next);
  } else {
    $('#pd-k').textContent = 'No paycheque coming';
    $('#pd-date').textContent = P.pay_stops ? `Pay ended ${dshort(P.pay_stops)}` : 'No regular pay found';
    $('#pd-count').textContent = P.mode === 'month' ? `${P.days_left} days left in the month` : '';
  }

  // ── where this pay goes: bills share · spent · free
  const inn = P.money_in, bills = P.bills, spent = Math.max(0, P.spent);
  let segs;
  if (!over) segs = [['bills', bills, 'Bills share'], ['spent', spent, 'Spent'], ['left', P.left, 'Free']];
  else { const b = Math.min(bills, inn); segs = [['bills', b, 'Bills share'], ['spent', Math.max(0, inn - b), 'Spent'], ['over', -P.left, 'Over']]; }
  segs = segs.filter(x => x[1] > 0.5);
  const tot = Math.max(1, segs.reduce((a, x) => a + x[1], 0));
  $('#split-title').innerHTML = P.mode === 'pay'
    ? (P.pay ? `This pay: <b>${fmt(inn)}</b> in on ${dshort(P.start)}` : `Waiting for this pay to land`)
    : `So far this month: <b>${fmt(inn)}</b> in`;
  $('#split-right').textContent = `Day ${Math.min(P.day, P.length)} of ${P.length}`;
  $('#h-split').innerHTML = segs.map(x => `<i class="${x[0]}" style="flex-grow:${x[1] / tot}" title="${x[2]}: ${fmt(x[1])}"></i>`).join('');
  $('#h-legend').innerHTML = segs.map(x => `<span><i class="${x[0]}"></i>${x[2]} <b>${fmt(x[1])}</b></span>`).join('');

  // ── day by day until payday
  const today = isoToday(), days = [...P.days];
  if (P.next_is_pay) days.push({ date: P.next, pay: true });
  const evenPace = Math.max(0, P.free) / P.length, ahead = Math.max(0, P.per_day);
  const top = Math.max(1, ...days.map(d => d.spent || 0), evenPace, ahead) * 1.12;
  const el = $('#h-days'); el.style.setProperty('--n', days.length); el.style.gap = days.length > 20 ? '3px' : '';
  el.innerHTML = days.map(d => {
    const lbl = days.length > 20 ? (new Date(d.date + 'T12:00').getDate()) : new Date(d.date + 'T12:00').toLocaleDateString('en-CA', { weekday: 'narrow' });
    if (d.pay) return `<div class="day pay" title="Payday · ${dayShort(d.date)}"><div class="col"></div><span class="lbl">${lbl}</span></div>`;
    const fut = d.date > today, now = d.date === today;
    const bar = fut ? '' : `<div class="bar ${d.spent <= evenPace ? 'ok' : ''}" style="height:${(d.spent / top * 100).toFixed(1)}%"></div>`;
    const ghost = fut && ahead > 0 ? `<div class="ghost" style="height:${(ahead / top * 100).toFixed(1)}%"></div>` : '';
    return `<div class="day ${fut ? 'future' : ''} ${now ? 'today' : ''}" title="${dayShort(d.date)}: ${fut ? 'up to ' + fmt(ahead) : fmt2(d.spent) + ' spent'}">
      ${!fut && d.spent > 0 ? `<span class="amt">${fmt(d.spent)}</span>` : ''}<div class="col">${bar}${ghost}</div><span class="lbl">${lbl}</span></div>`;
  }).join('');
  $('#days-l').innerHTML = `<b>${fmt(spent)}</b> spent since payday`;
  $('#days-r').innerHTML = P.left > 0 ? `<b>${fmt(ahead)}</b>/day keeps you even` : `Nothing left to spread`;
  $('#t-math').innerHTML = periodMath(P);

  // ── the long view, one line
  const by = new Date(F.horizon + 'T12:00').toLocaleDateString('en-CA', { month: 'short', year: 'numeric' });
  let paysLeft = 0;
  if (P.pay_stops && P.every_days) { let d = new Date(P.next + 'T12:00'); const stop = new Date(P.pay_stops + 'T12:00');
    while (d <= stop) { paysLeft++; d.setDate(d.getDate() + P.every_days); } }
  const stopTxt = P.pay_stops && P.pay_stops >= today ? `Pay ends <b>${dshort(P.pay_stops)}</b>: ${paysLeft} payday${paysLeft === 1 ? '' : 's'} left. ` : '';
  const endTxt = F.relative
    ? (F.end < 0 ? `If nothing changes, you'll be <b class="neg">${fmt(-F.end)} short</b> by ${by}.` : `If nothing changes, you'll be <b>${fmt(F.end)} ahead</b> by ${by}.`)
    : `Lowest point ahead: <b>${fmt(F.low.balance)}</b> around ${dshort(F.low.date)}.`;
  $('#ahead').innerHTML = `<span>${stopTxt}${endTxt}</span><span class="go">Plan</span>`;

  renderBills('#t-bills', '#billbar', '#bills-sum', '#t-lever', T.bills, P, T.lever);
  renderHistory(T.history);

  // ── latest: newest first; anything new since the last refresh glows
  const ids = new Set(T.recent.map(t => t.id));
  $('#t-stream').innerHTML = T.recent.filter(t => t.kind !== 'transfer').slice(0, 7).map(t => {
    const fresh = SEEN_TX && !SEEN_TX.has(t.id), inn = t.amount > 0, x = t.kind === 'transfer';
    const nm = t.merchant || '—';
    return `<li class="${fresh ? 'fresh' : ''}"><span class="s-ic ${inn ? 'in' : x ? 'xfer' : ''}">${esc((nm.match(/[A-Za-z0-9]/) || ['·'])[0].toUpperCase())}</span>
      <span class="s-main"><span class="s-name">${esc(nm)}</span><span class="s-meta">${esc(t.category)} · ${dshort(t.date)}</span></span>
      <span class="s-amt ${inn ? 'in' : ''}">${inn ? '+' : ''}${fmt2(t.amount)}</span></li>`; }).join('') || '<li class="muted">Nothing yet.</li>';
  SEEN_TX = ids;

  // ── what Ledger noticed
  const items = [...T.messages.map(m => ({ at: m.created, ic: 'msg', html: `<b>${esc(m.title.replace(/^\p{Extended_Pictographic}\s*/u, ''))}</b><div class="sec">${esc(m.body.length > 180 ? m.body.slice(0, 180) + '…' : m.body)}</div>` })),
    ...T.activity.map(a => ({ at: a.at, ic: a.kind, html: esc(a.text) }))]
    .sort((a, b) => b.at.localeCompare(a.at)).slice(0, 5);
  $('#t-feed').innerHTML = items.length ? items.map(i => `<li><span class="ic ic-${esc(i.ic)}"></span><span>${i.html}<div class="t">${ago(i.at)}</div></span></li>`).join('') : '<li class="muted">Quiet so far.</li>';
  tickClock();
}
setInterval(() => { if (!document.hidden && page === 'today') tickClock(); }, 20000);
$$('#askchips button').forEach(b => b.onclick = () => { $('#askq').value = b.textContent; $('#askf').requestSubmit(); });

function periodMath(P) {
  const row = (l, v, cls = '') => `<div class="${cls}"><span>${l}</span><span>${v}</span></div>`;
  const share = m => m * P.length / 30.44;
  return (P.mode === 'pay' ? row(`Paycheque${P.start ? ', ' + dshort(P.start) : ''}`, fmt(P.pay)) : row('Pay this month', fmt(P.pay))) +
    (P.other ? row('Other money in since then (friends paying you back aside)', fmt(P.other)) : '') +
    row('Came in', fmt(P.money_in), 'total') +
    Object.entries(P.bill_cats).sort((a, b) => b[1] - a[1]).map(([c, m]) => row(`− ${esc(billName(c))} <span class="muted">${fmt(m)}/mo × ${P.length} of 30.4 days</span>`, fmt(share(m)), 'ind')).join('') +
    row(`Free this ${P.mode === 'pay' ? 'pay' : 'month'}`, fmt(P.free), 'total') +
    row('− Everyday spending since then (food, gas, shopping…)', fmt(P.spent), 'ind') +
    row(`Left until ${P.next_is_pay ? 'payday' : dshort(P.next)}`, fmt(P.left), 'total') +
    row(`÷ ${P.days_left} day${P.days_left === 1 ? '' : 's'}`, `${fmt(P.per_day)} a day`, 'ind') +
    (P.lumps ? row(`<span class="muted">Not counted as pay: ${fmt(P.lumps)} one-off (OSAP, tax return…)</span>`, '') : '');
}

function renderBills(listSel, barSel, sumSel, leverSel, B, P, L) {
  const on = B.filter(b => b.status !== 'off');
  const total = on.reduce((a, b) => a + b.amount, 0), gone = on.reduce((a, b) => a + Math.min(b.paid, b.amount), 0);
  $(sumSel).innerHTML = `<b>${fmt(gone)}</b> of ${fmt(total)} gone out`;
  $(barSel).innerHTML = `<i style="width:${total ? (gone / total * 100).toFixed(1) : 0}%"></i>`;
  const perPay = m => P && P.mode === 'pay' && P.every_days ? `<small>${fmt(m * P.every_days / 30.44)} a pay</small>` : '';
  $(listSel).innerHTML = B.map(b => {
    const soon = b.status === 'due' && b.due && P && b.due < P.next;
    const cls = b.status === 'paid' ? 'paid' : b.status === 'part' ? 'part' : b.status === 'late' ? 'late' : b.status === 'off' ? 'off' : soon ? 'soon' : '';
    const wh = { paid: `Paid ${b.paid_on ? dshort(b.paid_on) : ''}`, part: `${fmt(b.paid)} so far${b.due ? ' · usually by ' + dshort(b.due) : ''}`,
      late: `Usually out by ${b.due ? dshort(b.due) : ''}, not seen yet`, off: `Off: ${esc(b.off || '')}`,
      untracked: 'Counted every month, no emails for this one',
      due: soon ? `Due ${dshort(b.due)} · before payday` : `Usually ${b.due ? dshort(b.due) : 'monthly'}` }[b.status];
    return `<li class="${cls}"><span class="st"></span><span><div class="nm">${esc(b.name)}</div><div class="wh">${wh}</div></span>
      <span class="am">${fmt(b.amount)}${b.status === 'paid' ? '' : perPay(b.amount)}</span></li>`; }).join('') || '<li class="muted">No bills found yet.</li>';
  if (!leverSel) return;
  let t = '';
  if (L) {
    t = L.gap_per_pay > 0
      ? `<b>Dad is ${fmt(L.dad)} a month.</b> A typical pay leaves ${fmt(L.free_typical)} after bills, but you usually spend ${fmt(L.usual_per_pay)} between paydays. You'd break even with Dad at about <b>${fmt(L.break_even)}</b>. Every $100 less is ${fmt(L.per_100)} more each pay.`
      : `<b>Dad is ${fmt(L.dad)} a month.</b> A typical pay leaves ${fmt(L.free_typical)} after bills, about ${fmt(-L.gap_per_pay)} more than you usually spend. Every $100 less to Dad adds ${fmt(L.per_100)} a pay.`;
  }
  $(leverSel).innerHTML = t;
}

function renderHistory(H) {
  const el = $('#t-hist');
  if (!H.length) { el.innerHTML = '<div class="empty">Appears after a couple of paydays.</div>'; $('#hist-sum').textContent = ''; $('#t-hist-note').textContent = ''; return; }
  const top = Math.max(1, ...H.map(h => Math.max(h.free, h.spent))) * 1.08;
  el.style.setProperty('--n', H.length);
  el.innerHTML = H.map(h => `<div class="hp" title="Pay ${dshort(h.start)}: ${fmt(h.pay + h.other)} in, ${fmt(h.bills)} bills share, ${fmt(h.spent)} everyday → ${h.kept >= 0 ? fmt(h.kept) + ' to spare' : fmt(-h.kept) + ' short'}">
    <div class="tr"><div class="cap" data-v="${fmt(h.free)}" style="bottom:${(Math.max(0, h.free) / top * 100).toFixed(1)}%"></div>
    <div class="b ${h.spent > h.free ? 'over' : ''}" style="height:${(h.spent / top * 100).toFixed(1)}%"></div></div><span class="d">${md(h.start)}</span></div>`).join('') +
    '';
  const ok = H.filter(h => h.spent <= h.free).length, avg = k => H.reduce((a, h) => a + h[k], 0) / H.length;
  $('#hist-sum').innerHTML = `Covered <b>${ok} of ${H.length}</b>`;
  $('#t-hist-note').innerHTML = `<span class="hist-legend"><span><i style="background:var(--free)"></i>Everyday spending</span><span><i style="background:var(--spent)"></i>More than the pay left</span><span><i class="cap"></i>What the pay left after bills</span></span>` +
    `<span style="display:block;margin-top:8px">On average a pay left <b>${fmt(avg('free'))}</b> after bills and you spent <b>${fmt(avg('spent'))}</b> before the next one.</span>`;
}

function mathHTML(S) { return S && S.mode ? periodMath(S) : ''; }

function renderGoals(sel, G, compact) {
  if (!$(sel)) return;
  if (!G.goals.length) { $(sel).innerHTML = '<div class="empty">No goals yet.</div>'; return; }
  $(sel).innerHTML = G.goals.map(g => `
    <div class="goal"><div class="top"><span>${g.done ? '✅ ' : ''}${esc(g.name)}</span><span class="sec">${fmt(g.target)}</span></div>
      <div class="meta">${[g.due ? 'by ' + dshort(g.due) : '', g.per_month ? `save ${fmt(g.per_month)}/mo to make it` : ''].filter(Boolean).join(' · ')}</div></div>`).join('');
}

// ───────── SPENDING ─────────
async function loadSpending() {
  const D = await api('/api/spending?month=' + month());
  VERSION = D.version;
  const k = D.kpis;
  $('#s-kpis').innerHTML = [
    k.paid_back > 0
      ? ['Spent', fmt(k.spent_gross), `${fmt(k.paid_back)} paid back by others → ${fmt(k.expenses)} net`]
      : ['Spent', fmt(k.expenses), k.projected_expenses != null ? `on pace for ~${fmt(k.projected_expenses)}` : `3-mo avg ${fmt(k.avg_expenses_3m)}`],
    ['Income', fmt(k.income), `${k.txns} transactions`],
    ['Kept', fmt(k.net), k.savings_rate == null ? 'no income yet' : k.savings_rate > 1 ? 'others paid back more than you spent so far' : `${Math.round(k.savings_rate * 100)}% savings rate`],
    (() => { const ref = k.projected_expenses ?? k.expenses, d = ref - k.avg_expenses_3m;
      return [k.projected_expenses != null ? 'Heading for' : 'Compared with usual', fmt(ref),
        `${Math.abs(d) < 25 ? 'about your usual' : fmt(Math.abs(d)) + (d > 0 ? ' more' : ' less') + ' than usual'} (${fmt(k.avg_expenses_3m)})`]; })()]
    .map(([l, v, f]) => `<div class="card kpi"><div class="label">${l}</div><div class="val">${v}</div><div class="foot">${f}</div></div>`).join('');
  // budgets
  const B = D.budgets.filter(b => b.spent > 0 || !b.auto);
  $('#s-budgets').innerHTML = B.length ? B.slice(0, 14).map(b => `<div class="bar-row" title="${b.auto ? 'Learned budget' : 'Your budget'}">
    <div class="name">${b.status === 'over' ? '🚨 ' : b.status === 'hot' ? '🔥 ' : ''}${esc(b.category)}</div>
    <div class="track"><div class="f ${b.status}" style="width:${Math.min(100, b.pct * 100).toFixed(1)}%"></div></div>
    <div class="r">${fmt(b.spent)} / ${fmt(b.budget)}</div></div>`).join('') : '<div class="empty">Budgets appear after a month of data.</div>';
  // categories
  const cats = D.categories.categories.filter(c => c.spent > .5), mx = Math.max(1, ...cats.map(c => Math.max(c.spent, c.avg3)));
  $('#s-cats').innerHTML = cats.length ? cats.slice(0, 14).map(c => `<div class="bar-row click" data-cat="${esc(c.category)}" title="${c.n} transactions · 3-mo avg ${fmt(c.avg3)}">
    <div class="name">${esc(c.category)}</div><div class="track"><div class="f" style="width:${(c.spent / mx * 100).toFixed(1)}%"></div>${c.avg3 ? `<div class="avg" style="left:${(c.avg3 / mx * 100).toFixed(1)}%"></div>` : ''}</div>
    <div class="r">${fmt(c.spent)}</div></div>`).join('') : '<div class="empty">No spending yet this month.</div>';
  $$('#s-cats [data-cat]').forEach(el => el.onclick = () => { go('activity').then(() => { $('#f-cat').value = el.dataset.cat; loadTx(); }); });
  heatmap(D.heat, D.heat_from);
  // monthly
  const ms = D.monthly.map(x => mname(x.month));
  chart('c-monthly', { type: 'bar', data: { labels: ms, datasets: [bar('In', D.monthly.map(x => x.income), css('--moss')), bar('Out', D.monthly.map(x => x.expenses), css('--ember'))] },
    options: opts({ onClick: (e, els) => { if (els[0]) { $('#month').value = D.monthly[els[0].index].month; load(); } } }) });
  // pace
  const P = D.daily; $('#pace-prev').textContent = mname(P.prev_month);
  chart('c-pace', { type: 'line', data: { labels: P.days, datasets: [
    { label: mname(month()), data: P.current, borderColor: css('--s1'), backgroundColor: css('--s1'), borderWidth: 2, pointRadius: 0, pointHoverRadius: 5 },
    { label: mname(P.prev_month), data: P.previous, borderColor: css('--text-muted'), backgroundColor: css('--text-muted'), borderDash: [4, 4], borderWidth: 2, pointRadius: 0, pointHoverRadius: 5 }] },
    options: (() => { const o = opts(); o.plugins.tooltip.callbacks.title = c => 'Day ' + c[0].label; return o; })() });
  // trend
  const T = D.trend, order = Object.entries(T.totals).sort((a, b) => b[1] - a[1]).map(x => x[0]);
  const top = order.filter(g => g !== 'Other').slice(0, 6), rest = order.filter(g => !top.includes(g));
  const series = top.map(g => [g, T.series[g]]); if (rest.length) series.push(['Other', T.months.map((_, i) => rest.reduce((a, g) => a + T.series[g][i], 0))]);
  const pal = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7'].map(css);
  $('#trend-legend').innerHTML = series.map((s, i) => `<span><i style="background:${pal[i]}"></i>${esc(s[0])}</span>`).join('');
  chart('c-trend', { type: 'bar', data: { labels: T.months.map(m => mname(m)), datasets: series.map((s, i) => ({ label: s[0], data: s[1], backgroundColor: pal[i],
    borderColor: css('--surface-1'), borderWidth: { top: 2 }, borderSkipped: 'bottom', maxBarThickness: 26, borderRadius: i === series.length - 1 ? { topLeft: 4, topRight: 4 } : 0 })) },
    options: (() => { const o = opts(); o.scales.x.stacked = o.scales.y.stacked = true; o.plugins.tooltip.itemSort = (a, b) => b.datasetIndex - a.datasetIndex;
      o.plugins.tooltip.callbacks.footer = c => 'Total ' + fmt(c.reduce((a, x) => a + x.parsed.y, 0)); return o; })() });
  $('#s-merchants').innerHTML = '<tr><th>Merchant</th><th>Category</th><th class="num">#</th><th class="num">Spent</th></tr>' + (D.merchants.map(r =>
    `<tr><td>${esc(r.merchant)}</td><td><span class="tag">${esc(r.category)}</span></td><td class="num muted">${r.n}</td><td class="num">${fmt2(r.spent)}</td></tr>`).join('') || '<tr><td colspan=4 class="empty">—</td></tr>');
  $('#s-people').innerHTML = '<tr><th>Person</th><th class="num">Received</th><th class="num">Sent</th><th class="num">Last</th></tr>' + (D.people.map(r =>
    `<tr><td>${esc(title(r.counterparty))}</td><td class="num">${r.received ? fmt(r.received) : '<span class="muted">—</span>'}</td><td class="num">${r.sent ? fmt(r.sent) : '<span class="muted">—</span>'}</td><td class="num muted">${dshort(r.last)}</td></tr>`).join('') || '<tr><td colspan=4 class="empty">No e-transfers yet</td></tr>');
  $('#s-tripcard').classList.toggle('hidden', !D.trips.length);
  $('#s-trips').innerHTML = D.trips.map(t => `<div class="bar-row"><div class="name">✈ ${esc(t.name)}</div>
    <div class="track"><div class="f ${t.budget && t.spent > t.budget ? 'over' : ''}" style="width:${t.budget ? Math.min(100, t.spent / t.budget * 100) : 0}%"></div></div>
    <div class="r">${fmt(t.spent)}${t.budget ? ' / ' + fmt(t.budget) : ''}</div></div><div class="sub">${dshort(t.start)} – ${dshort(t.end)}${t.budget ? '' : ' · set a budget in config.toml to track against it'}</div>`).join('');
}

function heatmap(heat, from) {
  const start = new Date(from + 'T12:00'); start.setDate(start.getDate() - start.getDay());
  const end = new Date(); const vals = Object.values(heat).filter(v => v > 0).sort((a, b) => a - b);
  const q = p => vals.length ? vals[Math.floor((vals.length - 1) * p)] : 0, cuts = [q(.2), q(.4), q(.6), q(.8)];
  let html = '', d = new Date(start);
  while (d <= end) {
    html += '<div class="wk">';
    for (let i = 0; i < 7; i++) {
      const iso = d.toISOString().slice(0, 10), v = heat[iso] || 0;
      const lvl = d > end || iso < from ? 'blank' : v <= 0 ? '' : 'l' + (1 + cuts.filter(c => v > c).length);
      html += `<div class="d ${lvl}" title="${dshort(iso)}: ${fmt2(v)}"></div>`; d.setDate(d.getDate() + 1);
    }
    html += '</div>';
  }
  $('#s-heat').innerHTML = html;
  $('#s-heat').insertAdjacentHTML('afterend', $('#heatleg') ? '' : `<div class="heat-legend" id="heatleg">less <span class="d l1" style="display:inline-block;width:12px;height:12px;border-radius:3px;background:var(--seq1)"></span><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:var(--seq3)"></span><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:var(--seq5)"></span> more</div>`);
  $('#s-heat').scrollLeft = 99999;
}

// ───────── PLAN ─────────
function wifQuery() {
  const v = id => (document.getElementById(id).value || '').replace(/[$,\s]/g, '');
  const p = new URLSearchParams();
  if (v('w-job')) { p.set('job_monthly', v('w-job')); p.set('job_start', $('#w-start').value); }
  if (v('w-winter')) p.set('winter_monthly', v('w-winter'));
  if (v('w-osap')) p.set('osap', v('w-osap'));
  return p.toString();
}
$('#wif').onsubmit = e => { e.preventDefault(); try { localStorage.setItem('ledger-wif', JSON.stringify(['w-job', 'w-start', 'w-winter', 'w-osap'].map(i => document.getElementById(i).value))); } catch (_) { } loadPlan(); };
$('#w-clear').onclick = () => { ['w-job', 'w-winter', 'w-osap'].forEach(i => document.getElementById(i).value = ''); try { localStorage.removeItem('ledger-wif'); } catch (_) { } loadPlan(); };
try { const w = JSON.parse(localStorage.getItem('ledger-wif') || 'null'); if (w) ['w-job', 'w-start', 'w-winter', 'w-osap'].forEach((i, k) => { if (w[k]) document.getElementById(i).value = w[k]; }); } catch (_) { }

async function loadPlan() {
  const D = await api('/api/planpage?' + wifQuery());
  VERSION = D.version;
  const F = D.forecast;
  $('#f-sub').textContent = F.relative ? `Change in cash from today through ${dshort(F.horizon)} (set balances on System for real numbers)` :
    `From ${fmt(F.start)} today to ${dshort(F.horizon)}. The dashed line is your ${fmt(F.buffer)} cushion.`;
  const labels = F.series.map(p => p[0]);
  const markIdx = new Set(F.markers.map(m => labels.findIndex(l => l >= m.date)).filter(i => i >= 0));
  chart('c-forecast', { type: 'line', data: { labels, datasets: [
    { label: 'Projected cash', data: F.series.map(p => p[1]), borderColor: css('--s1'), backgroundColor: css('--s7'), borderWidth: 2, tension: .15,
      pointRadius: labels.map((_, i) => markIdx.has(i) ? 5 : 0), pointHoverRadius: 5, pointBackgroundColor: css('--s7') },
    ...(F.relative ? [] : [{ label: 'Cushion', data: labels.map(() => F.buffer), borderColor: css('--text-muted'), borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 }]),
    ...(D.base ? [{ label: 'Without what-if', data: D.base.series.map(p => p[1]), borderColor: css('--text-muted'), borderWidth: 1.5, pointRadius: 0, tension: .15 }] : [])] },
    options: (() => { const o = opts({ scales: { x: { type: 'category', grid: { display: false }, ticks: { color: css('--text-muted'), maxTicksLimit: 10, callback: (v, i) => mname(labels[i].slice(0, 7)) } },
      y: { beginAtZero: false, grid: { color: css('--grid') }, border: { display: false }, ticks: { color: css('--text-muted'), callback: v => fmt(v) } } } });
      o.plugins.tooltip.callbacks.title = c => dshort(c[0].label);
      o.plugins.tooltip.filter = c => c.dataset.label !== 'Cushion';
      o.plugins.tooltip.callbacks.afterBody = c => F.markers.filter(m => m.date === c[0].label).map(m => '◆ ' + m.label);
      return o; })() });
  $('#c-forecast').closest('.card').querySelector('.legend').innerHTML = `<span><i style="background:var(--s1)"></i>${D.base ? 'With what-if' : 'Projected cash'}</span>` +
    (D.base ? '<span><i style="background:var(--text-muted)"></i>Without</span>' : '') + '<span><i class="dash"></i>Cushion</span>';
  $('#w-result').textContent = D.base ? `With this what-if: lowest ${fmt(F.low.balance)} (${dshort(F.low.date)}) vs ${fmt(D.base.low.balance)} without — ${F.low.balance >= F.buffer ? 'you stay above your cushion the whole way.' : F.low.balance >= 0 ? 'you dip into your cushion but never run out.' : `still ${fmt(-F.low.balance)} short at the low point.`}` : '';
  $('#f-callouts').innerHTML = [
    ['Lowest point', fmt(F.low.balance), dshort(F.low.date)],
    ['At ' + dshort(F.horizon), fmt(F.end), F.relative ? 'net change' : 'projected cash'],
    ...(F.relative ? [['Runway', 'Needs balances', 'add them on System to see when cash runs out']]
      : F.below_zero ? [['Runs out', dshort(F.below_zero), 'at current habits']] : F.below_buffer ? [['Under cushion', dshort(F.below_buffer), 'tighten before then']] : [['Runway', 'Covered', 'never below your cushion']]),
    ['Everyday spending', fmt(F.assumptions.everyday_per_day * 30.4) + '/mo', 'what the forecast assumes']]
    .map(([a, b, c]) => `<div class="callout">${a}<b>${b}</b>${c}</div>`).join('');
  // math
  $('#f-math').innerHTML = `<div class="math">${periodMath(D.period)}</div>`;
  $('#f-events').innerHTML = [...D.events].sort((a, b) => (a.date || a.start).localeCompare(b.date || b.start)).map(e => { const d = e.date || e.start; const amt = e.amount || e.budget || e.monthly;
    return `<li><span class="when">${dshort(d)}</span><span><i class="dotk event"></i>${esc(e.name)} <span class="tag">${esc({ pause: 'paused', stop: 'ends', one_time: 'one-off', trip: 'trip', income: 'new income' }[e.kind] || e.kind)}</span>${e.end ? ` <span class="muted">→ ${dshort(e.end)}</span>` : ''}</span>
    <span class="amt">${amt ? fmt(amt) : (e.kind === 'one_time' || e.kind === 'income' ? '<span class="muted">set amount</span>' : '')}</span></li>`; }).join('');
  const sv = D.savings, ms = sv.months.map(x => mname(x.month));
  $('#f-sav-sub').textContent = sv.of ? `Goal ${D.goal_pct}% · hit ${sv.hit} of last ${sv.of} months · kept ${fmt(sv.saved_6m)}, invested ${fmt(sv.invested_6m)}` : `Goal ${D.goal_pct}%`;
  chart('c-rate', { type: 'line', data: { labels: ms, datasets: [
    { label: 'Kept', data: sv.months.map(x => x.savings_rate == null ? null : Math.round(x.savings_rate * 100)), borderColor: css('--s3'), backgroundColor: css('--s3'), borderWidth: 2, pointRadius: 4, spanGaps: true },
    { label: 'Goal', data: ms.map(() => D.goal_pct), borderColor: css('--text-muted'), borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0 }] },
    options: (() => { const o = opts(); o.scales.y.ticks.callback = v => v + '%'; o.plugins.tooltip.callbacks.label = c => ` ${c.dataset.label} ${c.parsed.y}%`; return o; })() });
  $('#f-months').innerHTML = '<tr><th>Month</th><th class="num">Money in</th><th class="num">Bills</th><th class="num">Everyday</th><th class="num">One-offs</th><th class="num">Cash after</th></tr>' +
    F.months.map(m => `<tr><td>${mname(m.month, true)}</td><td class="num pos">${fmt(m.income)}</td><td class="num">${fmt(m.fixed)}</td><td class="num">${fmt(m.flexible)}</td><td class="num">${m.events ? fmt(m.events) : '<span class="muted">—</span>'}</td><td class="num" style="font-weight:600">${fmt(m.end)}</td></tr>`).join('');
  const A = F.assumptions;
  $('#f-assume').innerHTML = `Assumes: ${A.pay ? `pay ${fmt(A.pay.amount)} every ${A.pay.every_days} days${A.pay.until ? ' until ' + dshort(A.pay.until) : ''}` : 'no regular pay detected'} ·
    fixed ${Object.entries(A.fixed).map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(', ') || 'none yet'} · everyday ${fmt(A.everyday_per_day)}/day · other income ${fmt(A.other_income_monthly)}/mo.
    ${A.missing_balances.length ? `<br>Missing balances: ${A.missing_balances.map(esc).join(', ')}.` : ''} Edit life events in config.toml.`;
}


// ───────── ACTIVITY ─────────
function catSelect(cur, cls = 'catsel') {
  const groups = {}; CATS.forEach(c => (groups[c.grp] ||= []).push(c.name));
  return `<select class="${cls}">` + Object.entries(groups).map(([g, cs]) => `<optgroup label="${esc(g)}">` + cs.map(c => `<option ${c === cur ? 'selected' : ''}>${esc(c)}</option>`).join('') + '</optgroup>').join('') + '</select>';
}
async function loadActivity() { await loadReview(); await loadTx(); }
async function loadReview() {
  const rows = await api('/api/transactions?review=1&limit=1000');
  const groups = new Map();
  rows.forEach(t => { const key = (t.counterparty || t.merchant || t.description) + (t.amount > 0 ? '|in' : '|out');
    if (!groups.has(key)) groups.set(key, { first: t, ids: [], total: 0, n: 0 }); const g = groups.get(key); g.ids.push(t.id); g.total += t.amount; g.n++; });
  const G = [...groups.values()].sort((a, b) => b.n - a.n || Math.abs(b.total) - Math.abs(a.total));
  $('#reviewcard').classList.toggle('hidden', !G.length);
  $('#reviewtable').innerHTML = G.map((g, i) => { const t = g.first, name = t.counterparty ? title(t.counterparty) : (t.merchant || t.description);
    const hint = t.confidence ? 'read by AI — check the amount' : t.counterparty ? (t.amount > 0 ? 'money from this person' : 'money sent to this person') : esc(t.description);
    return `<tr data-g="${i}"><td><div>${esc(name)}</div><div class="desc">${hint}${t.memo ? ' · “' + esc(t.memo) + '”' : ''}</div></td>
      <td class="sec" style="white-space:nowrap">${g.n}× · ${dshort(t.date)}</td><td>${catSelect(t.category)}</td>
      <td class="num" style="font-weight:600">${g.total > 0 ? '+' : ''}${fmt2(g.total)}</td>
      <td class="num" style="white-space:nowrap"><button class="btn primary small save">${g.n > 1 ? 'Apply to all ' + g.n : 'Save'}</button> <button class="btn small ok">Looks right</button></td></tr>`; }).join('');
  $$('#reviewtable tr[data-g]').forEach(tr => { const g = G[+tr.dataset.g];
    const act = async () => { await api('/api/tx/' + g.first.id, { method: 'POST', body: { category: tr.querySelector('.catsel').value, apply_all: true } });
      await api('/api/review/ok', { method: 'POST', body: { ids: g.ids } }); };
    tr.querySelector('.save').onclick = async () => { await act(); toast('Saved — future ones will follow'); loadActivity(); };
    tr.querySelector('.ok').onclick = async () => { await act(); loadActivity(); }; });
  badges(rows.length);
}
$('#okall').onclick = async () => { await api('/api/review/ok', { method: 'POST', body: {} }); loadActivity(); };
async function loadTx() {
  const p = new URLSearchParams();
  if (!$('#f-allm').checked) p.set('month', month());
  [['q', '#f-q'], ['category', '#f-cat'], ['account_id', '#f-acct'], ['kind', '#f-kind']].forEach(([k, s]) => { if ($(s).value) p.set(k, $(s).value); });
  const rows = await api('/api/transactions?' + p);
  $('#tx-count').textContent = `${rows.length} shown · net ${fmt2(rows.reduce((a, t) => a + t.amount, 0))}`;
  $('#txtable').innerHTML = '<tr><th>Date</th><th>Merchant</th><th class="acct">Account</th><th>Category</th><th class="num">Amount</th><th>Note</th></tr>' + (rows.map(t => {
    const who = t.counterparty ? 'everyone from ' + title(t.counterparty) : 'all ' + (t.merchant || '');
    const badge = t.kind === 'transfer' ? `<span class="tag">${t.link_id ? 'transfer ⇄' : 'transfer'}</span>` : ['refund', 'reimbursement'].includes(t.kind) ? `<span class="tag">${t.kind}</span>` : '';
    const trip = (t.tags || '').includes('trip:') ? `<span class="tag">✈ ${esc(t.tags.split('trip:')[1].split(',')[0])}</span>` : '';
    return `<tr data-id="${t.id}"><td>${dshort(t.date)}</td>
      <td><div>${esc(t.merchant || t.description)} ${badge} ${trip} ${t.status === 'pending' ? '<span class="tag" title="From an alert; confirmed when a statement arrives">alert</span>' : ''}</div>
        <div class="desc" title="${esc(t.description)}">${esc(t.description)}${t.memo ? ' · “' + esc(t.memo) + '”' : ''}</div></td>
      <td class="acct sec" style="font-size:12px;white-space:nowrap">${esc(t.account_name || t.account_id)}</td>
      <td>${catSelect(t.category)}<div class="applybar hidden"><button class="btn primary small one">Just this</button><button class="btn small all">${esc(who.length > 30 ? who.slice(0, 29) + '…' : who)}</button></div></td>
      <td class="num ${t.kind === 'transfer' ? 'muted' : t.amount > 0 ? 'pos' : ''}" style="font-weight:600" title="${t.kind === 'transfer' ? 'Money moved between your own accounts: not income, not spending' : ''}">${t.kind === 'transfer' ? '⇄ ' + fmt2(Math.abs(t.amount)) : (t.amount > 0 ? '+' : '') + fmt2(t.amount)}</td>
      <td><input class="note" placeholder="note" value="${esc(t.note || '')}" style="width:110px;font-size:12px;padding:4px 6px"></td></tr>`; }).join('') || '<tr><td colspan=6 class="empty">Nothing matches.</td></tr>');
  $$('#txtable tr[data-id]').forEach(tr => { const id = tr.dataset.id, sel = tr.querySelector('.catsel'), bar = tr.querySelector('.applybar');
    sel.onchange = () => bar.classList.remove('hidden');
    const save = async all => { const r = await api('/api/tx/' + id, { method: 'POST', body: { category: sel.value, apply_all: all } });
      toast(all ? `Rule saved · ${r.recategorized} others updated` : 'Saved'); loadTx(); };
    bar.querySelector('.one').onclick = () => save(false); bar.querySelector('.all').onclick = () => save(true);
    const note = tr.querySelector('.note'); note.onchange = () => api('/api/tx/' + id, { method: 'POST', body: { note: note.value, category: sel.value } }).then(() => toast('Note saved')); });
}
let qt; $('#f-q').oninput = () => { clearTimeout(qt); qt = setTimeout(loadTx, 250); };
['#f-cat', '#f-acct', '#f-kind', '#f-allm'].forEach(s => $(s).onchange = loadTx);

// ───────── SUBSCRIPTIONS ─────────
async function loadSubs() {
  const [O, Bp] = await Promise.all([api('/api/overview?month=' + month()), api('/api/bills')]);
  renderBills('#b-bills', '#b-bar', '#b-sum', '#b-lever', Bp.bills, Bp.period, Bp.lever);
  const act = O.recurring.filter(r => r.active), yr = act.reduce((a, r) => a + r.yearly_cost, 0);
  const subsOnly = act.filter(r => ['Software & AI', 'Streaming & Music', 'Gym', 'Internet & Phone'].includes(r.category));
  $('#sub-kpis').innerHTML = [['Recurring per month', fmt(yr / 12), `${act.length} active`], ['Per year', fmt(yr), 'everything recurring'],
    ['Subscriptions only', fmt(subsOnly.reduce((a, r) => a + r.yearly_cost, 0) / 12) + '/mo', subsOnly.map(r => r.merchant).slice(0, 4).join(', ') || '—']]
    .map(([l, v, f]) => `<div class="card kpi"><div class="label">${l}</div><div class="val">${v}</div><div class="foot">${esc(f)}</div></div>`).join('');
  $('#rectable').innerHTML = '<tr><th>Merchant</th><th>Category</th><th>Every</th><th class="num">Amount</th><th class="num">Per year</th><th class="num">Last</th><th class="num">Next</th></tr>' +
    O.recurring.map(r => `<tr style="${r.active ? '' : 'opacity:.5'}"><td>${esc(r.merchant)}</td><td><span class="tag">${esc(r.category)}</span></td><td class="sec">${r.cadence}</td>
      <td class="num">${fmt2(r.amount)}</td><td class="num">${fmt(r.yearly_cost)}</td><td class="num muted">${dshort(r.last)}</td><td class="num">${r.next_expected ? dshort(r.next_expected) : '<span class="muted">stopped?</span>'}</td></tr>`).join('');
}

// ───────── SYSTEM ─────────
async function loadSystem() {
  const D = await api('/api/system');
  VERSION = D.version;
  const ph = D.phone, up = D.updates;
  $('#y-phone').innerHTML = !ph.installed
    ? `<p>Not set up yet. Install <b>Tailscale</b> on this laptop and on your phone, and sign in to both with the same account. Ledger notices within a couple of minutes and gives you the link here.</p>`
    : !ph.running ? `<p>Tailscale is installed but not signed in on this laptop. Open Tailscale from the system tray and sign in.</p>`
    : ph.enable_url ? `<p>One last step, once: Tailscale needs HTTPS switched on for your account.</p><p><a class="btn primary" href="${esc(ph.enable_url)}" target="_blank" rel="noopener">Switch on HTTPS</a></p><p class="muted">Ledger finishes the rest by itself a minute or two after.</p>`
    : ph.serving ? `<p class="pos" style="font-weight:650">On</p><p>Open this on your phone (with Tailscale on):</p><p><a class="link" style="font-size:16px;word-break:break-all" href="${esc(ph.url)}">${esc(ph.url)}</a></p><p class="muted">Then Share → Add to Home Screen to get it as an app.</p>`
    : `<p>Tailscale is signed in, but Ledger couldn't turn on its link yet.</p><p class="muted">${esc(ph.error || 'Trying again in a couple of minutes.')}</p>`;
  const v = up.current || {};
  $('#y-update').innerHTML = `<p>Running <b>${esc(v.sha || '?')}</b>${v.date ? ' from ' + dshort(v.date) : ''}${v.subject ? `<br><span class="muted">${esc(v.subject)}</span>` : ''}</p>
    <p class="sec">${up.auto ? `Checks every ${up.every} minutes (${up.method === 'git' ? 'with git' : 'by download'}).` : 'Automatic updates are off.'}
    ${up.checked ? `Last check ${ago(up.checked)}: ${esc(up.status)}.` : 'First check runs a few minutes after Ledger starts.'}</p>
    ${up.error ? `<p class="neg">${esc(up.error)}</p>` : ''}<button class="btn small" id="upnow">Check now</button>`;
  $('#upnow').onclick = async () => { $('#upnow').disabled = true; $('#upnow').textContent = 'Checking…';
    await api('/api/update', { method: 'POST' }); setTimeout(() => go('system'), 20000); };
  const src = (name, ok, detail, state) => `<div class="src"><div class="nm"><span class="st ${state || (ok ? 'ok' : 'bad')}">${state === 'off' ? 'off' : ok ? 'ok' : 'problem'}</span>${esc(name)}</div><div class="sec" style="margin-top:4px">${detail}</div></div>`;
  const bykey = Object.fromEntries(D.sources.map(s => [s.key, s]));
  $('#y-sources').innerHTML = [
    ...D.inboxes.map(i => { const s = bykey['inbox:' + i.address] || {}; const ok = i.has_password && !(s.fail_streak > 0);
      return src(i.address, ok, !i.has_password ? 'No app password in .env yet' : s.fail_streak ? esc(s.last_error) : `Last check ${ago(s.last_ok)} · ${s.items || 0} new so far`); }),
    src('Downloads watcher', true, D.watch.length ? `Watching ${D.watch.map(esc).join(', ')} for bank exports` : 'Off', D.watch.length ? null : 'off'),
    src('Local AI (reads unknown emails & merchants)', D.ai !== 'none', D.ai !== 'none' ? `Using ${D.ai}` : ({
      'no-model': D.ai_pull?.error ? `Model download failed: ${esc(D.ai_pull.error)}. Ledger will try again later.` : 'Ollama is running. Ledger is about to download the model.',
      'pulling': D.ai_pull?.total ? `Downloading the AI model: ${Math.round(D.ai_pull.done / D.ai_pull.total * 100)}% of ${(D.ai_pull.total / 1e9).toFixed(1)} GB` : 'Downloading the AI model…',
      'stopped': 'Ollama is installed — starting it…',
      'missing': 'Not installed yet. In PowerShell: irm https://ollama.com/install.ps1 | iex',
    }[D.ai_state] || 'Off — unknown emails wait in the list below'), D.ai === 'none' ? 'off' : null),
    src('Telegram', D.telegram, D.telegram ? 'Alerts, morning brief + chat active' : 'Run `python -m ledger setup` to link your bot', D.telegram ? null : 'off'),
    src('Apple Pay shortcut', D.applepay, D.applepay ? 'Ready — taps appear instantly' : 'Run `python -m ledger setup` to create its token', D.applepay ? null : 'off'),
    src('Phone widget', D.widget, D.widget ? 'Token set — see phone/ledger-widget.js' : 'Run `python -m ledger setup`', D.widget ? null : 'off'),
    src('Secrets', true, `Stored in ${esc(D.secrets)}`),
  ].join('');
  $('#y-paths').textContent = `Data folder: ${D.home}`;
  $('#y-quality').innerHTML = (D.quality || []).map(q => `<div class="callout">${q.ok ? '✅' : '⚠️'} ${esc(q.label)}<b>${esc(q.value)}</b>${esc(q.hint || '')}</div>`).join('');
  $('#y-accts').innerHTML = '<tr><th>Account</th><th class="num">Balance</th><th>Set</th></tr>' + D.accounts.map(a => `<tr><td>${esc(a.name)}<div class="desc">${esc(a.id)}${a.source ? ' · from ' + esc(a.source) : ''}</div></td>
    <td class="num">${a.balance == null ? '<span class="muted">unknown</span>' : fmt2(a.balance) + (a.approx ? ' ≈' : '')}</td>
    <td><form class="row balf" data-a="${esc(a.id)}"><input placeholder="${a.type === 'credit' ? 'owed, e.g. 320' : 'e.g. 1250'}" style="width:110px"><button class="btn small">Set</button></form></td></tr>`).join('');
  $$('.balf').forEach(f => f.onsubmit = async e => { e.preventDefault(); const v = parseFloat(f.querySelector('input').value); if (isNaN(v)) return;
    const acct = D.accounts.find(a => a.id === f.dataset.a); await api('/api/balance', { method: 'POST', body: { account_id: f.dataset.a, balance: acct.type === 'credit' ? -Math.abs(v) : v } });
    toast('Balance saved — Ledger keeps it current from here'); loadSystem(); });
  $('#y-unparsed').innerHTML = D.unparsed.length ? '<tr><th>Email</th><th></th></tr>' + D.unparsed.map(u => `<tr><td>${esc(u.subject)}<div class="desc">${esc(u.sender)} · ${u.date}</div></td><td class="num"><button class="btn small dis" data-m="${esc(u.msg_id)}">Dismiss</button></td></tr>`).join('') : '<tr><td class="empty">None — every bank email was understood.</td></tr>';
  $$('.dis').forEach(b => b.onclick = async () => { await api('/api/unparsed/dismiss', { method: 'POST', body: { msg_id: b.dataset.m } }); loadSystem(); });
  $('#y-rules').innerHTML = '<tr><th>When</th><th>Then</th><th></th></tr>' + (D.rules.map(r => `<tr><td>${esc(r.field)} is <b>${esc(r.pattern)}</b> <span class="muted">${r.direction ? '(money ' + r.direction + ')' : ''}</span></td>
    <td><span class="tag">${esc(r.category)}</span> ${r.kind ? '<span class="muted">as ' + r.kind + '</span>' : ''} ${r.origin === 'ai' ? '<span class="muted">· AI</span>' : ''}</td><td class="num"><button class="btn small del" data-id="${r.id}">✕</button></td></tr>`).join('') || '<tr><td colspan=3 class="empty">No custom rules yet.</td></tr>');
  $$('.del').forEach(b => b.onclick = async () => { const r = await api('/api/rules/' + b.dataset.id, { method: 'DELETE' }); toast(`Rule removed · ${r.recategorized} updated`); loadSystem(); });
  $('#y-budgets').innerHTML = D.categories.filter(c => c.kind === 'expense').map(c => `<div><span>${esc(c.name)}</span><input class="bud" data-c="${esc(c.name)}" value="${c.budget ?? ''}" placeholder="auto"></div>`).join('');
  $$('.bud').forEach(i => i.onchange = () => api('/api/budget', { method: 'POST', body: { category: i.dataset.c, budget: parseFloat(i.value) || null } }).then(() => toast('Budget saved')));
  $('#y-log').innerHTML = '<tr><th>When</th><th>What happened</th></tr>' + D.sync_log.map(l => { const s = l.summary || {}; const e = s.email || {};
    return `<tr><td class="muted" style="white-space:nowrap">${ago(l.at)}</td><td>${[e.new ? `${e.new} new from email` : '', e.merged ? `${e.merged} merged` : '', s.transfers_linked ? `${s.transfers_linked} transfers paired` : '', e.errors ? `${e.errors} inbox error(s)` : '', s.demo ? 'demo data' : ''].filter(Boolean).join(' · ') || 'nothing new'}</td></tr>`; }).join('');
}

// ───────── shared ─────────
function fillMonths(list) { const sel = $('#month'), cur = sel.value || thisMonth, ms = [...new Set([thisMonth, ...list])].sort().reverse();
  sel.innerHTML = ms.map(m => `<option value="${m}">${mname(m, true)}</option>`).join(''); sel.value = ms.includes(cur) ? cur : thisMonth; }
function setSync(at) { $('#lastsync').textContent = 'Synced ' + ago(at); const stale = !at || (Date.now() - new Date(at.replace(' ', 'T') + 'Z')) > 40 * 60000; $('#pulse').classList.toggle('stale', stale); $('#pulse2')?.classList.toggle('stale', stale); }
function badges(review, health) {
  if (review != null) { $('#rbadge').textContent = review; $('#rbadge').classList.toggle('hidden', !review); }
  if (health) $('#hbadge').classList.toggle('hidden', health.ok);
}
$('#askf').onsubmit = async e => { e.preventDefault(); const q = $('#askq').value.trim(); if (!q) return;
  $('#askout').textContent = 'Thinking…'; const r = await api('/api/ask', { method: 'POST', body: { q } }); $('#askout').textContent = r.answer || r.error; };
$('#syncbtn').onclick = async () => { const b = $('#syncbtn'); b.disabled = true; $('#lastsync').textContent = 'Checking…';
  try { const r = await api('/api/sync', { method: 'POST' }); toast((r.log || []).filter(l => l.trim()).slice(-1)[0]?.trim() || 'Synced'); } finally { b.disabled = false; load(); } };

// live: refresh the open page when new data lands (sync, Apple Pay tap, edits on another device)
setInterval(async () => { if (document.hidden) return; try { const v = (await api('/api/version')).version;
  if (VERSION != null && v !== VERSION) { VERSION = v; await load(); toast('Updated'); } } catch (e) { } }, 15000);

document.addEventListener('toggle', e => { if (e.target.matches?.('details.more') && e.target.open) Object.values(charts).forEach(c => c.resize()); }, true);
window.addEventListener('hashchange', () => { const h = location.hash.slice(1); if (h && h !== page && $('#p-' + h)) go(h); });

(async function init() {
  const M = await api('/api/meta');
  CATS = M.categories; ACCTS = M.accounts;
  $('#f-cat').innerHTML += CATS.map(c => `<option>${esc(c.name)}</option>`).join('');
  $('#f-acct').innerHTML += ACCTS.map(a => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
  const h = location.hash.slice(1); await go(['today', 'spending', 'plan', 'activity', 'subs', 'system'].includes(h) ? h : 'today');
})();
