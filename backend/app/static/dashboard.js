// ---------- дашборд с графиками ----------
const CHARTS = {};
let GROWTH_MODE = 'daily';
let DASH_DATA = null;
let dashTimer = null;

const C = { blue: '#2e6bff', green: '#12a150', orange: '#e8a13c', purple: '#9a5df0',
            pink: '#e05c9a', gray: '#99a3b5' };
const PALETTE = [C.blue, C.green, C.orange, C.purple, C.pink, '#39b0d2', '#d2603a', C.gray];

function dashDebounce() {
  clearTimeout(dashTimer);
  dashTimer = setTimeout(loadDashboard, 400);
}
function resetDashFilters() {
  document.getElementById('dash-days').value = '30';
  document.getElementById('dash-bot').value = '';
  document.getElementById('dash-tag').value = '';
  document.getElementById('dash-lang').value = '';
  document.getElementById('dash-source').value = '';
  loadDashboard();
}
function setGrowthMode(mode) {
  GROWTH_MODE = mode;
  document.getElementById('btn-mode-daily').classList.toggle('active', mode === 'daily');
  document.getElementById('btn-mode-cum').classList.toggle('active', mode === 'cum');
  if (DASH_DATA) renderGrowth(DASH_DATA);
}

function destroyChart(id) { if (CHARTS[id]) { CHARTS[id].destroy(); delete CHARTS[id]; } }

const BASE_OPTS = {
  responsive: true, maintainAspectRatio: false, resizeDelay: 120,
  animation: false,
  interaction: { mode: 'index', intersect: false },
  plugins: { legend: { display: true, labels: { boxWidth: 12, font: { size: 11 } } } },
  scales: { y: { beginAtZero: true, ticks: { precision: 0 } },
            x: { ticks: { maxTicksLimit: 12, font: { size: 10 } }, grid: { display: false } } },
};

function shortDates(days) {
  return days.map(d => { const [, m, dd] = d.split('-'); return `${dd}.${m}`; });
}

async function fillDashFilters() {
  const [bots, tags] = await Promise.all([api('/bots'), api('/tags')]);
  const botSel = document.getElementById('dash-bot');
  const cur = botSel.value;
  botSel.innerHTML = '<option value="">Все боты</option>' +
    bots.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  botSel.value = cur;
  const tagSel = document.getElementById('dash-tag');
  const curT = tagSel.value;
  tagSel.innerHTML = '<option value="">Все теги</option>' +
    tags.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('');
  tagSel.value = curT;
}

async function loadDashboard() {
  await fillDashFilters();
  const qs = new URLSearchParams();
  qs.set('days', document.getElementById('dash-days').value);
  const bot = document.getElementById('dash-bot').value;
  const tag = document.getElementById('dash-tag').value;
  const lang = document.getElementById('dash-lang').value;
  const src = document.getElementById('dash-source').value.trim();
  if (bot) qs.set('bot_id', bot);
  if (tag) qs.set('tag_id', tag);
  if (lang) qs.set('language', lang);
  if (src) qs.set('source', src);

  let d;
  try { d = await api('/analytics?' + qs); }
  catch (e) {
    document.getElementById('stats-cards').innerHTML =
      `<div class="panel" style="color:#d33">Не удалось загрузить аналитику: ${esc(e.message || e)}</div>`;
    return;
  }
  DASH_DATA = d;
  document.getElementById('dash-updated').textContent =
    'обновлено ' + new Date().toLocaleTimeString('ru');

  // селект языков заполняем из данных
  const langSel = document.getElementById('dash-lang');
  if (langSel.options.length <= 1) {
    langSel.innerHTML = '<option value="">Все языки</option>' +
      d.breakdowns.languages.filter(l => l.k && l.k !== '—')
        .map(l => `<option value="${esc(l.k)}">${esc(l.k)}</option>`).join('');
    langSel.value = lang;
  }

  const t = d.totals;
  document.getElementById('stats-cards').innerHTML = [
    [t.subscribers, 'Подписчиков', 'bots'],
    ['+' + t.new_period, 'Новых за период', null],
    [t.active, 'Активных', 'subscribers'],
    [t.blocked, 'Заблокировали', null],
    [t.retention_7d + '%', 'Активны за 7 дней', null],
    [t.lifetime_avg_days + ' дн', 'Средний срок жизни', null],
    [t.lifetime_median_days + ' дн', 'Медианный срок', null],
  ].map(([n, l, page]) =>
    `<div class="card${page ? ' clickable' : ''}"${page ? ` onclick="go('${page}')"` : ''}>
      <div class="num">${n}</div><div class="lbl">${l}</div></div>`).join('');

  renderGrowth(d);
  renderActivity(d);
  renderBar('chart-lifetime', d.breakdowns.lifetime, C.purple, 'Подписчиков');
  renderDoughnut('chart-langs', d.breakdowns.languages);
  renderBar('chart-sources', d.breakdowns.sources, C.orange, 'Подписчиков');
  renderDoughnut('chart-bots', d.breakdowns.bots);
}

