// ---------- главный экран бота ----------
let BOT_ID = null;
let BOT_SEG_BUILDER = null;
let BOT_SEG_APPLIED = null;

async function openBot(id) {
  BOT_ID = id;
  BOT_SEG_APPLIED = null;
  BOT_SEG_BUILDER = null;
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.getElementById('page-bot').classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach(n =>
    n.classList.toggle('active', n.dataset.page === 'bots'));
  document.getElementById('bot-seg-panel').classList.add('hidden');
  document.getElementById('bot-seg-count').textContent = '';
  document.getElementById('bot-sub-search').value = '';
  await refreshBotOverview();
  botTab('subs');
}

async function refreshBotOverview() {
  const o = await api(`/bots/${BOT_ID}/overview`);
  document.getElementById('bot-title').innerHTML =
    `🤖 ${esc(o.name)} ${o.tg_username ? `<a href="https://t.me/${esc(o.tg_username)}" target="_blank" style="font-size:14px">@${esc(o.tg_username)}</a>` : ''}`;
  document.getElementById('bot-status-pill').innerHTML = o.is_active
    ? (o.running ? '<span class="pill" style="background:#e3f7ec;color:#12a150">работает</span>'
                 : '<span class="pill" style="background:#fdeaea;color:#d33">ошибка</span>')
    : '<span class="pill gray">выключен</span>';
  document.getElementById('bot-toggle-btn').textContent = o.is_active ? 'Выключить' : 'Включить';
  const err = document.getElementById('bot-error-banner');
  if (o.last_error && o.is_active && !o.running) {
    err.textContent = 'Ошибка бота: ' + o.last_error;
    err.classList.remove('hidden');
  } else err.classList.add('hidden');

  const s = o.stats;
  document.getElementById('bot-stats').innerHTML = [
    [s.subscribers, 'Подписчиков'],
    [s.active, 'Активных'],
    [s.blocked, 'Заблокировали'],
    ['+' + s.new_24h, 'Новых за 24ч'],
    ['+' + s.new_7d, 'Новых за неделю'],
    [s.active_24h, 'Активны за 24ч'],
    [s.funnels, 'Воронок'],
    [s.broadcasts, 'Рассылок'],
  ].map(([n, l]) => `<div class="card"><div class="num">${n}</div><div class="lbl">${l}</div></div>`).join('');
}

