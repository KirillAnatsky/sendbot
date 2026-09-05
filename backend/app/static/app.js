// ---------- базовое ----------
let TOKEN = localStorage.getItem('sb_token') || '';
let TAGS = [];

async function api(path, opts = {}) {
  opts.headers = Object.assign(
    { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN },
    opts.headers || {}
  );
  if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  const r = await fetch('/api' + path, opts);
  if (r.status === 401) { showLogin(); throw new Error('auth'); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { alert(data.detail || 'Ошибка'); throw new Error(data.detail || 'error'); }
  return data;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

// ---------- логин ----------
function showLogin() {
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('app').classList.add('hidden');
  // курсор сразу в нужное поле: логин уже заполнен — значит в пароль
  setTimeout(() => {
    const login = document.getElementById('login-name');
    const pw = document.getElementById('login-password');
    (login.value.trim() ? pw : login).focus();
  }, 50);
  setupTelegramLogin();
}
function showApp() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  // если в адресе уже есть раздел (#broadcasts) и он доступен — открываем его
  const fromHash = location.hash.slice(1);
  const hashOk = fromHash && loaders[fromHash]
    && !(PAGE_PERM[fromHash] && !can(PAGE_PERM[fromHash]))
    && !(fromHash === 'team' && ME.role !== 'owner');
  const page = hashOk ? fromHash : firstAllowedPage();
  if (page) go(page);
  else document.getElementById('app').innerHTML =
    '<div class="panel" style="margin:40px">Вам пока не выдали доступ ни к одному разделу. Обратитесь к владельцу аккаунта.</div>';
}
let ME = null;  // текущий пользователь

async function doLogin() {
  const login = document.getElementById('login-name').value.trim();
  const pw = document.getElementById('login-password').value;
  const errEl = document.getElementById('login-error');
  errEl.textContent = '';
  try {
    let r;
    try {
      r = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login, password: pw }),
      });
    } catch {
      // сеть упала — это не «неверный пароль», говорим честно
      errEl.textContent = 'Сервер недоступен — проверьте соединение';
      return;
    }
    if (!r.ok) throw new Error();
    const data = await r.json();
    TOKEN = data.token;
    ME = data.user;
    localStorage.setItem('sb_token', TOKEN);
    applyRoleUI();
    showApp();
  } catch {
    document.getElementById('login-error').textContent = 'Неверный логин или пароль';
  }
}

// ---------- вход через Telegram ----------
// Виджет Telegram — это <script> с параметрами, который сам рисует кнопку и
// зовёт наш колбэк с подписанными данными. Подпись проверяет сервер.
async function setupTelegramLogin() {
  let cfg;
  try { cfg = await (await fetch('/api/auth/telegram/config')).json(); }
  catch (e) { return; }
  if (!cfg.enabled || !cfg.bot_username) return;   // причина — в cfg.reason

  const box = document.getElementById('tg-login');
  const holder = document.getElementById('tg-login-widget');
  holder.innerHTML = '';
  const sc = document.createElement('script');
  sc.async = true;
  sc.src = 'https://telegram.org/js/telegram-widget.js?22';
  sc.setAttribute('data-telegram-login', cfg.bot_username);
  sc.setAttribute('data-size', 'large');
  sc.setAttribute('data-userpic', 'false');
  sc.setAttribute('data-onauth', 'onTelegramAuth(user)');
  sc.setAttribute('data-request-access', 'write');
  holder.appendChild(sc);
  box.classList.remove('hidden');
}

async function onTelegramAuth(user) {
  const errEl = document.getElementById('login-error');
  errEl.textContent = '';
  try {
    const r = await fetch('/api/auth/telegram', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(user),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { errEl.textContent = data.detail || 'Не удалось войти через Telegram'; return; }
    TOKEN = data.token; ME = data.user;
    localStorage.setItem('sb_token', TOKEN);
    applyRoleUI();
    showApp();
  } catch (e) {
    errEl.textContent = 'Сервер недоступен';
  }
}

// Та же кнопка, но уже внутри админки: привязать свой телеграм к своей учётке.
async function linkMyTelegram() {
  let cfg;
  try { cfg = await api('/auth/telegram/config'); } catch (e) { return; }
  if (!cfg.enabled) {
    alert(cfg.reason || 'Вход через Telegram не настроен.');
    return;
  }
  const box = document.getElementById('tg-link-modal');
  box.innerHTML = `<div class="dl-head"><b>Привязать Telegram</b>
      <button class="btn" onclick="document.getElementById('tg-link-modal').classList.add('hidden')">✕</button></div>
    <p style="font-size:13px;color:#7a8499">Нажмите кнопку — Telegram подтвердит, что аккаунт ваш.</p>
    <div id="tg-link-widget"></div>`;
  const sc = document.createElement('script');
  sc.async = true;
  sc.src = 'https://telegram.org/js/telegram-widget.js?22';
  sc.setAttribute('data-telegram-login', cfg.bot_username);
  sc.setAttribute('data-size', 'large');
  sc.setAttribute('data-userpic', 'false');
  sc.setAttribute('data-onauth', 'onTelegramLink(user)');
  sc.setAttribute('data-request-access', 'write');
  document.getElementById('tg-link-widget').appendChild(sc);
  box.classList.remove('hidden');
}

async function onTelegramLink(user) {
  try {
    const r = await api('/auth/telegram/link', { method: 'POST', body: user });
    ME.tg_id = r.tg_id; ME.tg_username = r.tg_username;
    document.getElementById('tg-link-modal').classList.add('hidden');
    applyRoleUI();
    alert('Телеграм привязан: @' + (r.tg_username || r.tg_id));
  } catch (e) { /* alert показан в api() */ }
}

// ---------- права текущего пользователя ----------
const LEVEL_RANK = { none: 0, view: 1, edit: 2 };

// can('funnels')          — доступен ли раздел хотя бы на просмотр
// can('funnels', 'edit')  — можно ли изменять
function can(feature, level = 'view') {
  if (!ME) return false;
  if (ME.role === 'owner') return true;
  const have = (ME.permissions || {})[feature] || 'none';
  return LEVEL_RANK[have] >= LEVEL_RANK[level];
}

// отдельное право на удаление — общее для всех разделов
function canDelete() { return can('delete', 'edit'); }

// разделы меню и то, какое право им нужно
const PAGE_PERM = {
  dashboard: 'analytics', analysis: 'analytics', bots: 'bots',
  funnels: 'funnels', subscribers: 'subscribers', tags: 'tags',
  broadcasts: 'broadcasts', ai: 'ai', logs: 'logs', sheets: 'integrations',
};

// прячем разделы и кнопки, на которые нет прав
function applyRoleUI() {
  const owner = ME && ME.role === 'owner';
  document.querySelectorAll('.owner-only').forEach(el => el.classList.toggle('hidden', !owner));

  // пункты меню
  document.querySelectorAll('.nav-item[data-page]').forEach(el => {
    const page = el.dataset.page;
    if (page === 'team') { el.classList.toggle('hidden', !owner); return; }
    const feature = PAGE_PERM[page];
    el.classList.toggle('hidden', feature ? !can(feature) : false);
  });

  // элементы, помеченные правом: data-perm="funnels" [data-perm-level="edit"]
  document.querySelectorAll('[data-perm]').forEach(el => {
    const lvl = el.dataset.permLevel || 'view';
    el.classList.toggle('hidden', !can(el.dataset.perm, lvl));
  });

  document.getElementById('side-user-name').textContent =
    ME ? `${ME.name || ME.login}${owner ? ' · владелец' : ''}` : '';
  const tgLink = document.getElementById('side-tg-link');
  if (tgLink && ME) {
    tgLink.textContent = ME.tg_id
      ? `✅ Telegram: @${ME.tg_username || ME.tg_id}`
      : '🔗 Привязать Telegram';
    tgLink.title = ME.tg_id
      ? 'Предпросмотр рассылок приходит сюда'
      : 'Нужно для входа через Telegram и предпросмотра рассылок';
  }
}

// первая доступная страница — на неё уводим, если на дашборд прав нет
function firstAllowedPage() {
  if (can('analytics')) return 'dashboard';
  for (const [page, feature] of Object.entries(PAGE_PERM)) {
    if (can(feature)) return page;
  }
  return ME && ME.role === 'owner' ? 'team' : null;
}

async function changeMyPassword() {
  const oldPw = prompt('Текущий пароль:');
  if (!oldPw) return;
  const newPw = prompt('Новый пароль (минимум 6 символов):');
  if (!newPw) return;
  try {
    await api('/auth/password', { method: 'POST', body: { old_password: oldPw, new_password: newPw } });
    alert('Пароль изменён');
  } catch (e) { /* ошибка уже показана */ }
}
function logout() { localStorage.removeItem('sb_token'); TOKEN = ''; showLogin(); }
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !document.getElementById('login-screen').classList.contains('hidden')) doLogin();
});