function renderGrowth(d) {
  destroyChart('growth');
  const labels = shortDates(d.days);
  const daily = GROWTH_MODE === 'daily';
  CHARTS.growth = new Chart(document.getElementById('chart-growth'), {
    type: daily ? 'bar' : 'line',
    data: {
      labels,
      datasets: [{
        label: daily ? 'Новых за день' : 'Всего подписчиков',
        data: daily ? d.series.new_subscribers : d.series.cumulative_subscribers,
        backgroundColor: daily ? C.blue : 'rgba(46,107,255,.15)',
        borderColor: C.blue, borderWidth: 2, fill: !daily, tension: .3,
        pointRadius: daily ? 0 : 2,
      }],
    },
    options: BASE_OPTS,
  });
}

function renderActivity(d) {
  destroyChart('activity');
  CHARTS.activity = new Chart(document.getElementById('chart-activity'), {
    type: 'line',
    data: {
      labels: shortDates(d.days),
      datasets: [
        { label: 'Входящие сообщения', data: d.series.incoming_messages,
          borderColor: C.green, backgroundColor: 'rgba(18,161,80,.12)', fill: true, tension: .3, pointRadius: 0, borderWidth: 2 },
        { label: 'Клики по кнопкам', data: d.series.button_clicks,
          borderColor: C.pink, backgroundColor: 'rgba(224,92,154,.10)', fill: true, tension: .3, pointRadius: 0, borderWidth: 2 },
      ],
    },
    options: BASE_OPTS,
  });
}

function renderBar(canvasId, items, color, label) {
  destroyChart(canvasId);
  const data = (items || []).filter(i => i.v > 0);
  const ctx = document.getElementById(canvasId);
  if (!data.length) { emptyChart(ctx); return; }
  CHARTS[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: { labels: data.map(i => i.k), datasets: [{ label, data: data.map(i => i.v), backgroundColor: color, borderRadius: 4 }] },
    options: { ...BASE_OPTS, indexAxis: 'y', plugins: { legend: { display: false } } },
  });
}

function renderDoughnut(canvasId, items) {
  destroyChart(canvasId);
  const data = (items || []).filter(i => i.v > 0);
  const ctx = document.getElementById(canvasId);
  if (!data.length) { emptyChart(ctx); return; }
  CHARTS[canvasId] = new Chart(ctx, {
    type: 'doughnut',
    data: { labels: data.map(i => i.k), datasets: [{ data: data.map(i => i.v), backgroundColor: PALETTE, borderWidth: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, resizeDelay: 120,
      animation: false, cutout: '58%',
      plugins: { legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } } } },
  });
}

function emptyChart(canvas) {
  // рисуем заглушку с учётом реального размера контейнера
  const wrap = canvas.parentElement;
  const w = wrap ? wrap.clientWidth : 300;
  const h = wrap ? wrap.clientHeight : 200;
  canvas.width = w; canvas.height = h;
  const c = canvas.getContext('2d');
  c.clearRect(0, 0, w, h);
  c.fillStyle = '#99a3b5';
  c.font = '13px -apple-system, sans-serif';
  c.textAlign = 'center';
  c.fillText('нет данных', w / 2, h / 2);
}
