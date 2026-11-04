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
const dshort = d => new Date(d + 'T12:00').toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
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
  location.hash = p;
  window.scrollTo(0, 0);
  return load();
}
$$('#nav a, #tabbar a').forEach(a => a.onclick = () => go(a.dataset.p));
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

// ───────── TODAY ─────────
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const NUM = {};  // last shown value per element, so numbers glide from old to new instead of jumping
function countTo(sel, v, f = fmt) {
  const el = $(sel); if (!el) return;
  const from = NUM[sel], to = v; NUM[sel] = v;
  if (v == null || from == null || reduceMotion || from === to) { el.textContent = v == null ? '—' : f(v); return; }
  const t0 = performance.now(), dur = 900;
  const step = t => { const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = f(from + (to - from) * e); if (k < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
// the dial: inner ring = today's money left, outer ring = how much of the day has gone (moves with the clock)
function dialTicks() {
  const g = $('#dial-ticks'); if (!g || g.childElementCount) return;
  for (let h = 0; h < 24; h++) {
    const a = (h / 24) * 2 * Math.PI - Math.PI / 2, r1 = h % 6 ? 117 : 113, r2 = 119;
    const l = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    l.setAttribute('x1', 130 + r1 * Math.cos(a)); l.setAttribute('y1', 130 + r1 * Math.sin(a));
    l.setAttribute('x2', 130 + r2 * Math.cos(a)); l.setAttribute('y2', 130 + r2 * Math.sin(a));
    l.setAttribute('class', h % 6 ? 'tick' : 'tick major'); g.appendChild(l);
  }
}
function dayFrac() { const n = new Date(); return (n.getHours() * 60 + n.getMinutes()) / 1440; }
function tickClock() {
  const f = dayFrac(), a = f * 2 * Math.PI - Math.PI / 2;
  $('#dial-time')?.style.setProperty('stroke-dasharray', `${f * 1000} 1000`);
  const h = $('#dial-hand'); if (h) { h.setAttribute('cx', 130 + 123 * Math.cos(a)); h.setAttribute('cy', 130 + 123 * Math.sin(a)); }
  const now = new Date();
  const c = $('#clock'); if (c) c.textContent = now.toLocaleDateString('en-CA', { weekday: 'long', month: 'long', day: 'numeric' }) + ', ' +
    now.toLocaleTimeString('en-CA', { hour: 'numeric', minute: '2-digit' });
  if (TODAY) heroLine(TODAY);
}
let TODAY = null, SEEN_TX = null;
function heroLine(T) {
  const d = T.today, pct = Math.round(dayFrac() * 100);
  let s;
  if (d.allowance <= 0) s = `There's no room left in this month's flexible money, so today is a no-spend day if you can manage it.`;
  else if (d.spent <= 0) s = `Nothing spent yet today. You have ${fmt(d.allowance)} to work with, and ${pct}% of the day is gone.`;
  else if (d.left >= 0) s = `${fmt(d.spent)} spent of today's ${fmt(d.allowance)}${d.purchases ? ` across ${d.purchases} purchase${d.purchases > 1 ? 's' : ''}` : ''}. ` +
    (d.spent / d.allowance > dayFrac() + .15 ? `You're ahead of the day: ${pct}% of it is gone.` : `Right on pace with ${pct}% of the day gone.`);
  else s = `${fmt(-d.left)} over today's ${fmt(d.allowance)}. It comes out of the rest of the month: tomorrow's number will be a little smaller.`;
  $('#hero-line').textContent = s;
}
async function loadToday() {
  const T = await api('/api/today?month=' + month());
  TODAY = T; VERSION = T.version; fillMonths(T.months); setSync(T.last_sync); badges(T.to_review, T.health);
  const S = T.safe, D = T.today;
  $('#headline').className = 'headline ' + T.tone; $('#hl-text').textContent = T.headline;

  // dial
  dialTicks();
  const frac = D.allowance > 0 ? Math.max(0, Math.min(1, D.left / D.allowance)) : 0;
  const m = $('#dial-money');
  m.classList.toggle('over', D.left < 0);
  m.style.strokeDasharray = `${(D.left < 0 ? 1 : frac) * 1000} 1000`;
  $('#hero').classList.toggle('over', D.left < 0);
  countTo('#t-today', Math.abs(D.left));
  $('#dial-cap').textContent = D.left < 0 ? 'over today' : `left of ${fmt(D.allowance)} today`;
  tickClock();

  // month runway: what's spent, what's promised to bills, where today sits
  // flexible money for the month: what's spent so far, what's held back, and what's still free (bills are already out)
  const total = Math.max(1, S.flexible_budget), spent = Math.max(0, S.spent), bills = Math.max(0, S.hold_back || 0);
  const daysIn = S.days_left + new Date().getDate() - 1 || 30, timeFrac = (new Date().getDate() - 1 + dayFrac()) / daysIn;
  $('#r-spent').style.width = Math.min(100, spent / total * 100) + '%';
  $('#r-bills').style.width = Math.min(100, bills / total * 100) + '%';
  $('#r-now').style.left = Math.min(100, timeFrac * 100) + '%';
  $('#runway-title').textContent = new Date().toLocaleString('en-CA', { month: 'long' });
  $('#runway-left').innerHTML = `<b>${fmt(S.safe_to_spend)}</b> left · ${S.days_left} days`;
  $('#runway-legend').innerHTML = `<span><i class="k-spent"></i>Spent ${fmt(spent)} of ${fmt(S.flexible_budget)} flexible</span>` +
    (bills ? `<span><i class="k-bills"></i>Held back ${fmt(bills)}</span>` : '') + `<span><i class="k-free"></i>Still free</span><span><i class="k-now"></i>Today</span>` +
    (S.upcoming_total ? `<span class="muted">Bills still due this month (${fmt(S.upcoming_total)}) are already set aside.</span>` : '');

  // quiet stats
  const C = T.cash;
  if (C.complete) { countTo('#t-cash', C.cash); $('#t-cash-sub').textContent = C.investments ? `plus ${fmt(C.investments)} invested` : (C.approx ? 'approximate' : 'across your accounts'); }
  else { $('#t-cash').textContent = 'Not set'; NUM['#t-cash'] = null; $('#t-cash-sub').innerHTML = `<a class="link" data-go="system">Add balances</a>`; }
  const F = T.forecast;
  $('#t-fc-k').textContent = 'By ' + new Date(F.horizon + 'T12:00').toLocaleDateString('en-CA', { month: 'short', year: 'numeric' });
  if (F.relative) { countTo('#t-fc', F.end, v => (v >= 0 ? '+' : '') + fmt(v)); $('#t-fc-sub').textContent = 'change at current habits'; }
  else { countTo('#t-fc', F.low.balance); $('#t-fc-sub').textContent = `lowest point, ${dshort(F.low.date)}`; }
  const K = T.kpis;
  countTo('#t-net', K.net, v => (v >= 0 ? '+' : '') + fmt(v));
  $('#t-net-sub').textContent = `${fmt(K.income)} in, ${fmt(K.spent_gross ?? K.expenses)} out`;
  $('#t-net').classList.toggle('neg', K.net < 0);

  // the river: projected cash to the horizon
  const ser = F.series;
  $('#t-river-sub').textContent = F.relative ? `Change from today if things carry on like the last few months. Add balances to see real amounts.`
    : (F.below_buffer ? `Dips under your ${fmt(F.buffer)} cushion around ${dshort(F.below_buffer)}.` : `Stays above your ${fmt(F.buffer)} cushion all the way to ${dshort(F.horizon)}.`);
  const ctx = $('#c-spark').getContext('2d'), grad = ctx.createLinearGradient(0, 0, 0, 240);
  grad.addColorStop(0, css('--ice') + '55'); grad.addColorStop(1, css('--ice') + '00');
  chart('c-spark', { type: 'line', data: { labels: ser.map(p => p[0]), datasets: [
    { label: 'Cash', data: ser.map(p => p[1]), borderColor: css('--ice'), backgroundColor: grad, fill: 'origin', borderWidth: 2.5, pointRadius: 0, tension: .35 },
    ...(F.relative ? [] : [{ label: 'Cushion', data: ser.map(() => F.buffer), borderColor: css('--brass'), borderDash: [5, 5], borderWidth: 1.2, pointRadius: 0 }])] },
    options: opts({ animation: reduceMotion ? false : { duration: 900, easing: 'easeOutCubic' },
      scales: { x: { grid: { display: false }, border: { display: false }, ticks: { color: css('--text-muted'), maxTicksLimit: 7, callback: function (v) { const l = this.getLabelForValue(v); return new Date(l + 'T12:00').toLocaleDateString('en-CA', { month: 'short' }); } } },
        y: { beginAtZero: false, grid: { color: css('--grid') }, border: { display: false }, ticks: { color: css('--text-muted'), callback: v => fmt(v), maxTicksLimit: 5 } } },
      plugins: { legend: { display: false }, tooltip: opts().plugins.tooltip } }) });

  // live stream: newest first; anything that arrived since the last refresh glows briefly
  const ids = new Set(T.recent.map(t => t.id));
  $('#t-stream').innerHTML = T.recent.slice(0, 8).map(t => {
    const fresh = SEEN_TX && !SEEN_TX.has(t.id), inn = t.amount > 0;
    return `<li class="${fresh ? 'fresh' : ''}"><span class="s-dot ${inn ? 'in' : t.kind === 'transfer' ? 'xfer' : 'out'}"></span>
      <span class="s-main"><span class="s-name">${esc(t.merchant || '—')}</span><span class="s-meta">${esc(t.category)} · ${esc(t.account || '')}</span></span>
      <span class="s-when">${dshort(t.date)}</span><span class="s-amt ${inn ? 'in' : ''}">${inn ? '+' : ''}${fmt2(t.amount)}</span></li>`; }).join('') || '<li class="muted">Nothing yet.</li>';
  SEEN_TX = ids;

  // next three weeks: a strip of days, then the list
  const days = [...Array(21)].map((_, i) => { const d = new Date(); d.setDate(d.getDate() + i); return d.toISOString().slice(0, 10); });
  const byDay = {}; T.upcoming.forEach(u => (byDay[u.date] ||= []).push(u));
  $('#t-strip').innerHTML = days.map((d, i) => { const u = byDay[d] || [], dt = new Date(d + 'T12:00');
    return `<div class="sd ${i === 0 ? 'today' : ''} ${dt.getDay() === 0 || dt.getDay() === 6 ? 'we' : ''}" title="${dshort(d)}${u.length ? ': ' + esc(u.map(x => x.label).join(', ')) : ''}">
      <span class="sd-n">${dt.getDate()}</span><span class="sd-dots">${u.slice(0, 3).map(x => `<i class="${x.kind}"></i>`).join('')}</span></div>`; }).join('');
  $('#t-upcoming').innerHTML = T.upcoming.length ? T.upcoming.slice(0, 7).map(u => `<li><span class="when">${dshort(u.date)}</span>
    <span><i class="dotk ${u.kind}"></i>${esc(u.label)}</span>
    <span class="amt ${u.amount > 0 ? 'pos' : ''}">${u.amount == null ? '' : (u.amount > 0 ? '+' : '') + fmt(u.amount)}</span></li>`).join('') : '<li class="muted">Nothing due in the next three weeks.</li>';

  // what Ledger noticed: messages + its own actions, newest first
  const items = [...T.messages.map(m => ({ at: m.created, ic: 'msg', html: `<b>${esc(m.title.replace(/^\p{Extended_Pictographic}\s*/u, ''))}</b><div class="sec">${esc(m.body.length > 200 ? m.body.slice(0, 200) + '…' : m.body)}</div>` })),
    ...T.activity.map(a => ({ at: a.at, ic: a.kind, html: esc(a.text) }))]
    .sort((a, b) => b.at.localeCompare(a.at)).slice(0, 7);
  $('#t-feed').innerHTML = items.length ? items.map(i => `<li><span class="ic ic-${esc(i.ic)}"></span><span>${i.html}<div class="t">${ago(i.at)}</div></span></li>`).join('') : '<li class="muted">Quiet so far.</li>';

  renderGoals('#t-goals', T.goals, true);
}
setInterval(() => { if (!document.hidden && page === 'today') tickClock(); }, 30000);
$('#askchips') && $$('#askchips button').forEach(b => b.onclick = () => { $('#askq').value = b.textContent; $('#askf').requestSubmit(); });

function renderGoals(sel, G, compact) {
  if (!G.goals.length) { $(sel).innerHTML = '<div class="empty">No goals yet.</div>'; return; }
  $(sel).innerHTML = (G.needs_balances ? `<div class="sub">Set your balances (System) so Ledger can fund these from real cash.</div>` : '') + G.goals.map(g => `
    <div class="goal"><div class="top"><span>${g.done ? '✅ ' : ''}${esc(g.name)}</span>
      <span class="sec">${g.funded == null ? '' : fmt(g.funded) + ' / '}<span ${compact ? '' : `class="link" data-goal="${g.id}"`}>${fmt(g.target)}</span></span></div>
      <div class="track"><div class="f ${g.done ? 'good' : ''}" style="width:${g.pct == null ? 0 : Math.min(100, g.pct * 100)}%"></div></div>
      <div class="meta">${[g.due ? 'by ' + dshort(g.due) : '', g.per_month ? `save ${fmt(g.per_month)}/mo to make it` : '', g.why || ''].filter(Boolean).join(' · ')}</div></div>`).join('');
  $$(sel + ' [data-goal]').forEach(el => el.onclick = async () => {
    const v = prompt('New target ($):', ''); if (!v) return;
    await api('/api/goal', { method: 'POST', body: { id: +el.dataset.goal, target: parseFloat(v) } }); toast('Goal updated'); load();
  });
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
    ['Kept', fmt(k.net), k.savings_rate != null ? `${Math.round(k.savings_rate * 100)}% savings rate` : 'no income yet'],
    (() => { const ref = k.projected_expenses ?? k.expenses, d = ref - k.avg_expenses_3m;
      return ['vs usual', (d >= 0 ? '+' : '') + fmt(d), k.projected_expenses != null ? 'where this month is heading vs your 3-month average' : 'vs your 3-month average']; })()]
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
  chart('c-monthly', { type: 'bar', data: { labels: ms, datasets: [bar('Income', D.monthly.map(x => x.income), css('--s1')), bar('Spending', D.monthly.map(x => x.expenses), css('--s2'))] },
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
    `From ${fmt(F.start)} today through ${dshort(F.horizon)} · buffer ${fmt(F.buffer)}`;
  const labels = F.series.map(p => p[0]);
  const markIdx = new Set(F.markers.map(m => labels.findIndex(l => l >= m.date)).filter(i => i >= 0));
  chart('c-forecast', { type: 'line', data: { labels, datasets: [
    { label: 'Projected cash', data: F.series.map(p => p[1]), borderColor: css('--s1'), backgroundColor: css('--s7'), borderWidth: 2, tension: .15,
      pointRadius: labels.map((_, i) => markIdx.has(i) ? 5 : 0), pointHoverRadius: 5, pointBackgroundColor: css('--s7') },
    ...(F.relative ? [] : [{ label: 'Buffer', data: labels.map(() => F.buffer), borderColor: css('--text-muted'), borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0 }]),
    ...(D.base ? [{ label: 'Without what-if', data: D.base.series.map(p => p[1]), borderColor: css('--text-muted'), borderWidth: 1.5, pointRadius: 0, tension: .15 }] : [])] },
    options: (() => { const o = opts({ scales: { x: { type: 'category', grid: { display: false }, ticks: { color: css('--text-muted'), maxTicksLimit: 10, callback: (v, i) => mname(labels[i].slice(0, 7)) } },
      y: { beginAtZero: false, grid: { color: css('--grid') }, border: { display: false }, ticks: { color: css('--text-muted'), callback: v => fmt(v) } } } });
      o.plugins.tooltip.callbacks.title = c => dshort(c[0].label);
      o.plugins.tooltip.filter = c => c.dataset.label !== 'Buffer';
      o.plugins.tooltip.callbacks.afterBody = c => F.markers.filter(m => m.date === c[0].label).map(m => '◆ ' + m.label);
      return o; })() });
  $('#c-forecast').closest('.card').querySelector('.legend').innerHTML = `<span><i style="background:var(--s1)"></i>${D.base ? 'With what-if' : 'Projected cash'}</span>` +
    (D.base ? '<span><i style="background:var(--text-muted)"></i>Without</span>' : '') + '<span><i class="dash"></i>Buffer</span>';
  $('#w-result').textContent = D.base ? `With this what-if: lowest ${fmt(F.low.balance)} (${dshort(F.low.date)}) vs ${fmt(D.base.low.balance)} without — ${F.low.balance >= F.buffer ? 'you stay above your buffer the whole way.' : F.low.balance >= 0 ? 'you dip into your buffer but never run out.' : `still ${fmt(-F.low.balance)} short at the low point.`}` : '';
  $('#f-callouts').innerHTML = [
    ['Lowest point', fmt(F.low.balance), dshort(F.low.date)],
    ['At ' + dshort(F.horizon), fmt(F.end), F.relative ? 'net change' : 'projected cash'],
    ...(F.below_zero ? [['Runs out', dshort(F.below_zero), 'at current habits']] : F.below_buffer ? [['Under buffer', dshort(F.below_buffer), 'tighten before then']] : [['Runway', 'Covered', 'never below your buffer']]),
    ['Everyday spending', fmt(F.assumptions.everyday_per_day * 30.4) + '/mo', 'what the forecast assumes']]
    .map(([a, b, c]) => `<div class="callout">${a}<b>${b}</b>${c}</div>`).join('');
  // math
  const S = D.safe;
  $('#f-math').innerHTML = `<div class="math">
    <div><span>Expected income this month</span><span>${fmt(S.income)}</span></div>
    <div class="ind"><span>Pay ${S.pay ? `(${fmt(S.pay.typical)} every ${S.pay.every_days} days)` : ''}</span><span>${fmt(S.pay_monthly)}</span></div>
    <div class="ind"><span>Other income (avg)</span><span>${fmt(S.other_income_monthly)}</span></div>
    <div><span>− Savings goal (${D.goal_pct}%)</span><span>${fmt(S.savings_set_aside)}</span></div>
    <div><span>− Fixed bills</span><span>${fmt(S.fixed_monthly)}</span></div>
    ${S.fixed_items.map(f => `<div class="ind"><span>${esc(f.merchant)}${f.off ? ` <span class="tag">off: ${esc(f.off)}</span>` : ''}</span><span>${fmt(f.amount)}</span></div>`).join('')}
    ${S.hold_back ? `<div><span>− Held back for later (${esc(S.hold_reason)})</span><span>${fmt(S.hold_back)}</span></div>` : ''}
    <div><span>− Everyday spending so far</span><span>${fmt(S.spent)}</span></div>
    <div class="total"><span>Safe to spend, rest of month</span><span>${fmt(S.safe_to_spend)}</span></div></div>`;
  renderGoals('#f-goals', D.goals, false);
  $('#f-events').innerHTML = [...D.events].sort((a, b) => (a.date || a.start).localeCompare(b.date || b.start)).map(e => { const d = e.date || e.start; const amt = e.amount || e.budget || e.monthly;
    return `<li><span class="when">${dshort(d)}</span><span><i class="dotk event"></i>${esc(e.name)} <span class="tag">${esc(e.kind)}</span>${e.end ? ` <span class="muted">→ ${dshort(e.end)}</span>` : ''}</span>
    <span class="amt">${amt ? fmt(amt) : (e.kind === 'one_time' || e.kind === 'income' ? '<span class="muted">set amount</span>' : '')}</span></li>`; }).join('');
  const sv = D.savings, ms = sv.months.map(x => mname(x.month));
  $('#f-sav-sub').textContent = sv.of ? `Goal ${D.goal_pct}% · hit ${sv.hit} of last ${sv.of} months · kept ${fmt(sv.saved_6m)}, invested ${fmt(sv.invested_6m)}` : `Goal ${D.goal_pct}%`;
  chart('c-rate', { type: 'line', data: { labels: ms, datasets: [
    { label: 'Kept', data: sv.months.map(x => x.savings_rate == null ? null : Math.round(x.savings_rate * 100)), borderColor: css('--s3'), backgroundColor: css('--s3'), borderWidth: 2, pointRadius: 4, spanGaps: true },
    { label: 'Goal', data: ms.map(() => D.goal_pct), borderColor: css('--text-muted'), borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0 }] },
    options: (() => { const o = opts(); o.scales.y.ticks.callback = v => v + '%'; o.plugins.tooltip.callbacks.label = c => ` ${c.dataset.label} ${c.parsed.y}%`; return o; })() });
  $('#f-months').innerHTML = '<tr><th>Month</th><th class="num">In</th><th class="num">Fixed</th><th class="num">Everyday</th><th class="num">Events</th><th class="num">End cash</th></tr>' +
    F.months.map(m => `<tr><td>${mname(m.month, true)}</td><td class="num pos">${fmt(m.income)}</td><td class="num">${fmt(m.fixed)}</td><td class="num">${fmt(m.flexible)}</td><td class="num">${m.events ? fmt(m.events) : '<span class="muted">—</span>'}</td><td class="num" style="font-weight:600">${fmt(m.end)}</td></tr>`).join('');
  const A = F.assumptions;
  $('#f-assume').innerHTML = `Assumes: ${A.pay ? `pay ${fmt(A.pay.amount)} every ${A.pay.every_days} days${A.pay.until ? ' until ' + dshort(A.pay.until) : ''}` : 'no regular pay detected'} ·
    fixed ${Object.entries(A.fixed).map(([k, v]) => `${esc(k)} ${fmt(v)}`).join(', ') || 'none yet'} · everyday ${fmt(A.everyday_per_day)}/day · other income ${fmt(A.other_income_monthly)}/mo.
    ${A.missing_balances.length ? `<br>Missing balances: ${A.missing_balances.map(esc).join(', ')}.` : ''} Edit life events in config.toml.`;
}
$('#goalf').onsubmit = async e => { e.preventDefault(); if (!$('#g-name').value) return;
  await api('/api/goal', { method: 'POST', body: { name: $('#g-name').value, target: parseFloat($('#g-target').value) || 0, due: $('#g-due').value || null } });
  $('#g-name').value = $('#g-target').value = ''; toast('Goal added'); loadPlan(); };

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
      <td class="num ${t.amount > 0 ? 'pos' : ''}" style="font-weight:600">${t.amount > 0 ? '+' : ''}${fmt2(t.amount)}</td>
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
  const O = await api('/api/overview?month=' + month());
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
  const src = (name, ok, detail, state) => `<div class="src"><div class="nm"><span class="st ${state || (ok ? 'ok' : 'bad')}">${state === 'off' ? 'off' : ok ? 'ok' : 'problem'}</span>${esc(name)}</div><div class="sec" style="margin-top:4px">${detail}</div></div>`;
  const bykey = Object.fromEntries(D.sources.map(s => [s.key, s]));
  $('#y-sources').innerHTML = [
    ...D.inboxes.map(i => { const s = bykey['inbox:' + i.address] || {}; const ok = i.has_password && !(s.fail_streak > 0);
      return src(i.address, ok, !i.has_password ? 'No app password in .env yet' : s.fail_streak ? esc(s.last_error) : `Last check ${ago(s.last_ok)} · ${s.items || 0} new so far`); }),
    src('Downloads watcher', true, D.watch.length ? `Watching ${D.watch.map(esc).join(', ')} for bank exports` : 'Off', D.watch.length ? null : 'off'),
    src('Local AI (reads unknown emails & merchants)', D.ai !== 'none', D.ai !== 'none' ? `Using ${D.ai}` : ({
      'no-model': 'Ollama is running but the model isn\'t downloaded — run: ollama pull qwen3:8b',
      'stopped': 'Ollama is installed — starting it…',
      'missing': 'Not installed (optional) — ollama.com',
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
$('#syncbtn').onclick = async () => { const b = $('#syncbtn'); b.disabled = true; b.textContent = 'Syncing…';
  try { const r = await api('/api/sync', { method: 'POST' }); toast((r.log || []).filter(l => l.trim()).slice(-1)[0]?.trim() || 'Synced'); } finally { b.disabled = false; b.textContent = 'Sync now'; load(); } };

// live: refresh the open page when new data lands (sync, Apple Pay tap, edits on another device)
setInterval(async () => { if (document.hidden) return; try { const v = (await api('/api/version')).version;
  if (VERSION != null && v !== VERSION) { VERSION = v; await load(); toast('Updated'); } } catch (e) { } }, 15000);

window.addEventListener('hashchange', () => { const h = location.hash.slice(1); if (h && h !== page && $('#p-' + h)) go(h); });

(async function init() {
  const M = await api('/api/meta');
  CATS = M.categories; ACCTS = M.accounts;
  $('#f-cat').innerHTML += CATS.map(c => `<option>${esc(c.name)}</option>`).join('');
  $('#f-acct').innerHTML += ACCTS.map(a => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('');
  const h = location.hash.slice(1); await go(['today', 'spending', 'plan', 'activity', 'subs', 'system'].includes(h) ? h : 'today');
})();