// ---------- навигация ----------
// вызовы ленивые: часть функций объявлена в других файлах (dashboard.js, bot.js),
// которые подключаются после app.js — прямые ссылки давали бы ReferenceError
const loaders = {
  dashboard: () => loadDashboard(),
  bots: () => loadBots(),
  funnels: () => loadFunnels(),
  subscribers: async () => { await loadTags(true); await loadBotFilter(); await loadSubscribers(); },
  tags: () => loadTagsPage(),
  broadcasts: () => loadBroadcasts(),
  sheets: () => loadSheetsPage(),
  analysis: () => loadAnalysisPage(),
  team: () => loadUsers(),
  logs: () => loadLogs(),
  ai: () => loadAIPage(),
};
function go(page) {
  const feature = PAGE_PERM[page];
  if (feature && !can(feature)) {
    alert('У вас нет доступа к этому разделу. Права выдаёт владелец аккаунта.');
    return;
  }
  if (page === 'team' && !(ME && ME.role === 'owner')) return;
  if (page !== 'logs' && typeof closeLogs === 'function') closeLogs();
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.getElementById('page-' + page).classList.remove('hidden');
  document.querySelectorAll('.nav-item').forEach(n =>
    n.classList.toggle('active', n.dataset.page === page));
  // раздел живёт в адресе: F5 и кнопка «назад» возвращают куда надо
  if (location.hash.slice(1) !== page) {
    try { history.replaceState(null, '', '#' + page); } catch (e) {}
  }
  (loaders[page] || (() => {}))();
}

window.addEventListener('hashchange', () => {
  const p = location.hash.slice(1);
  if (!ME || !p || !loaders[p]) return;
  const el = document.getElementById('page-' + p);
  if (el && el.classList.contains('hidden')) {
    const feature = PAGE_PERM[p];
    if (feature && !can(feature)) return;
    if (p === 'team' && ME.role !== 'owner') return;
    go(p);
  }
});

// дашборд живёт в dashboard.js (loadDashboard)

// ---------- боты ----------
let BOTS = [];
async function loadBots() {
  BOTS = await api('/bots');
  document.getElementById('bots-list').innerHTML = BOTS.length ? `<table>
    <tr><th>Название</th><th>Токен</th><th>Статус</th><th>Подписчиков</th><th>Воронок</th><th></th></tr>
    ${BOTS.map(b => `<tr>
      <td><a href="#" onclick="openBot(${b.id});return false"><b>${esc(b.name)}</b></a>${b.tg_username ? ` <span style="color:#7a8499">@${esc(b.tg_username)}</span>` : ''}</td>
      <td style="font-family:monospace;font-size:12px">${esc(b.token_hint)}</td>
      <td>${b.is_active
        ? (b.running ? '<span class="status-active">работает</span>' : `<span style="color:#d33" title="${esc(b.last_error||'')}">ошибка</span>`)
        : '<span class="status-off">выключен</span>'}</td>
      <td>${b.subscribers}</td>
      <td>${b.funnels}</td>
      <td>
        <button class="btn primary" onclick="openBot(${b.id})">Открыть</button>
        ${can('bots', 'edit') ? `
        <button class="btn" onclick="toggleBot(${b.id})">${b.is_active ? 'Выключить' : 'Включить'}</button>` : ''}
        ${can('bots', 'edit') && canDelete() ? `
        <button class="btn danger" onclick="deleteBot(${b.id})">Удалить</button>` : ''}
      </td>
    </tr>`).join('')}
  </table>` : '<div class="panel">Ботов пока нет. Создайте первого — токен возьмите у @BotFather.</div>';
  if (BOTS.some(b => b.is_active && !b.running)) {
    clearTimeout(window._botTimer);
    window._botTimer = setTimeout(() => {
      if (!document.getElementById('page-bots').classList.contains('hidden')) loadBots();
    }, 3000);
  }
}
function showBotForm() { document.getElementById('bot-form').classList.remove('hidden'); }
function hideBotForm() { document.getElementById('bot-form').classList.add('hidden'); }
async function createBot() {
  const name = document.getElementById('bot-name').value.trim();
  const token = document.getElementById('bot-token').value.trim();
  if (!token) { alert('Вставьте токен'); return; }
  await api('/bots', { method: 'POST', body: { name, token } });
  document.getElementById('bot-name').value = '';
  document.getElementById('bot-token').value = '';
  hideBotForm();
  loadBots();
}
async function toggleBot(id) {
  try { await api(`/bots/${id}/toggle`, { method: 'POST' }); }
  finally { loadBots(); }
}
async function editBotToken(id) {
  const token = prompt('Новый токен бота (от @BotFather):');
  if (!token) return;
  const b = BOTS.find(x => x.id === id);
  await api(`/bots/${id}`, { method: 'PUT', body: { name: b ? b.name : '', token } });
  loadBots();
}
async function checkBot(id) {
  const r = await api(`/bots/${id}/check`);
  if (!r.ok) { alert('Ошибка связи с Telegram:\n' + r.error); return; }
  alert(
    `@${r.username}\n` +
    `Запущен в приложении: ${r.running ? 'да' : 'нет'}\n` +
    `Вебхук: ${r.webhook_url || 'нет'}\n` +
    `Ожидает апдейтов: ${r.pending_updates}\n` +
    (r.webhook_last_error ? `Ошибка вебхука: ${r.webhook_last_error}\n` : '') +
    `\n${r.hint}`
  );
}
async function deleteBot(id) {
  if (!confirm('Удалить бота? Его подписчики и рассылки тоже удалятся.')) return;
  await api('/bots/' + id, { method: 'DELETE' });
  loadBots();
}
async function loadBotFilter() {
  const bots = await api('/bots');
  const sel = document.getElementById('sub-bot-filter');
  const cur = sel.value;
  sel.innerHTML = '<option value="">Все боты</option>' +
    bots.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('');
  sel.value = cur;
}

// ---------- логи (стрим по сокету) ----------
let LOG_WS = null;
let LOG_BUFFER = [];
let _logsTimer = null;

function logsDebounce() {          // фильтр применяем локально, без запросов
  clearTimeout(_logsTimer);
  _logsTimer = setTimeout(renderLogs, 200);
}

function loadLogs() {
  connectLogs();
}

function connectLogs() {
  closeLogs();
  LOG_BUFFER = [];
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const onlyErrors = document.getElementById('log-errors').checked;
  const url = `${proto}://${location.host}/api/logs/ws?token=${encodeURIComponent(TOKEN)}&only_errors=${onlyErrors}`;
  const view = document.getElementById('log-view');
  view.textContent = 'подключение…';

  const ws = new WebSocket(url);
  LOG_WS = ws;
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.error) { view.textContent = msg.error; return; }
    LOG_BUFFER.push(...msg.lines);
    if (LOG_BUFFER.length > 5000) LOG_BUFFER = LOG_BUFFER.slice(-5000);
    renderLogs();
  };
  ws.onclose = () => {
    // переподключаемся, пока раздел открыт
    if (LOG_WS === ws && !document.getElementById('page-logs').classList.contains('hidden')) {
      setTimeout(connectLogs, 3000);
    }
  };
}

function closeLogs() {
  if (LOG_WS) { const w = LOG_WS; LOG_WS = null; try { w.close(); } catch (e) {} }
}