function botTab(tab) {
  document.querySelectorAll('#page-bot .tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === tab));
  document.querySelectorAll('.bot-tab').forEach(el => el.classList.add('hidden'));
  document.getElementById('bot-tab-' + tab).classList.remove('hidden');
  if (tab === 'subs') botLoadSubs();
  if (tab === 'funnels') botLoadFunnels();
  if (tab === 'broadcasts') botLoadBroadcasts();
}

// --- вкладка подписчиков (с чатом и сегментом) ---
let _botSubsTimer = null;
function botSubsDebounce() {        // поиск не дёргает сервер на каждую букву
  clearTimeout(_botSubsTimer);
  _botSubsTimer = setTimeout(botLoadSubs, 300);
}

async function botLoadSubs() {
  if (!TAGS.length) await loadTags();
  const search = document.getElementById('bot-sub-search').value.trim();
  let filter = BOT_SEG_APPLIED ? JSON.parse(JSON.stringify(BOT_SEG_APPLIED)) : { match: 'all', conditions: [] };
  if (search) filter = { match: 'all', active_24h: filter.active_24h,
    conditions: [...(filter.conditions || []), { field: 'name', op: 'contains', value: search }] };
  let res;
  try {
    res = await api('/subscribers/search', { method: 'POST', body: { bot_id: BOT_ID, filter, limit: 500 } });
  } catch (e) {
    document.getElementById('bot-subs-list').innerHTML =
      `<div class="panel" style="color:#d33">Ошибка загрузки: ${esc(e.message || e)}</div>`;
    return;
  }
  const subs = res.subscribers;
  document.getElementById('bot-subs-list').innerHTML = subs.length ? `
    <div class="list-meta">Найдено: <b>${res.total}</b>${res.total > subs.length ? ` (первые ${subs.length})` : ''}</div>
    <table>
    <tr><th>Имя</th><th>Username</th><th>Язык</th><th>Активность</th><th>Статус</th><th>Теги</th></tr>
    ${subs.map(s => `<tr>
      <td><a href="#" onclick="openChat(${s.id});return false"><b>${esc(s.first_name || '')} ${esc(s.last_name || '')}</b></a></td>
      <td>${s.username ? '@' + esc(s.username) : '—'}</td>
      <td>${esc(s.language_code || '—')}</td>
      <td>${s.last_active_at ? new Date(s.last_active_at + 'Z').toLocaleDateString('ru') : '—'}</td>
      <td>${s.is_active ? '<span class="status-active">активен</span>' : '<span class="status-off">блок</span>'}</td>
      <td>${s.tags.map(t => `<span class="pill">${esc(t.name)}</span>`).join('') || '—'}</td>
    </tr>`).join('')}
  </table>` : '<div class="panel">У этого бота пока нет подписчиков. Отправь боту /start, чтобы проверить.</div>';
}

async function botToggleSeg() {
  const panel = document.getElementById('bot-seg-panel');
  const willShow = panel.classList.contains('hidden');
  panel.classList.toggle('hidden');
  if (willShow && !BOT_SEG_BUILDER) {
    await loadSegFields();
    BOT_SEG_BUILDER = makeSegment(document.getElementById('bot-seg-builder'));
  }
}
async function botApplySeg() {
  BOT_SEG_APPLIED = BOT_SEG_BUILDER.getFilter();
  await botLoadSubs();
  const r = await api('/subscribers/search', { method: 'POST',
    body: { bot_id: BOT_ID, filter: BOT_SEG_APPLIED, count_only: true } });
  document.getElementById('bot-seg-count').textContent = `Подходит: ${r.total}`;
}
function botResetSeg() {
  if (BOT_SEG_BUILDER) BOT_SEG_BUILDER.reset();
  BOT_SEG_APPLIED = null;
  document.getElementById('bot-seg-count').textContent = '';
  botLoadSubs();
}

// --- вкладка воронок ---
async function botLoadFunnels() {
  const funnels = await api('/funnels?bot_id=' + BOT_ID);
  document.getElementById('bot-funnels-list').innerHTML = funnels.length ? `<table>
    <tr><th>Название</th><th>Триггер</th><th>Статус</th><th>Запусков</th><th></th></tr>
    ${funnels.map(f => `<tr>
      <td><a href="#" onclick="openEditor(${f.id});return false"><b>${esc(f.name)}</b></a></td>
      <td>${TRIGGER_LABEL[f.trigger_type] || f.trigger_type}${f.trigger_value ? ': ' + esc(f.trigger_value) : ''}</td>
      <td>${f.is_active ? '<span class="status-active">включена</span>' : '<span class="status-off">выключена</span>'}</td>
      <td>${f.runs}</td>
      <td><button class="btn" onclick="toggleFunnel(${f.id}); setTimeout(botLoadFunnels, 300)">${f.is_active ? 'Выключить' : 'Включить'}</button></td>
    </tr>`).join('')}
  </table>` : '<div class="panel">Этому боту ещё не назначены воронки. Создай новую или назначь существующую в редакторе воронки (строка «Боты»).</div>';
}
async function botNewFunnel() {
  const r = await api('/funnels', { method: 'POST',
    body: { name: 'Новая воронка', graph_ui: {}, bot_ids: [BOT_ID] } });
  openEditor(r.id);
}

// --- вкладка рассылок ---
async function botLoadBroadcasts() {
  const bcs = await api('/broadcasts?bot_id=' + BOT_ID);
  document.getElementById('bot-bc-list').innerHTML = bcs.length ? `<table>
    <tr><th>Название</th><th>Статус</th><th>Прогресс</th><th>Дата</th></tr>
    ${bcs.map(b => {
      const pct = b.total ? Math.round(100 * (b.sent + b.failed) / b.total) : 0;
      return `<tr>
        <td><b>${esc(b.name)}</b><br><span style="color:#7a8499;font-size:12px">${esc((b.text || '').slice(0, 60))}</span></td>
        <td>${BC_STATUS[b.status] || b.status}</td>
        <td>${b.sent}/${b.total}${b.failed ? ` (не дошло: ${b.failed})` : ''}
          <div class="progress"><i style="width:${pct}%"></i></div></td>
        <td>${new Date(b.created_at + 'Z').toLocaleString('ru')}</td>
      </tr>`;
    }).join('')}
  </table>` : '<div class="panel">Рассылок этому боту ещё не было.</div>';
  if (bcs.some(b => b.status === 'running' || b.status === 'pending')) {
    clearTimeout(window._botBcTimer);
    window._botBcTimer = setTimeout(() => {
      if (!document.getElementById('page-bot').classList.contains('hidden')) botLoadBroadcasts();
    }, 3000);
  }
}
async function botNewBroadcast() {
  go('broadcasts');
  await showBroadcastForm();
  document.getElementById('bc-bot').value = String(BOT_ID);
}

// --- генератор deep-link ---
let DL_USERNAME = null;
async function botDeepLink() {
  const o = await api(`/bots/${BOT_ID}/overview`);
  if (!o.tg_username) { alert('Сначала включи бота — нужен его @username'); return; }
  DL_USERNAME = o.tg_username;
  document.getElementById('dl-value').value = '';
  dlBuild();
  document.getElementById('dl-overlay').classList.remove('hidden');
  document.getElementById('dl-modal').classList.remove('hidden');
}
function closeDeepLink() {
  document.getElementById('dl-overlay').classList.add('hidden');
  document.getElementById('dl-modal').classList.add('hidden');
}
function dlBuild() {
  const el = document.getElementById('dl-value');
  // Telegram разрешает в start только латиницу, цифры, _ и -
  const clean = el.value.replace(/[^A-Za-z0-9_-]/g, '');
  if (clean !== el.value) el.value = clean;
  document.getElementById('dl-link').value =
    `https://t.me/${DL_USERNAME}` + (clean ? `?start=${clean}` : '');
}
function dlCopy() {
  const el = document.getElementById('dl-link');
  el.select(); document.execCommand('copy');
  flashStatus ? flashStatus('Скопировано') : alert('Скопировано');
}

// --- управление ботом ---
async function botScreenToggle() {
  try { await api(`/bots/${BOT_ID}/toggle`, { method: 'POST' }); }
  finally { refreshBotOverview(); }
}
async function botScreenCheck() {
  await checkBot(BOT_ID);
  refreshBotOverview();
}
async function botScreenRename() {
  const o = await api(`/bots/${BOT_ID}/overview`);
  const name = prompt('Новое название бота:', o.name);
  if (!name || name === o.name) return;
  await api(`/bots/${BOT_ID}`, { method: 'PUT', body: { name } });
  refreshBotOverview();
}
async function botScreenToken() {
  const token = prompt('Новый токен бота (от @BotFather):');
  if (!token) return;
  const o = await api(`/bots/${BOT_ID}/overview`);
  await api(`/bots/${BOT_ID}`, { method: 'PUT', body: { name: o.name, token } });
  alert('Токен обновлён');
  refreshBotOverview();
}
async function botScreenDelete() {
  if (!confirm('Удалить бота? Его подписчики и рассылки тоже удалятся.')) return;
  await api('/bots/' + BOT_ID, { method: 'DELETE' });
  go('bots');
}
