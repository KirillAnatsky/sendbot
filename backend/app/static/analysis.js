// ---------- анализ воронок (в стиле Amplitude) ----------
let AN_OPTIONS = null;
let AN_STEPS = [];

const AN_TYPES = [
  ['subscribed', '👋 Подписался на бота'],
  ['node', '🔀 Прошёл шаг воронки'],
  ['button', '👆 Кликнул кнопку'],
  ['broadcast', '📣 Получил рассылку'],
  ['message_in', '💬 Написал сообщение'],
];

async function loadAnalysisPage() {
  AN_OPTIONS = await api('/analysis/options');
  const bots = await api('/bots');
  const botSel = document.getElementById('an-bot');
  botSel.innerHTML = '<option value="">Все боты</option>' +
    bots.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  if (!AN_STEPS.length) AN_STEPS = [{ type: 'subscribed' }, { type: 'node' }];
  anRender();
}

function anAddStep() {
  if (AN_STEPS.length >= 10) return;
  AN_STEPS.push({ type: 'node' });
  anRender();
}

function anRender() {
  const box = document.getElementById('an-steps');
  box.innerHTML = AN_STEPS.map((st, i) => {
    const typeOpts = AN_TYPES.map(([v, l]) =>
      `<option value="${v}" ${st.type === v ? 'selected' : ''}>${l}</option>`).join('');
    let params = '';
    if (st.type === 'node' || st.type === 'button') {
      const fOpts = AN_OPTIONS.funnels.map(f =>
        `<option value="${f.id}" ${String(st.funnel_id) === String(f.id) ? 'selected' : ''}>${esc(f.name)}</option>`).join('');
      params += `<select class="an-funnel" data-i="${i}"><option value="">— воронка —</option>${fOpts}</select>`;
      const funnel = AN_OPTIONS.funnels.find(f => String(f.id) === String(st.funnel_id));
      if (funnel) {
        const nOpts = funnel.nodes.map(n =>
          `<option value="${n.id}" ${String(st.node_id) === String(n.id) ? 'selected' : ''}>${esc(n.label)}</option>`).join('');
        params += `<select class="an-node" data-i="${i}"><option value="">— шаг —</option>${nOpts}</select>`;
        if (st.type === 'button') {
          const node = funnel.nodes.find(n => String(n.id) === String(st.node_id));
          const btns = (node && node.buttons) || [];
          params += `<select class="an-btn" data-i="${i}">
            <option value="">любая кнопка</option>
            ${btns.map((b, bi) => `<option value="${bi}" ${String(st.button) === String(bi) ? 'selected' : ''}>${esc(b)}</option>`).join('')}
          </select>`;
        }
      }
    }
    if (st.type === 'broadcast') {
      const bOpts = AN_OPTIONS.broadcasts.map(b =>
        `<option value="${b.id}" ${String(st.broadcast_id) === String(b.id) ? 'selected' : ''}>${esc(b.name)}</option>`).join('');
      params += `<select class="an-bc" data-i="${i}"><option value="">— рассылка —</option>${bOpts}</select>`;
    }
    return `<div class="an-step">
      <span class="an-step-num">${i + 1}</span>
      <select class="an-type" data-i="${i}">${typeOpts}</select>
      ${params}
      <button class="btn danger" onclick="AN_STEPS.splice(${i},1);anRender()">✕</button>
    </div>`;
  }).join('');

  box.querySelectorAll('.an-type').forEach(s => s.onchange = e => {
    AN_STEPS[+e.target.dataset.i] = { type: e.target.value };
    anRender();
  });
  box.querySelectorAll('.an-funnel').forEach(s => s.onchange = e => {
    const st = AN_STEPS[+e.target.dataset.i];
    st.funnel_id = e.target.value; delete st.node_id; delete st.button;
    anRender();
  });
  box.querySelectorAll('.an-node').forEach(s => s.onchange = e => {
    AN_STEPS[+e.target.dataset.i].node_id = e.target.value;
    anRender();
  });
  box.querySelectorAll('.an-btn').forEach(s => s.onchange = e => {
    AN_STEPS[+e.target.dataset.i].button = e.target.value;
  });
  box.querySelectorAll('.an-bc').forEach(s => s.onchange = e => {
    AN_STEPS[+e.target.dataset.i].broadcast_id = e.target.value;
  });
}

function anStepLabel(st, i) {
  const t = AN_TYPES.find(([v]) => v === st.type);
  let label = t ? t[1] : st.type;
  if (st.type === 'node' || st.type === 'button') {
    const f = AN_OPTIONS.funnels.find(f => String(f.id) === String(st.funnel_id));
    const n = f && f.nodes.find(n => String(n.id) === String(st.node_id));
    if (n) label += `: ${n.label}`;
    if (st.type === 'button' && st.button !== undefined && st.button !== '') {
      const btns = (n && n.buttons) || [];
      label += ` «${btns[+st.button] || 'кнопка'}»`;
    }
  }
  if (st.type === 'broadcast') {
    const b = AN_OPTIONS.broadcasts.find(b => String(b.id) === String(st.broadcast_id));
    if (b) label += `: ${b.name}`;
  }
  return label;
}

async function anRun() {
  // валидация
  for (const st of AN_STEPS) {
    if ((st.type === 'node' || st.type === 'button') && (!st.funnel_id || !st.node_id)) {
      alert('Заполни воронку и шаг во всех строках'); return;
    }
    if (st.type === 'broadcast' && !st.broadcast_id) { alert('Выбери рассылку'); return; }
  }
  const status = document.getElementById('an-status');
  status.textContent = 'считаю…';
  let r;
  try {
    r = await api('/analysis/funnel', { method: 'POST', body: {
      steps: AN_STEPS,
      days: +document.getElementById('an-days').value,
      bot_id: +document.getElementById('an-bot').value || null,
    }});
  } catch (e) { status.textContent = ''; return; }
  status.textContent = '';
  document.getElementById('an-result').classList.remove('hidden');

  const labels = AN_STEPS.map((st, i) => `${i + 1}. ${anStepLabel(st, i)}`);
  destroyChart('chart-an');
  CHARTS['chart-an'] = new Chart(document.getElementById('chart-an'), {
    type: 'bar',
    data: { labels: labels.map(l => l.length > 45 ? l.slice(0, 45) + '…' : l),
      datasets: [{ label: 'Людей', data: r.steps.map(s => s.count),
        backgroundColor: r.steps.map((_, i) => i === 0 ? '#2e6bff' : 'rgba(46,107,255,' + Math.max(.25, 1 - i * .15) + ')'),
        borderRadius: 6 }] },
    options: { ...BASE_OPTS, plugins: { legend: { display: false } } },
  });

  document.getElementById('an-table').innerHTML = `<table>
    <tr><th>Шаг</th><th>Людей</th><th>От предыдущего</th><th>От первого</th></tr>
    ${r.steps.map((s, i) => `<tr>
      <td>${esc(labels[i])}</td>
      <td><b>${s.count}</b></td>
      <td>${i ? s.from_prev + '%' : '—'}</td>
      <td>${i ? s.from_first + '%' : '100%'}</td>
    </tr>`).join('')}
  </table>`;
}