function renderLogs() {
  const view = document.getElementById('log-view');
  const q = document.getElementById('log-search').value.trim().toLowerCase();
  const limit = +document.getElementById('log-lines').value;
  let rows = LOG_BUFFER;
  if (q) rows = rows.filter(l => l.toLowerCase().includes(q));
  rows = rows.slice(-limit);
  const atBottom = view.scrollTop + view.clientHeight >= view.scrollHeight - 60;
  view.innerHTML = rows.length
    ? rows.map(l => {
        const cls = /\| ERROR|\| CRITICAL|Traceback/.test(l) ? 'log-err'
          : /\| WARNING/.test(l) ? 'log-warn' : '';
        return `<span class="${cls}">${esc(l)}</span>`;
      }).join('\n')
    : 'пока пусто — новые события появятся здесь автоматически';
  document.getElementById('log-meta').textContent =
    `${rows.length} строк · живой поток${LOG_WS && LOG_WS.readyState === 1 ? ' 🟢' : ' 🔴'}`;
  if (atBottom) view.scrollTop = view.scrollHeight;
}

// ---------- команда ----------
let EDIT_USER_ID = null;

let PERM_FEATURES = [];

async function saveAuthBot() {
  const v = document.getElementById('auth-bot').value;
  try {
    const r = await api('/auth/telegram/settings', { method: 'PUT',
      body: { bot_id: v ? +v : null } });
    document.getElementById('auth-bot-hint').textContent = r.bot_username
      ? `@${r.bot_username} — этому боту и привязывайте домен` : 'Вход через Telegram выключен';
    alert(r.bot_username
      ? `Сохранено ✅\n\nБот: @${r.bot_username}\nТеперь у @BotFather: /setdomain → этот бот → funnels.win`
      : 'Сохранено ✅ Вход через Telegram выключен.');
  } catch (e) { /* alert показан */ }
}

async function loadUsers() {
  const [users, bots, feats, funnels] = await Promise.all([
    api('/users'), api('/bots'), api('/permissions/features'), api('/funnels'),
  ]);
  try {
    const auth = await api('/auth/telegram/settings');
    const sel = document.getElementById('auth-bot');
    sel.innerHTML = '<option value="">— вход только по паролю —</option>' +
      bots.map(b => `<option value="${b.id}" ${String(auth.bot_id) === String(b.id) ? 'selected' : ''}>${esc(b.name)}${b.tg_username ? ' (@' + esc(b.tg_username) + ')' : ''}</option>`).join('');
    document.getElementById('auth-bot-hint').textContent = auth.bot_username
      ? `Домен привязывайте к @${auth.bot_username}` : '';
  } catch (e) { /* не владелец — блока нет */ }
  window._allBots = bots;
  window._allFunnels = funnels;
  PERM_FEATURES = feats;
  document.getElementById('users-list').innerHTML = `<table>
    <tr><th>Логин</th><th>Имя</th><th>Роль</th><th>Телеграм</th><th>Боты</th><th>Доступ к разделам</th><th>Статус</th><th>Последний вход</th><th></th></tr>
    ${users.map(u => `<tr>
      <td><b>${esc(u.login)}</b></td>
      <td>${esc(u.name || '—')}</td>
      <td>${u.role === 'owner' ? 'Владелец' : 'Сотрудник'}</td>
      <td>${u.tg_id ? `<span class="pill">@${esc(u.tg_username || u.tg_id)}</span>` : '<span style="color:#7a8499;font-size:12px">не привязан</span>'}</td>
      <td>${(u.bot_ids && u.bot_ids.length)
        ? u.bot_ids.map(id => { const b = bots.find(x => x.id === id); return `<span class="pill">${esc(b ? b.name : id)}</span>`; }).join('')
        : '<span style="color:#7a8499;font-size:12px">все</span>'}</td>
      <td>${permSummary(u)}</td>
      <td>${u.is_active ? '<span class="status-active">активен</span>' : '<span class="status-off">отключён</span>'}</td>
      <td>${u.last_login_at ? new Date(u.last_login_at + 'Z').toLocaleString('ru') : '—'}</td>
      <td>
        <button class="btn" onclick='editUser(${JSON.stringify(u)})'>Изменить</button>
        <button class="btn danger" onclick="deleteUser(${u.id})">Удалить</button>
      </td>
    </tr>`).join('')}
  </table>`;
}

function permSummary(u) {
  if (u.role === 'owner') return '<span class="pill">всё</span>';
  const p = u.permissions || {};
  const on = PERM_FEATURES.filter(f => p[f.key] && p[f.key] !== 'none');
  if (!on.length) return '<span style="color:#d33;font-size:12px">нет доступа</span>';
  return on.map(f => f.toggle
    ? `<span class="pill danger-pill" title="может удалять">${esc(f.label)}</span>`
    : `<span class="pill ${p[f.key] === 'edit' ? '' : 'gray'}" title="${p[f.key] === 'edit' ? 'может изменять' : 'только смотреть'}">${esc(f.label)}${p[f.key] === 'edit' ? '' : ' 👁'}</span>`
  ).join('');
}

function showUserForm() {
  EDIT_USER_ID = null;
  document.getElementById('u-login').value = '';
  document.getElementById('u-login').disabled = false;
  document.getElementById('u-name').value = '';
  document.getElementById('u-pass').value = '';
  document.getElementById('u-tgid').value = '';
  document.getElementById('u-role').value = 'staff';
  renderUserBots([]);
  renderUserFunnels([]);
  renderPermMatrix({});
  updateRoleHint();
  document.getElementById('user-form').classList.remove('hidden');
}

// матрица «раздел × уровень»
function renderPermMatrix(perms) {
  const box = document.getElementById('u-perms');
  if (!box) return;
  box.innerHTML = PERM_FEATURES.map(f => {
    const cur = perms[f.key] || 'none';
    const levels = f.toggle
      ? [['none', 'запрещено'], ['edit', 'разрешено']]
      : f.view_only
        ? [['none', 'нет'], ['view', 'смотреть']]
        : [['none', 'нет'], ['view', 'смотреть'], ['edit', 'изменять']];
    return `<div class="perm-row">
      <div class="perm-name"><b>${esc(f.label)}</b><span>${esc(f.hint || '')}</span></div>
      <div class="perm-levels" data-key="${f.key}">
        ${levels.map(([v, l]) =>
          `<label class="perm-opt${cur === v ? ' on' : ''}">
             <input type="radio" name="perm-${f.key}" value="${v}"${cur === v ? ' checked' : ''}
               onchange="this.closest('.perm-levels').querySelectorAll('.perm-opt').forEach(o=>o.classList.remove('on'));this.closest('.perm-opt').classList.add('on')">
             ${l}
           </label>`).join('')}
      </div>
    </div>`;
  }).join('');
}

function collectPerms() {
  const out = {};
  document.querySelectorAll('#u-perms .perm-levels').forEach(row => {
    const checked = row.querySelector('input:checked');
    if (checked && checked.value !== 'none') out[row.dataset.key] = checked.value;
  });
  return out;
}

// у владельца прав не спрашиваем — ему доступно всё
function updateRoleHint() {
  const isOwner = document.getElementById('u-role').value === 'owner';
  document.getElementById('u-perms-wrap').classList.toggle('hidden', isOwner);
  document.getElementById('u-bots-wrap').classList.toggle('hidden', isOwner);
  document.getElementById('u-funnels-wrap').classList.toggle('hidden', isOwner);
  const hint = document.getElementById('u-owner-hint');
  if (hint) hint.classList.toggle('hidden', !isOwner);
}

function permPreset(kind) {
  const p = {};
  PERM_FEATURES.forEach(f => {
    if (kind === 'none') return;
    if (f.toggle) { if (kind === 'all') p[f.key] = 'edit'; return; }  // удаление — только вручную
    if (kind === 'view') p[f.key] = 'view';
    if (kind === 'all') p[f.key] = f.view_only ? 'view' : 'edit';
    if (kind === 'marketer') {
      if (['funnels', 'broadcasts', 'tags', 'subscribers', 'chat'].includes(f.key)) p[f.key] = 'edit';
      else if (['analytics', 'bots'].includes(f.key)) p[f.key] = 'view';
    }
    if (kind === 'support') {
      if (['chat', 'subscribers'].includes(f.key)) p[f.key] = 'edit';
      else if (f.key === 'tags') p[f.key] = 'view';
    }
  });
  renderPermMatrix(p);
}
function hideUserForm() { document.getElementById('user-form').classList.add('hidden'); }

function renderUserFunnels(selected) {
  const box = document.getElementById('u-funnels');
  if (!box) return;
  const list = window._allFunnels || [];
  box.innerHTML = list.length
    ? list.map(f => `<span class="pill gray ${selected.includes(f.id) ? 'on' : ''}" data-id="${f.id}" onclick="this.classList.toggle('on')">${esc(f.name)}</span>`).join('')
    : '<span style="color:#99a;font-size:12px">воронок пока нет</span>';
}

function renderUserBots(selected) {
  const bots = window._allBots || [];
  document.getElementById('u-bots').innerHTML = bots.length
    ? bots.map(b => `<span class="pill gray ${selected.includes(b.id) ? 'on' : ''}" data-id="${b.id}" onclick="this.classList.toggle('on')">${esc(b.name)}</span>`).join('')
    : '<span style="color:#99a;font-size:12px">ботов пока нет</span>';
}

function editUser(u) {
  EDIT_USER_ID = u.id;
  document.getElementById('u-login').value = u.login;
  document.getElementById('u-login').disabled = true;
  document.getElementById('u-name').value = u.name || '';
  document.getElementById('u-pass').value = '';
  document.getElementById('u-pass').placeholder = 'оставь пустым, чтобы не менять';
  document.getElementById('u-tgid').value = u.tg_id || '';
  document.getElementById('u-role').value = u.role;
  renderUserBots(u.bot_ids || []);
  renderUserFunnels(u.funnel_ids || []);
  renderPermMatrix(u.role === 'owner' ? {} : (u.permissions || {}));
  updateRoleHint();
  document.getElementById('user-form').classList.remove('hidden');
}

async function saveUser() {
  const body = {
    login: document.getElementById('u-login').value.trim(),
    name: document.getElementById('u-name').value.trim(),
    password: document.getElementById('u-pass').value || null,
    role: document.getElementById('u-role').value,
    bot_ids: [...document.querySelectorAll('#u-bots .pill.on')].map(p => +p.dataset.id),
    funnel_ids: [...document.querySelectorAll('#u-funnels .pill.on')].map(p => +p.dataset.id),
    permissions: collectPerms(),
    tg_id: +document.getElementById('u-tgid').value || null,
    is_active: true,
  };
  if (body.role !== 'owner' && !Object.keys(body.permissions).length &&
      !confirm('Ни один раздел не отмечен — человек не сможет ничего открыть. Сохранить всё равно?')) return;
  if (EDIT_USER_ID) await api('/users/' + EDIT_USER_ID, { method: 'PUT', body });
  else await api('/users', { method: 'POST', body });
  hideUserForm();
  loadUsers();
}

async function deleteUser(id) {
  if (!confirm('Удалить пользователя?')) return;
  await api('/users/' + id, { method: 'DELETE' });
  loadUsers();
}

// ---------- теги ----------
async function loadTags(fillFilters = false) {
  TAGS = await api('/tags');
}
async function loadTagsPage() {
  await loadTags();
  document.getElementById('tags-list').innerHTML = `<table>
    <tr><th>Тег</th><th>Подписчиков</th><th></th></tr>
    ${TAGS.map(t => `<tr><td>${esc(t.name)}</td><td>${t.count}</td>
      <td>${can('tags', 'edit') && canDelete() ? `<button class="btn danger" onclick="deleteTag(${t.id})">Удалить</button>` : ''}</td></tr>`).join('')}
  </table>`;
}
async function createTag() {
  const name = document.getElementById('new-tag-name').value.trim();
  if (!name) return;
  await api('/tags', { method: 'POST', body: { name } });
  document.getElementById('new-tag-name').value = '';
  loadTagsPage();
}
async function deleteTag(id) {
  if (!confirm('Удалить тег?')) return;
  await api('/tags/' + id, { method: 'DELETE' });
  loadTagsPage();
}

// ---------- подписчики + сегмент ----------
let SEG_BUILDER = null;
let SEG_APPLIED = null;  // применённый фильтр сегмента (или null)

let _subsTimer = null;
function subsDebounce() {           // поиск не дёргает сервер на каждую букву
  clearTimeout(_subsTimer);
  _subsTimer = setTimeout(loadSubscribers, 300);
}


// Три разных состояния, которые легко перепутать: «заблокировал бота» —
// написать ему уже нельзя; «отписался» — диалог жив, но рассылки не идут.
function subStatusHtml(s) {
  if (!s.is_active) return '<span class="status-off">блок</span>';
  if (s.is_subscribed === false) return '<span class="status-off">отписался</span>';
  return '<span class="status-active">активен</span>';
}

function currentSubFilter() {
  const botId = +document.getElementById('sub-bot-filter').value || null;
  const search = document.getElementById('sub-search').value.trim();
  let filter = SEG_APPLIED ? JSON.parse(JSON.stringify(SEG_APPLIED)) : { match: 'all', conditions: [] };
  // строка поиска добавляется как доп. условие «имя содержит» (И)
  if (search) {
    filter = { match: 'all', active_24h: filter.active_24h,
      conditions: [...(filter.conditions || []), { field: 'name', op: 'contains', value: search }] };
  }
  return { bot_id: botId, filter };
}

async function loadSubscribers() {
  let res;
  try {
    const body = currentSubFilter();
    res = await api('/subscribers/search', { method: 'POST', body: { ...body, limit: 500 } });
  } catch (e) {
    document.getElementById('subscribers-list').innerHTML =
      `<div class="panel" style="color:#d33">Не удалось загрузить подписчиков: ${esc(e.message || e)}. Обновите страницу (Cmd+Shift+R).</div>`;
    return;
  }
  const subs = res.subscribers;
  LAST_AUDIENCE_TOTAL = res.total;
  document.getElementById('subscribers-list').innerHTML = `
    <div class="list-meta">
      Найдено: <b>${res.total}</b>${res.total > subs.length ? ` (показаны первые ${subs.length})` : ''}
      ${res.total && can('subscribers', 'edit') ? `
      <span class="bulk-actions">
        С этой аудиторией (${res.total}):
        <select id="bulk-tag-select">
          ${TAGS.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}
        </select>
        <button class="btn" onclick="bulkAction('add_tag')">+ тег</button>
        <button class="btn" onclick="bulkAction('remove_tag')">− тег</button>
        ${canDelete() ? `<button class="btn danger" onclick="bulkAction('delete')">🗑 Удалить</button>` : ''}
      </span>` : ''}
    </div>
    <table>
    <tr><th>Имя</th><th>Username</th><th>Язык</th><th>Активность</th><th>Статус</th><th>Теги</th><th></th></tr>
    ${subs.map(s => `<tr>
      <td><a href="#" onclick="openChat(${s.id});return false"><b>${esc(s.first_name || '')} ${esc(s.last_name || '')}</b></a></td>
      <td>${s.username ? '@' + esc(s.username) : '—'}</td>
      <td>${esc(s.language_code || '—')}</td>
      <td>${s.last_active_at ? new Date(s.last_active_at + 'Z').toLocaleDateString('ru') : '—'}</td>
      <td>${subStatusHtml(s)}</td>
      <td>${s.tags.map(t => `<span class="pill">${esc(t.name)}${can('subscribers', 'edit')
        ? `<span class="x" onclick="removeSubTag(${s.id},${t.id})">✕</span>` : ''}</span>`).join('')}</td>
      <td>${can('subscribers', 'edit') ? `<select onchange="addSubTag(${s.id}, this.value); this.value=''">
        <option value="">+ тег</option>
        ${TAGS.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}
      </select>` : ''}</td>
    </tr>`).join('')}
  </table>`;
}

async function toggleSegPanel() {
  const panel = document.getElementById('seg-panel');
  const willShow = panel.classList.contains('hidden');
  panel.classList.toggle('hidden');
  if (willShow && !SEG_BUILDER) {
    await loadSegFields();
    SEG_BUILDER = makeSegment(document.getElementById('seg-builder'));
  }
}
async function applySegment() {
  SEG_APPLIED = SEG_BUILDER.getFilter();
  await loadSubscribers();
  // показать точное число совпадений сегмента (без учёта строки поиска)
  const botId = +document.getElementById('sub-bot-filter').value || null;
  const r = await api('/subscribers/search', { method: 'POST', body: { bot_id: botId, filter: SEG_APPLIED, count_only: true } });
  document.getElementById('seg-count').textContent = `Подходит под сегмент: ${r.total}`;
}
function resetSegment() {
  if (SEG_BUILDER) SEG_BUILDER.reset();
  SEG_APPLIED = null;
  document.getElementById('seg-count').textContent = '';
  loadSubscribers();
}
async function addSubTag(subId, tagId) {
  if (!tagId) return;
  await api(`/subscribers/${subId}/tags`, { method: 'POST', body: { tag_id: +tagId } });
  loadSubscribers();
}
async function removeSubTag(subId, tagId) {
  await api(`/subscribers/${subId}/tags/${tagId}`, { method: 'DELETE' });
  loadSubscribers();
}

let LAST_AUDIENCE_TOTAL = null;

// массовое действие над текущей аудиторией (фильтр сегмента + строка поиска)
async function bulkAction(action) {
  const total = LAST_AUDIENCE_TOTAL || 0;
  if (!total) return;
  const tagSel = document.getElementById('bulk-tag-select');
  const tagId = tagSel ? +tagSel.value : null;
  const tagName = tagSel ? tagSel.options[tagSel.selectedIndex]?.text : '';

  const label = {
    delete: `УДАЛИТЬ ${total} подписчиков?\n\nУдалится вся история переписки, теги и статистика. Отменить будет нельзя.`,
    add_tag: `Добавить тег «${tagName}» ${total} подписчикам?`,
    remove_tag: `Снять тег «${tagName}» у ${total} подписчиков?`,
  }[action];
  if (!confirm(label)) return;
  if (action === 'delete' && total >= 100) {
    const typed = prompt(`Это ${total} человек. Чтобы подтвердить, введите число ${total}:`);
    if (typed !== String(total)) { alert('Не совпало — отменено.'); return; }
  }

  try {
    const body = { ...currentSubFilter(), action, tag_id: tagId, expected_total: total };
    const r = await api('/subscribers/bulk', { method: 'POST', body });
    alert(`Готово. Затронуто: ${r.affected}`);
  } catch (e) { /* alert показан в api() */ }
  loadSubscribers();
}

// удалить одного подписчика (кнопка в чате)
async function deleteSubscriber(subId) {
  if (!confirm('Удалить этого подписчика?\n\nПереписка, теги и статистика удалятся. Если он снова напишет боту — появится как новый.')) return;
  try { await api(`/subscribers/${subId}`, { method: 'DELETE' }); }
  catch (e) { return; }
  closeChat();
  loadSubscribers();
}

// ---------- воронки: список ----------
const TRIGGER_LABEL = { start: '/start', keyword: 'слово', tag_added: 'тег', message: 'сообщение' };
async function loadFunnels() {
  const all = await api('/funnels');
  const funnels = all.filter(f => !f.is_chain);
  const chains = all.filter(f => f.is_chain);
  document.getElementById('funnels-list').innerHTML =
    funnelsTable(funnels, false) + (chains.length ? funnelsTable(chains, true) : '');
}

// Цепочки живут в том же списке, но отдельной таблицей: у них нет ни
// триггера, ни собственных ботов, и включать их нечего — колонки, которые
// в их строках всегда пустые, только путали бы.
function funnelsTable(list, isChain) {
  if (!list.length) {
    return isChain ? '' : '<div class="panel">Пока нет воронок — создайте первую.</div>';
  }
  const head = isChain
    ? `<h3 class="list-title">⛓ Цепочки</h3>
       <p class="list-note">Кусок воронки, вынесенный отдельно. Одну цепочку можно
       вызвать из нескольких воронок и править в одном месте.</p>
       <table><tr><th>Название</th><th>Где вызывается</th><th>Запусков</th><th></th></tr>`
    : `<table><tr><th>Название</th><th>Триггер</th><th>Боты</th><th>Статус</th><th>Запусков</th><th></th></tr>`;
  return head + list.map(f => `<tr>
      <td><a href="#" onclick="openEditor(${f.id});return false"><b>${esc(f.name)}</b></a></td>
      ${isChain
        ? `<td>${(f.used_by && f.used_by.length)
             ? f.used_by.map(n => `<span class="pill">${esc(n)}</span>`).join('')
             : '<span style="color:#c33;font-size:12px">никем — цепочка не работает</span>'}</td>`
        : `<td>${TRIGGER_LABEL[f.trigger_type] || f.trigger_type}${f.trigger_value ? ': ' + esc(f.trigger_value) : ''}</td>
           <td>${(f.bots && f.bots.length) ? f.bots.map(b => `<span class="pill">${esc(b)}</span>`).join('') : '<span style="color:#c33;font-size:12px">не назначена</span>'}</td>
           <td>${f.is_active ? '<span class="status-active">включена</span>' : '<span class="status-off">выключена</span>'}</td>`}
      <td>${f.runs}</td>
      <td>
        ${!isChain && can('funnels', 'edit') ? `<button class="btn" onclick="toggleFunnel(${f.id})">${f.is_active ? 'Выключить' : 'Включить'}</button>` : ''}
        ${can('funnels', 'edit') && canDelete() ? `<button class="btn danger" onclick="deleteFunnel(${f.id})">Удалить</button>` : ''}
      </td>
    </tr>`).join('') + '</table>';
}

async function createFunnel() {
  const r = await api('/funnels', { method: 'POST', body: { name: 'Новая воронка', graph_ui: {} } });
  openEditor(r.id);
}
async function createChain() {
  const r = await api('/funnels', { method: 'POST', body: { name: 'Новая цепочка', graph_ui: {}, is_chain: true } });
  openEditor(r.id);
}
async function toggleFunnel(id) { await api(`/funnels/${id}/toggle`, { method: 'POST' }); loadFunnels(); }
async function deleteFunnel(id) {
  if (!confirm('Удалить? Отменить это будет нельзя.')) return;
  await api('/funnels/' + id, { method: 'DELETE' });
  loadFunnels();
}

// ---------- рассылки ----------
let BC_SEG = null;
let BC_MEDIA = null;
let BC_TEXT = null;
async function showBroadcastForm() {
  await loadTags();
  await loadSegFields();
  const bots = await api('/bots');
  const botSel = document.getElementById('bc-bot');
  botSel.innerHTML = bots.length
    ? bots.map(b => `<option value="${b.id}">${esc(b.name)} (${b.subscribers} подписчиков)</option>`).join('')
    : '<option value="">нет ботов</option>';
  BC_SEG = makeSegment(document.getElementById('bc-segment'));
  BC_MEDIA = mountMediaUploader(document.getElementById('bc-media'), []);
  BC_TEXT = mountRichText(document.getElementById('bc-text-rt'), '');
  document.getElementById('bc-buttons').innerHTML = '';
  document.getElementById('bc-order').value = '';
  document.getElementById('bc-count').textContent = '';
  document.getElementById('broadcast-form').classList.remove('hidden');
}
function hideBroadcastForm() { document.getElementById('broadcast-form').classList.add('hidden'); }

// ---------- кнопки рассылки ----------
// Два вида: ссылка и «повесить тег». Ветвление, как в воронке, тут
// невозможно — у рассылки нет узлов, некуда вести.
const BC_BTN_STYLES = [
  ['', 'обычная'], ['primary', '🔵 основная'],
  ['success', '🟢 зелёная'], ['danger', '🔴 красная'],
];

function bcButtonRow(b = {}) {
  const isTag = !b.url && (b.tag_id || b.tag_id === 0);
  return `<div class="bc-btn-row">
    <div class="row1">
      <input class="bc-label inline-input" placeholder="Текст кнопки" value="${esc(b.label || '')}">
      <select class="bc-style inline-input" style="flex:0 0 130px">
        ${BC_BTN_STYLES.map(([v, l]) =>
          `<option value="${v}" ${(b.style || '') === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select>
      <button class="btn danger" type="button" title="убрать"
        onclick="this.closest('.bc-btn-row').remove()">✕</button>
    </div>
    <div class="row2">
      <select class="bc-kind inline-input" onchange="bcButtonKind(this)">
        <option value="url" ${isTag ? '' : 'selected'}>ссылка</option>
        <option value="tag" ${isTag ? 'selected' : ''}>повесить тег</option>
      </select>
      <input class="bc-action bc-url inline-input ${isTag ? 'hidden' : ''}"
        placeholder="https://…" value="${esc(b.url || '')}">
      <select class="bc-action bc-tag inline-input ${isTag ? '' : 'hidden'}">
        ${TAGS.map(t => `<option value="${t.id}" ${String(b.tag_id) === String(t.id) ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}
      </select>
      <input class="bc-reply inline-input ${isTag ? '' : 'hidden'}" style="flex:1"
        placeholder="ответ во всплывашке (необязательно)" value="${esc(b.reply || '')}">
    </div>
  </div>`;
}

function bcButtonKind(sel) {
  const row = sel.closest('.bc-btn-row');
  const tag = sel.value === 'tag';
  row.querySelector('.bc-url').classList.toggle('hidden', tag);
  row.querySelector('.bc-tag').classList.toggle('hidden', !tag);
  row.querySelector('.bc-reply').classList.toggle('hidden', !tag);
}

function bcAddButton() {
  if (!TAGS.length) { /* теги подтянутся при открытии формы */ }
  document.getElementById('bc-buttons')
    .insertAdjacentHTML('beforeend', bcButtonRow());
}

function collectBcButtons() {
  return [...document.querySelectorAll('#bc-buttons .bc-btn-row')].map(row => {
    const b = { label: row.querySelector('.bc-label').value.trim() };
    const style = row.querySelector('.bc-style').value;
    if (style) b.style = style;
    if (row.querySelector('.bc-kind').value === 'tag') {
      const tag = row.querySelector('.bc-tag').value;
      if (tag) b.tag_id = +tag;
      const reply = row.querySelector('.bc-reply').value.trim();
      if (reply) b.reply = reply;
    } else {
      b.url = row.querySelector('.bc-url').value.trim();
    }
    return b;
  }).filter(b => b.label && (b.url || b.tag_id));
}
async function countBroadcast() {
  const botId = +document.getElementById('bc-bot').value;
  if (!botId) { alert('Выберите бота'); return; }
  const r = await api('/subscribers/search', { method: 'POST',
    body: { bot_id: botId, filter: BC_SEG.getFilter(), count_only: true, deliverable: true } });
  document.getElementById('bc-count').textContent = `Получателей: ${r.total}`;
}
// Предпросмотр: то же сообщение, но только себе. Заодно самая честная
// проверка живости — если дошло, значит бот отвечает и разметка не сломана.
async function previewBroadcast() {
  const botId = +document.getElementById('bc-bot').value;
  if (!botId) { alert('Выберите бота'); return; }
  const btn = document.getElementById('bc-preview-btn');
  btn.disabled = true;
  try {
    await api('/broadcasts/preview', { method: 'POST', body: {
      bot_id: botId,
      text: BC_TEXT ? BC_TEXT.getHtml() : '',
      media: BC_MEDIA ? BC_MEDIA.getItems() : [],
      buttons: collectBcButtons(),
      text_first: document.getElementById('bc-order').value === '1',
    }});
    alert('Отправлено вам в Telegram — проверьте, как выглядит.\n\nКнопки в предпросмотре ничего не делают: теги вешаются только в настоящей рассылке.');
  } catch (e) { /* alert показан в api() */ }
  finally { btn.disabled = false; }
}

async function sendBroadcast() {
  const botId = +document.getElementById('bc-bot').value;
  if (!botId) { alert('Выберите бота'); return; }
  const body = {
    bot_id: botId,
    name: document.getElementById('bc-name').value || 'Рассылка',
    text: BC_TEXT ? BC_TEXT.getHtml() : '',
    media: BC_MEDIA ? BC_MEDIA.getItems() : [],
    buttons: collectBcButtons(),
    text_first: document.getElementById('bc-order').value === '1',
    segment: BC_SEG.getFilter(),
  };
  if (!body.text.trim() && !body.media.length && !body.buttons.length) {
    alert('Добавьте текст, вложение или кнопку'); return;
  }
  // перед стартом показываем, скольким людям уйдёт — запуск вслепую опасен
  let total = null;
  try {
    const r = await api('/subscribers/search', { method: 'POST',
      body: { bot_id: botId, filter: body.segment, count_only: true, deliverable: true } });
    total = r.total;
  } catch (e) { /* не посчиталось — спросим без числа */ }
  if (total === 0) { alert('Под выбранный сегмент не попадает ни один подписчик.'); return; }
  if (!confirm(total != null ? `Запустить рассылку? Получателей: ${total}` : 'Запустить рассылку?')) return;
  await api('/broadcasts', { method: 'POST', body });
  hideBroadcastForm();
  if (BC_TEXT) BC_TEXT.setHtml('');
  loadBroadcasts();
}
const BC_STATUS = { pending: 'в очереди', running: 'отправляется', done: 'завершена', failed: 'ошибка' };
async function loadBroadcasts() {
  const bcs = await api('/broadcasts');
  document.getElementById('broadcasts-list').innerHTML = bcs.length ? `<table>
    <tr><th>Название</th><th>Бот</th><th>Статус</th><th>Прогресс</th><th>Дата</th></tr>
    ${bcs.map(b => {
      const pct = b.total ? Math.round(100 * (b.sent + b.failed) / b.total) : 0;
      return `<tr>
        <td><a href="#" onclick="openBroadcast(${b.id});return false"><b>${esc(b.name)}</b></a><br><span style="color:#7a8499;font-size:12px">${esc(plainText(b.text).slice(0, 60))}${b.text.length > 60 ? '…' : ''}</span></td>
        <td>${esc(b.bot || '—')}</td>
        <td>${BC_STATUS[b.status] || b.status}</td>
        <td>${b.sent}/${b.total}${b.failed ? ` (не дошло: ${b.failed})` : ''}
          <div class="progress"><i style="width:${pct}%"></i></div></td>
        <td>${new Date(b.created_at + 'Z').toLocaleString('ru')}</td>
      </tr>`;
    }).join('')}
  </table>` : '<div class="panel">Рассылок ещё не было.</div>';
  // автообновление, пока есть активные
  if (bcs.some(b => b.status === 'running' || b.status === 'pending')) {
    clearTimeout(window._bcTimer);
    window._bcTimer = setTimeout(() => {
      if (!document.getElementById('page-broadcasts').classList.contains('hidden')) loadBroadcasts();
    }, 3000);
  }
}

// ---------- карточка рассылки ----------
async function openBroadcast(id) {
  let b;
  try { b = await api('/broadcasts/' + id); } catch (e) { return; }

  const media = (b.media || []).length
    ? b.media
    : (b.photo_url ? [{ type: 'photo', path: b.photo_url, name: '' }] : []);

  const pct = b.total ? Math.round(100 * (b.sent + b.failed) / b.total) : 0;
  const rec = b.recipients || [];

  document.getElementById('bc-detail-body').innerHTML = `
    <div class="bc-grid">
      <div class="bc-col">
        <div class="bc-section-title">Что отправляли</div>
        <div class="bc-preview">
          ${media.length ? `<div class="bc-media">${media.map(m => `
            <div class="bc-media-item" title="${esc(m.name || m.path)}">
              ${mediaThumbHtml(m)}
              <span>${esc(m.name || m.path.split('/').pop())}</span>
            </div>`).join('')}</div>` : ''}
          <div class="bc-text">${b.text ? rtPreview(b.text) : '<i style="color:#99a">без текста</i>'}</div>
          ${(b.buttons || []).length ? `<div class="df-btns">${b.buttons.map(x => {
            const st = x.style ? ` st-${x.style}` : '';
            const what = x.url ? '🔗 ' + esc(x.url) : 'тег «' + esc(x.tag_name || x.tag_id) + '»';
            return `<div class="df-btn${st}" title="${esc(what)}">${esc(x.label)}</div>`;
          }).join('')}</div>` : ''}
        </div>
        ${b.text_first ? '<div class="bc-hint">Текст уходил отдельным сообщением перед вложением.</div>' : ''}
        ${media.length > 1 ? '<div class="bc-hint">Несколько фото/видео уходят одним альбомом.</div>' : ''}
      </div>

      <div class="bc-col">
        <div class="bc-section-title">Результат</div>
        <div class="bc-stats">
          <div class="bc-stat"><span>${b.sent}</span><label>отправлено</label></div>
          <div class="bc-stat ${b.failed ? 'bad' : ''}"><span>${b.failed}</span><label>не дошло</label></div>
          <div class="bc-stat"><span>${b.total}</span><label>в аудитории</label></div>
          <div class="bc-stat"><span>${b.total ? Math.round(100 * b.sent / b.total) : 0}%</span><label>доставляемость</label></div>
        </div>
        <div class="progress" style="margin:10px 0"><i style="width:${pct}%"></i></div>

        <div class="bc-section-title">Кому</div>
        <div class="chat-info-row"><span>Бот</span><b>${esc(b.bot)}</b></div>
        <div class="chat-info-row"><span>Отбор</span><b>${esc(b.audience_kind)}</b></div>
        ${b.audience.map(a => `<div class="bc-cond">${esc(a)}</div>`).join('')}
        <div class="chat-info-row" style="margin-top:8px"><span>Статус</span><b>${BC_STATUS[b.status] || b.status}</b></div>
        <div class="chat-info-row"><span>Создана</span><b>${new Date(b.created_at + 'Z').toLocaleString('ru')}</b></div>
      </div>
    </div>

    <div class="bc-section-title" style="margin-top:16px">
      Получатели${rec.length ? ` <span style="font-weight:400;color:#7a8499">— показаны ${rec.length}${b.total > rec.length ? ` из ${b.total}` : ''}</span>` : ''}
    </div>
    ${rec.length ? `<table>
      <tr><th>Имя</th><th>Username</th><th>Доставлено</th><th>Время</th></tr>
      ${rec.map(r => `<tr>
        <td><a href="#" onclick="closeBroadcast();openChat(${r.id});return false">${esc(r.name)}</a></td>
        <td>${r.username ? '@' + esc(r.username) : '—'}</td>
        <td>${r.delivered ? '<span class="status-active">да</span>' : '<span class="status-off">нет</span>'}</td>
        <td>${new Date(r.at + 'Z').toLocaleString('ru')}</td>
      </tr>`).join('')}
    </table>` : '<div class="panel">Пока никому не отправлено.</div>'}`;

  document.getElementById('bc-detail-title').textContent = b.name;
  document.getElementById('bc-overlay').classList.remove('hidden');
  document.getElementById('bc-detail').classList.remove('hidden');
}

function closeBroadcast() {
  document.getElementById('bc-detail').classList.add('hidden');
  document.getElementById('bc-overlay').classList.add('hidden');
}

// ---------- AI ----------
const AI_DEFAULT_MODELS = { anthropic: 'claude-sonnet-4-5', openai: 'gpt-4o' };

async function loadAIPage() {
  const s = await api('/ai/settings');
  document.getElementById('ai-provider').value = s.provider;
  document.getElementById('ai-model').value = s.model || '';
  document.getElementById('ai-key-hint').textContent =
    s.has_key ? `(сохранён: ${s.key_hint})` : '(не задан)';
  loadAIUsage();
}
function aiProviderChanged() {
  document.getElementById('ai-model').value =
    AI_DEFAULT_MODELS[document.getElementById('ai-provider').value] || '';
}
async function saveAISettings() {
  const key = document.getElementById('ai-key').value.trim();
  await api('/ai/settings', { method: 'PUT', body: {
    provider: document.getElementById('ai-provider').value,
    model: document.getElementById('ai-model').value.trim() || null,
    api_key: key || null,
  }});
  document.getElementById('ai-key').value = '';
  alert('Сохранено ✅');
  loadAIPage();
}
async function aiGenerate() {
  const spec = document.getElementById('ai-spec').value.trim();
  if (!spec) { alert('Вставь ТЗ'); return; }
  const btn = document.getElementById('ai-generate-btn');
  const status = document.getElementById('ai-status');
  btn.disabled = true;
  status.textContent = 'Генерирую… (обычно 20–60 сек)';
  try {
    const r = await api('/ai/generate', { method: 'POST', body: { spec_text: spec } });
    status.textContent = `Готово: «${r.name}» (${r.input_tokens}+${r.output_tokens} токенов)`;
    if (confirm(`Воронка «${r.name}» собрана (выключена). Открыть в редакторе?`)) {
      openEditor(r.funnel_id);
    }
  } catch (e) {
    status.textContent = 'Ошибка — детали в алерте';
  } finally {
    btn.disabled = false;
    loadAIUsage();
  }
}
async function aiUploadDocx(input) {
  const file = input.files[0];
  if (!file) return;
  input.value = '';
  const status = document.getElementById('ai-status');
  status.textContent = 'Читаю документ…';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/ai/extract_docx', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + TOKEN },
      body: fd,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || 'Ошибка чтения файла');
    document.getElementById('ai-spec').value = data.text;
    status.textContent = `Загружено: ${file.name} (${data.chars.toLocaleString('ru')} симв.`
      + (data.images ? `, картинок: ${data.images}` : '')
      + `). Проверь текст и жми «Собрать воронку».`;
  } catch (e) {
    status.textContent = '';
    alert(e.message);
  }
}

async function loadAIUsage() {
  const u = await api('/ai/usage');
  document.getElementById('ai-usage').innerHTML = `
    <div class="cards" style="margin-bottom:14px">
      <div class="card"><div class="num">${u.requests}</div><div class="lbl">Запросов</div></div>
      <div class="card"><div class="num">${u.input_tokens.toLocaleString('ru')}</div><div class="lbl">Входных токенов</div></div>
      <div class="card"><div class="num">${u.output_tokens.toLocaleString('ru')}</div><div class="lbl">Выходных токенов</div></div>
    </div>` + (u.recent.length ? `<table>
      <tr><th>Дата</th><th>Модель</th><th>Токены</th><th>Результат</th></tr>
      ${u.recent.map(r => `<tr>
        <td>${new Date(r.created_at + 'Z').toLocaleString('ru')}</td>
        <td>${esc(r.model)}</td>
        <td>${r.input_tokens}+${r.output_tokens}</td>
        <td>${r.status === 'ok'
          ? (r.funnel_id ? `<a href="#" onclick="openEditor(${r.funnel_id});return false">воронка #${r.funnel_id}</a>` : 'ок')
          : `<span style="color:#d33" title="${esc(r.error || '')}">ошибка</span>`}</td>
      </tr>`).join('')}
    </table>` : '');
}

// ---------- живой чат ----------
let CHAT_SUB = null;
let CHAT_TIMER = null;
let CHAT_LAST_COUNT = -1;

async function openChat(subId) {
  CHAT_SUB = subId;
  CHAT_LAST_COUNT = -1;
  CHAT_MEDIA = [];
  renderChatAttachments();
  setupChatDropzone();
  if (!TAGS.length) await loadTags();
  document.getElementById('chat-overlay').classList.remove('hidden');
  document.getElementById('chat-drawer').classList.remove('hidden');
  // без права «изменять» переписку можно только читать
  const rw = can('chat', 'edit');
  document.querySelector('.chat-compose')?.classList.toggle('hidden', !rw);
  document.querySelector('.chat-actions')?.classList.toggle('hidden', !rw);
  document.getElementById('chat-delete-btn')?.classList.toggle('hidden',
    !(can('subscribers', 'edit') && canDelete()));
  await refreshChatInfo();
  await refreshChatMessages();
  clearInterval(CHAT_TIMER);
  CHAT_TIMER = setInterval(() => { refreshChatMessages(); }, 4000);
}
function closeChat() {
  clearInterval(CHAT_TIMER);
  CHAT_SUB = null;
  document.getElementById('chat-overlay').classList.add('hidden');
  document.getElementById('chat-drawer').classList.add('hidden');
  if (!document.getElementById('page-subscribers').classList.contains('hidden')) loadSubscribers();
  if (!document.getElementById('page-bot').classList.contains('hidden') && typeof botLoadSubs === 'function') botLoadSubs();
}

async function refreshChatInfo() {
  const s = await api('/subscribers/' + CHAT_SUB);
  document.getElementById('chat-name').textContent =
    `${s.first_name || ''} ${s.last_name || ''}`.trim() || 'Подписчик';
  document.getElementById('chat-username').textContent = s.username ? '@' + s.username : '';

  const paused = s.paused_until && new Date(s.paused_until + 'Z') > new Date();
  document.getElementById('chat-info').innerHTML = `
    <div class="chat-info-row"><span>Статус</span><b>${
      !s.is_active ? 'Заблокировал бота'
        : s.is_subscribed === false ? 'Отписался от рассылок' : 'Подписан'}</b></div>
    <div class="chat-info-row"><span>Добавлен</span><b>${new Date(s.created_at + 'Z').toLocaleString('ru')}</b></div>
    <div class="chat-info-row"><span>Активность</span><b>${s.last_active_at ? new Date(s.last_active_at + 'Z').toLocaleString('ru') : '—'}</b></div>
    <div class="chat-info-row"><span>Язык</span><b>${esc(s.language_code || '—')}</b></div>
    <div class="chat-info-row"><span>Метка (последняя)</span><b>${esc(s.source || '—')}</b></div>
    ${s.first_source && s.first_source !== s.source
      ? `<div class="chat-info-row"><span>Первая метка</span><b>${esc(s.first_source)}</b></div>` : ''}
    ${Object.entries(s.params || {}).map(([k, v]) =>
      `<div class="chat-info-row"><span>param ${esc(k)}</span><b>${esc(v)}</b></div>`).join('')}
    <div class="chat-info-row"><span>Бот</span><b>${esc(s.bot_name)} ${s.bot_running ? '🟢' : '🔴'}</b></div>`;

  document.getElementById('chat-pause-state').textContent = paused
    ? `на паузе до ${new Date(s.paused_until + 'Z').toLocaleTimeString('ru')}` : 'не на паузе';

  // теги подписчика
  document.getElementById('chat-tags').innerHTML = s.tags.length
    ? s.tags.map(t => `<span class="pill">${esc(t.name)}<span class="x" onclick="chatRemoveTag(${t.id})">✕</span></span>`).join('')
    : '<span style="color:#99a;font-size:12px">нет тегов</span>';
  document.getElementById('chat-add-tag').innerHTML = '<option value="">+ добавить тег</option>' +
    TAGS.filter(t => !s.tags.some(x => x.id === t.id)).map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('');

  // воронки для ручного запуска
  if (!document.getElementById('chat-flow').options.length) {
    const funnels = await api('/funnels');
    document.getElementById('chat-flow').innerHTML = '<option value="">выбрать…</option>' +
      funnels.map(f => `<option value="${f.id}">${esc(f.name)}</option>`).join('');
  }
}

async function refreshChatMessages() {
  if (CHAT_SUB == null) return;
  const msgs = await api(`/subscribers/${CHAT_SUB}/messages`);
  if (msgs.length === CHAT_LAST_COUNT) return;  // без изменений — не перерисовываем
  CHAT_LAST_COUNT = msgs.length;
  const box = document.getElementById('chat-messages');
  box.innerHTML = msgs.map(m => {
    const cls = m.direction === 'in' ? 'in'
      : (m.is_broadcast ? 'out bc' : (m.is_operator ? 'out op' : 'out'));
    const who = m.direction === 'in' ? ''
      : (m.is_broadcast ? '📣 рассылка «' + esc(m.broadcast_name || '') + '»'
        : (m.is_operator ? 'оператор' : 'бот'));
    return `<div class="msg ${cls}">
      <div class="msg-bubble">${rtPreview(m.text)}</div>
      <div class="msg-meta">${who ? who + ' · ' : ''}${new Date(m.created_at + 'Z').toLocaleTimeString('ru', {hour:'2-digit',minute:'2-digit'})}</div>
    </div>`;
  }).join('') || '<div class="chat-empty">Переписки пока нет.</div>';
  box.scrollTop = box.scrollHeight;
}

// --- вложения в чате оператора ---
let CHAT_MEDIA = [];
const CHAT_ICON = { video: '🎬', audio: '🎵', voice: '🎤', video_note: '⭕️', document: '📎' };

function chatPickFiles(input) {
  if (input.files.length) chatUploadFiles([...input.files]);
  input.value = '';
}

async function chatUploadFiles(files) {
  const box = document.getElementById('chat-attachments');
  box.classList.remove('hidden');
  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file, file.name || 'pasted.png');
    try {
      const r = await fetch('/api/media/upload', {
        method: 'POST', headers: { 'Authorization': 'Bearer ' + TOKEN }, body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || 'Ошибка загрузки');
      CHAT_MEDIA.push({ type: data.kind, path: data.path, name: data.name });
    } catch (e) { alert(e.message); }
  }
  renderChatAttachments();
}

function renderChatAttachments() {
  const box = document.getElementById('chat-attachments');
  if (!CHAT_MEDIA.length) { box.classList.add('hidden'); box.innerHTML = ''; return; }
  box.classList.remove('hidden');
  box.innerHTML = CHAT_MEDIA.map((m, i) => {
    const thumb = m.type === 'photo'
      ? `<img src="/${esc(m.path)}" alt="">`
      : `<span class="ca-icon">${CHAT_ICON[m.type] || '📎'}</span>`;
    return `<div class="chat-att">${thumb}
      <span class="ca-name">${esc((m.name || '').slice(0, 18))}</span>
      <button class="ca-del" onclick="chatRemoveAttachment(${i})">✕</button></div>`;
  }).join('');
}
function chatRemoveAttachment(i) { CHAT_MEDIA.splice(i, 1); renderChatAttachments(); }

// перетаскивание файлов в окно чата и вставка картинки из буфера
let _chatDropReady = false;
function setupChatDropzone() {
  if (_chatDropReady) return;
  _chatDropReady = true;
  const drawer = document.getElementById('chat-drawer');
  drawer.addEventListener('dragover', e => { e.preventDefault(); drawer.classList.add('drop-hint'); });
  drawer.addEventListener('dragleave', e => {
    if (!drawer.contains(e.relatedTarget)) drawer.classList.remove('drop-hint');
  });
  drawer.addEventListener('drop', e => {
    e.preventDefault(); drawer.classList.remove('drop-hint');
    if (e.dataTransfer.files.length) chatUploadFiles([...e.dataTransfer.files]);
  });
  document.getElementById('chat-text').addEventListener('paste', e => {
    const imgs = [...(e.clipboardData.items || [])]
      .filter(i => i.type.startsWith('image/')).map(i => i.getAsFile());
    if (imgs.length) { e.preventDefault(); chatUploadFiles(imgs); }
  });
}

async function sendChatMessage() {
  const ta = document.getElementById('chat-text');
  const text = ta.value.trim();
  const media = CHAT_MEDIA.slice();
  if (!text && !media.length) return;
  ta.value = '';
  CHAT_MEDIA = [];
  renderChatAttachments();
  try {
    await api(`/subscribers/${CHAT_SUB}/send`, { method: 'POST', body: { text, media } });
    await refreshChatMessages();
  } catch (e) {
    ta.value = text;
    CHAT_MEDIA = media;
    renderChatAttachments();
  }
}
async function chatPause(minutes) {
  await api(`/subscribers/${CHAT_SUB}/pause`, { method: 'POST', body: { minutes } });
  refreshChatInfo();
}
async function chatStartFlow() {
  const fid = +document.getElementById('chat-flow').value;
  if (!fid) return;
  await api(`/subscribers/${CHAT_SUB}/start_flow`, { method: 'POST', body: { funnel_id: fid } });
  setTimeout(refreshChatMessages, 500);
}
async function chatAddTag(tagId) {
  if (!tagId) return;
  await api(`/subscribers/${CHAT_SUB}/tags`, { method: 'POST', body: { tag_id: +tagId } });
  refreshChatInfo();
}
async function chatRemoveTag(tagId) {
  await api(`/subscribers/${CHAT_SUB}/tags/${tagId}`, { method: 'DELETE' });
  refreshChatInfo();
}

// Esc закрывает верхний открытый слой: модалку, карточку рассылки, чат
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const visible = id => !document.getElementById(id).classList.contains('hidden');
  if (visible('dl-modal')) { closeDeepLink(); return; }
  if (visible('bc-detail')) { closeBroadcast(); return; }
  if (visible('chat-drawer')) { closeChat(); return; }
  if (visible('page-editor')) {
    const ai = document.getElementById('ai-chat');
    if (ai && !ai.classList.contains('hidden')) { toggleAiChat(); return; }
    const sd = document.getElementById('steps-drawer');
    if (sd && !sd.classList.contains('hidden')) { sd.classList.add('hidden'); return; }
  }
});

// ---------- старт ----------
// ждём загрузки всех скриптов (dashboard.js, bot.js и т.д.)
window.addEventListener('load', async function init() {
  if (!TOKEN) { showLogin(); return; }
  try {
    ME = await api('/auth/me');
    applyRoleUI();
    showApp();
  } catch { /* показан логин */ }
});
