// ---------- визуальный редактор воронок (Drawflow) ----------
let editor = null;
let currentFunnelId = null;
let selectedNodeId = null;

const NODE_META = {
  start:     { title: '▶️ Старт',     inputs: 0, outputs: 1 },
  message:   { title: '💬 Сообщение', inputs: 1, outputs: 1 },
  delay:     { title: '⏱ Задержка',   inputs: 1, outputs: 1 },
  condition: { title: '❓ Условие',    inputs: 1, outputs: 2 },
  action:    { title: '⚡️ Действие',  inputs: 1, outputs: 1 },
  language:  { title: '🌐 Язык',      inputs: 1, outputs: 2 },
  filter:    { title: '🔎 Фильтр',    inputs: 1, outputs: 2 },
  chain:     { title: '⛓ Цепочка',   inputs: 1, outputs: 1 },
  note:      { title: '⚠️ Заметка',   inputs: 0, outputs: 0 },
};

const MEDIA_ICON = { video: '🎬', audio: '🎵', voice: '🎤', video_note: '⭕️', document: '📎' };

function nodeHtml(type, data) {
  data = data || {};
  // «Сообщение» рендерим как превью реального сообщения: вложения + текст + кнопки
  if (type === 'message') {
    const media = data.media || (data.photo_url ? [{ type: 'photo', path: data.photo_url }] : []);
    let mediaHtml = '';
    if (media.length) {
      // одно вложение показываем крупно, во всю ширину карточки — по нему
      // и узнают шаг; несколько складываем сеткой, как альбом в Telegram
      const grid = media.length === 1 ? 'one' : (media.length === 2 ? 'two' : 'many');
      mediaHtml = `<div class="df-media ${grid}">` + media.slice(0, 6).map(m => {
        if (m.type === 'photo') {
          const src = m.path.startsWith('http') ? m.path : '/' + m.path;
          // draggable=false — иначе браузер начинает своё перетаскивание
          // картинки, и блок остаётся на месте
          return `<img class="df-media-thumb" draggable="false" src="${esc(src)}" alt="">`;
        }
        return `<span class="df-media-thumb icon" title="${esc(m.type)}">${MEDIA_ICON[m.type] || '📎'}</span>`;
      }).join('') + (media.length > 6 ? `<span class="df-media-more">+${media.length - 6}</span>` : '') + `</div>`;
    }
    const orderHint = (data.text_first && media.length)
      ? '<div class="df-order">↑ текст над вложением</div>' : '';
    const text = (data.text || '').slice(0, 500);
    const toggle = text.length > 120
      ? `<span class="df-toggle" onclick="toggleNodeExpand(event, this)">развернуть ▾</span>` : '';
    const textHtml = text
      ? `<div class="df-sub">${rtPreview(text)}</div>${toggle}`
      : (media.length ? '' : `<div class="df-sub empty">нет текста</div>`);
    // кнопки — как в Telegram, с номером выхода
    let btnsHtml = '';
    const buttons = data.buttons || [];
    if (buttons.length) {
      let port = 2;
      btnsHtml = `<div class="df-btns">` + buttons.map(b => {
        const st = b.style ? ` st-${b.style}` : '';
        const off = b.disabled ? ' st-off' : '';
        const icon = b.icon_custom_emoji_id && typeof emojiIconHtml === 'function'
          ? emojiIconHtml(b.icon_custom_emoji_id) + ' ' : '';
        if (b.url) return `<div class="df-btn url${st}${off}">🔗 ${icon}${esc(b.label || 'ссылка')}</div>`;
        return `<div class="df-btn${st}${off}"><span class="df-btn-port">${port++}</span>${icon}${esc(b.label || 'кнопка')}</div>`;
      }).join('') + `</div>`;
    }
    // Номер шага ставим пустым: настоящий проставит refreshStepNumbers, когда
    // карточка окажется на холсте и станет понятен порядок обхода.
    return `<div class="df-title">${NODE_META.message.title}<span class="df-step"></span></div>` +
           `${orderHint}${mediaHtml}${textHtml}${btnsHtml}` +
           `<div class="df-ports">${buttons.some(b => !b.url) ? '1: далее' : ''}</div>`;
  }
  if (type === 'language') {
    const langs = data.languages || [];
    const rows = langs.map((l, i) =>
      `<div class="df-btn"><span class="df-btn-port">${i + 2}</span>${esc(l)}</div>`).join('');
    return `<div class="df-title">${NODE_META.language.title}</div>` +
           `<div class="df-btns">${rows || '<div class="df-sub empty">языки не заданы</div>'}</div>` +
           `<div class="df-ports">1: остальные</div>`;
  }
  const sum = summary(type, data);
  const toggle = sum.length > 120
    ? `<span class="df-toggle" onclick="toggleNodeExpand(event, this)">развернуть ▾</span>` : '';
  return `<div class="df-title">${NODE_META[type].title}</div>` +
         `<div class="df-sub">${esc(sum)}</div>` + toggle +
         `<div class="df-ports">${esc(portsHint(type, data))}</div>`;
}

function toggleNodeExpand(ev, el) {
  ev.stopPropagation();
  const nodeEl = el.closest('.drawflow-node');
  const expanded = nodeEl.classList.toggle('expanded');
  el.textContent = expanded ? 'свернуть ▴' : 'развернуть ▾';
  // перерисовать линии связей и порты под новый размер карточки
  if (editor) editor.updateConnectionNodes(nodeEl.id);
  decoratePorts();
}

// Список тегов для селекта плюс пункт «создать». Создавать тег, не выходя
// из редактора воронки, — иначе приходится бросать несохранённую работу,
// идти в раздел «Теги» и возвращаться.
// Короткое описание фильтра для карточки. Названия полей и операций берём
// из того же справочника, что и конструктор, — чтобы подписи не разъезжались.
function segSummaryText(filter) {
  const conds = (filter && filter.conditions) || [];
  if (!conds.length && !(filter && filter.active_24h)) return 'условия не заданы';
  const fields = (typeof SEG_FIELDS !== 'undefined' && SEG_FIELDS) || [];
  const parts = conds.map(c => {
    const fd = fields.find(f => f.key === c.field);
    const label = fd ? fd.label : c.field;
    const op = (fd && (fd.ops.find(o => o[0] === c.op) || [])[1]) || c.op;
    let val = c.value;
    if (fd && (fd.type === 'choice' || fd.type === 'select')) {
      const o = (fd.options || []).find(x => String(x.v) === String(c.value));
      if (o) val = o.l;
    } else if (fd && fd.type === 'weekdays') {
      // «1,2,4» на карточке узла не читается — показываем Пн, Вт, Чт
      val = String(c.value ?? '').split(',').filter(Boolean).map(d => {
        const o = (fd.options || []).find(x => String(x.v) === d);
        return o ? o.l : d;
      }).join(', ');
    } else if (fd && fd.type === 'time' && c.op === 'between') {
      val = String(c.value ?? '').replace('-', ' – ');
    }
    return `${label} ${op}${val === '' || val == null ? '' : ' ' + val}`;
  });
  if (filter && filter.active_24h) parts.unshift('активен за 24 часа');
  return parts.join((filter && filter.match) === 'any' ? ' ИЛИ ' : ' И ');
}

function tagOptions(selected, head = '') {
  return head + TAGS.map(t =>
    `<option value="${t.id}" ${String(selected) === String(t.id) ? 'selected' : ''}>${esc(t.name)}</option>`
  ).join('') + '<option value="__new__">+ создать тег…</option>';
}

async function maybeCreateTag(sel) {
  if (sel.value !== '__new__') return;
  const name = (prompt('Название нового тега:') || '').trim();
  if (!name) { sel.innerHTML = tagOptions('', sel.dataset.head || ''); return; }
  let tag;
  try { tag = await api('/tags', { method: 'POST', body: { name } }); }
  catch (e) { sel.innerHTML = tagOptions('', sel.dataset.head || ''); return; }
  await loadTags();
  // новый тег появляется сразу во всех селектах на экране, а в том,
  // откуда создавали, ещё и выбирается
  document.querySelectorAll('select.tag-select').forEach(el => {
    el.innerHTML = tagOptions(el === sel ? tag.id : el.value, el.dataset.head || '');
  });
  scheduleAutoApply();
  flashStatus(`Тег «${name}» создан`);
}

function tagName(id) {
  const t = TAGS.find(t => String(t.id) === String(id));
  return t ? t.name : '?';
}

function summary(type, d) {
  d = d || {};
  if (type === 'start') return 'Точка входа';
  if (type === 'message') {
    const n = (d.media || []).length || (d.photo_url ? 1 : 0);
    const tag = n ? `📎${n} ` : '';
    return tag + (d.text || (n ? 'вложение' : 'нет текста')).slice(0, 500);
  }
  if (type === 'delay') {
    const u = { seconds: 'сек', minutes: 'мин', hours: 'ч', days: 'дн' }[d.unit] || '?';
    return `Ждать ${d.amount || '?'} ${u}`;
  }
  if (type === 'condition') return `Есть тег «${tagName(d.tag)}»?`;
  if (type === 'language') {
    const names = (d.languages || []).map(l => String(l).split(',')
      .map(c => languageName(c.trim())).join(' / '));
    return `Язык: ${names.join(' / ') || '?'} / остальные`;
  }
  if (type === 'filter') return segSummaryText(d.filter);
  if (type === 'note') return d.text || 'пустая заметка';
  if (type === 'action') return actionSummary(d);
  if (type === 'chain') return chainName(d.funnel_id);
  return '';
}

// Цепочки, доступные для вызова. Грузятся вместе с редактором: список
// короткий, а без него блок «Цепочка» нечем заполнить.
let CHAINS = [];
let IS_CHAIN = false;   // открытая сейчас воронка — на самом деле цепочка

async function loadChains() {
  try {
    const all = await api('/funnels');
    CHAINS = all.filter(f => f.is_chain && f.id !== currentFunnelId);
  } catch (e) { CHAINS = []; }
}

function chainName(id) {
  const c = CHAINS.find(c => String(c.id) === String(id));
  if (c) return 'Цепочка: ' + c.name;
  return id ? 'Цепочка #' + id + ' — удалена' : 'Цепочка не выбрана';
}

// Операции блока «Действие». Порядок = порядок в выпадающем списке.
const ACTION_OPS = [
  ['add_tag',            'Добавить тег',            1],
  ['remove_tag',         'Снять тег',               1],
  ['unsubscribe',        'Отписать от рассылок',    1],
  ['check_subscription', 'Проверить подписку на канал', 2],
  ['delete_message',     'Удалить прошлое сообщение',   1],
];
const ACTION_OUTPUTS = Object.fromEntries(ACTION_OPS.map(([op, , n]) => [op, n]));

function actionSummary(d) {
  switch (d.op) {
    case 'remove_tag':         return 'Снять тег: ' + tagName(d.tag);
    case 'unsubscribe':        return 'Отписать от рассылок';
    case 'check_subscription': return 'Подписан на ' + (d.channel || '?') + '?';
    case 'delete_message':     return 'Удалить сообщение: ' + msgNodeName(d.target);
    default:                   return 'Добавить тег: ' + tagName(d.tag);
  }
}

// Список блоков «Сообщение» текущей воронки — чтобы выбрать, что удалять.
function messageNodes() {
  const out = [];
  if (!editor) return out;
  const data = editor.export().drawflow.Home.data;
  Object.keys(data).forEach(id => {
    if (data[id].name !== 'message') return;
    // текст берём через plainText: он снимает разметку так же, как её видит
    // подписчик («<b>Привет</b>, друг» -> «Привет, друг», а не «Привет , друг»)
    const t = plainText((data[id].data || {}).text || '').replace(/\s+/g, ' ').trim();
    out.push({ id: String(id), label: '#' + id + ' · ' + (t.slice(0, 40) || 'без текста') });
  });
  return out;
}

function msgNodeName(target) {
  if (!target || target === 'last') return 'последнее';
  const n = messageNodes().find(m => m.id === String(target));
  return n ? n.label : '#' + target + ' (блок удалён)';
}

function portsHint(type, d) {
  d = d || {};
  if (type === 'condition') return '1: да  •  2: нет';
  if (type === 'action' && d.op === 'check_subscription') return '1: подписан  •  2: нет';
  if (type === 'message') {
    const btns = d.buttons || [];
    let s = '1: далее';
    btns.forEach((b, i) => { if (!b.url) s += `  •  ${i + 2}: ${b.label || 'кнопка'}`; });
    return s;
  }
  return '';
}

// ---------- открытие/закрытие ----------
let editorReturnTo = 'funnels';  // куда вернуться по «Назад»
let EDITOR_SNAPSHOT = null;      // состояние на момент загрузки/сохранения

// всё, что уходит в saveFunnel — если отличается от снимка, есть несохранённое
function editorStateJson() {
  if (!editor) return '';
  try {
    return JSON.stringify({
      graph: editor.export(),
      name: document.getElementById('funnel-name').value,
      trigger: document.getElementById('funnel-trigger').value,
      triggerValue: document.getElementById('funnel-trigger-value').value,
      triggerTag: document.getElementById('funnel-trigger-tag').value,
      bots: [...document.querySelectorAll('#funnel-bots .pill.on')].map(p => p.dataset.id),
    });
  } catch (e) { return ''; }
}

function editorDirty() {
  return EDITOR_SNAPSHOT !== null && editorStateJson() !== EDITOR_SNAPSHOT;
}

// не дать закрыть вкладку с несохранённой воронкой
window.addEventListener('beforeunload', e => {
  if (!document.getElementById('page-editor').classList.contains('hidden') && editorDirty()) {
    saveDraft();   // даже если человек подтвердит уход — изменения не пропадут
    e.preventDefault();
    e.returnValue = '';
  }
});

// Из блока «Цепочка» — сразу в редактор этой цепочки. Кнопка была, а
// функции не было: клик молча падал с ошибкой.
function openChainEditor(id) {
  openEditor(+id);
}

async function openEditor(id) {
  // переход из одной воронки в другую (например, в цепочку) — тот же уход
  // из редактора, что и кнопка «Назад»: несохранённое не теряем
  const edPage = document.getElementById('page-editor');
  if (editor && !edPage.classList.contains('hidden') && currentFunnelId !== id) {
    flushAutoApply();
    if (editorDirty()) {
      saveDraft();
      if (!confirm('Есть несохранённые изменения — перейти без сохранения?\n\nОни останутся черновиком: при следующем открытии воронки их можно будет восстановить.')) return;
    }
  }
  // запоминаем, откуда пришли (список воронок или экран бота)
  editorReturnTo = !document.getElementById('page-bot').classList.contains('hidden') ? 'bot' : 'funnels';
  await loadTags();
  await loadSegFields();      // нужны ноде «Фильтр»: и конструктор, и подписи
  currentFunnelId = id;
  const [f, bots] = await Promise.all([api('/funnels/' + id), api('/bots')]);
  await loadChains();
  IS_CHAIN = !!f.is_chain;
  FUNNEL_LIVE = !!f.is_active || IS_CHAIN;
  setAutosaveStatus('');
  if (!_autosaveTimer) _autosaveTimer = setInterval(autosaveTick, AUTOSAVE_MS);

  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.getElementById('page-editor').classList.remove('hidden');
  document.getElementById('funnel-name').value = f.name;
  document.getElementById('funnel-trigger').value = f.trigger_type;

  if (f.trigger_type === 'keyword') document.getElementById('funnel-trigger-value').value = f.trigger_value || '';
  populateTriggerTag(f.trigger_type, f.trigger_value || '');
  updateTriggerInputs();

  // У цепочки нет ни триггера, ни своих ботов: она идёт под ботом той
  // воронки, которая её вызвала. Показывать пустые настройки — врать.
  document.getElementById('funnel-trigger').classList.toggle('hidden', IS_CHAIN);
  document.getElementById('chain-hint').classList.toggle('hidden', !IS_CHAIN);
  document.querySelector('.editor-subbar').classList.toggle('hidden', IS_CHAIN);
  document.getElementById('funnel-name').placeholder =
    IS_CHAIN ? 'Название цепочки' : 'Название воронки';

  // привязка к ботам
  const fbEl = document.getElementById('funnel-bots');
  const assigned = new Set(f.bot_ids || []);
  fbEl.innerHTML = bots.length
    ? bots.map(b => `<span class="pill gray ${assigned.has(b.id) ? 'on' : ''}" data-id="${b.id}" onclick="this.classList.toggle('on')">${esc(b.name)}</span>`).join('')
    : '<span style="color:#99a;font-size:12px">нет ботов — создайте в разделе «Боты»</span>';

  if (!editor) {
    editor = new Drawflow(document.getElementById('drawflow'));
    editor.reroute = true;
    // «магнит»: отпустил связь в любом месте карточки — цепляется к её входу
    editor.force_first_input = true;
    editor.start();
    // Drawflow по умолчанию таскает холст левой кнопкой — отключаем:
    // холст двигаем тачпадом/пробелом, а ЛКМ по фону = рамка выделения
    const _origMove = editor.position.bind(editor);
    editor.position = function (e) {
      if (editor.drag || editor.connection || editor.drag_point) return _origMove(e);
      if (editor.editor_selected && !spaceHeld) return;  // блокируем панорамирование ЛКМ
      return _origMove(e);
    };
    editor.on('nodeSelected', id => { onNodeSelected(id); showProps(id); });
    // Drawflow узнаёт об отпускании кнопки только по mouseup на самом холсте.
    // А клик по блоку у правого края открывает панель свойств прямо под
    // курсором — кнопку отпускают уже над панелью, Drawflow этого не видит
    // и считает, что блок всё ещё тащат: блок «прилипает» к мыши. Отпустили
    // где угодно за пределами холста — завершаем перетаскивание сами.
    document.addEventListener('mouseup', e => {
      if (!editor || document.getElementById('drawflow').contains(e.target)) return;
      if (editor.drag || editor.drag_point || editor.editor_selected || editor.connection) {
        try { editor.dragEnd(e); } catch (err) {
          editor.drag = editor.drag_point = editor.editor_selected = false;
        }
      }
    });
    // Drawflow шлёт nodeUnselected и при перевыборе другого блока — группу
    // при этом сбрасывать нельзя (это и ломало групповое перетаскивание).
    // Пустой фон снимает выделение сам (см. setupMarquee).
    editor.on('nodeUnselected', () => { hideProps(); });
    editor.on('nodeRemoved', () => { hideProps(); refreshStepNumbers(); });
    // номера зависят от связей и состава блоков, а не от содержимого карточки
    ['nodeCreated', 'connectionCreated', 'connectionRemoved']
      .forEach(ev => editor.on(ev, () => refreshStepNumbers()));
    setupClipboard();
    setupQuickActions();
  }
  editor.clear();
  clearMultiSelection();
  hideProps();

  if (f.graph_ui && f.graph_ui.drawflow) {
    editor.import(f.graph_ui);
  } else {
    editor.addNode('start', 0, 1, 80, 150, 'start', {}, nodeHtml('start', {}), false);
  }
  document.getElementById('steps-drawer').classList.add('hidden');
  redrawAllNodes();
  decoratePorts();
  refreshStepNumbers();
  loadFunnelStats();
  EDITOR_SNAPSHOT = editorStateJson();
  offerDraft();
}

// ---------- статистика шагов ----------
let FUNNEL_STATS = null;

async function loadFunnelStats() {
  try { FUNNEL_STATS = await api(`/funnels/${currentFunnelId}/stats`); }
  catch { FUNNEL_STATS = null; return; }
  applyStatsBadges();
}

function applyStatsBadges() {
  document.querySelectorAll('.node-stat-badge, .node-clicks').forEach(el => el.remove());
  if (!FUNNEL_STATS) return;
  const entered = FUNNEL_STATS.unique_entered || 0;
  // бейдж «сколько дошло» на каждом узле
  Object.entries(FUNNEL_STATS.nodes || {}).forEach(([nid, count]) => {
    const el = document.getElementById('node-' + nid);
    if (!el) return;
    const pct = entered ? Math.round(100 * count / entered) : 0;
    const b = document.createElement('div');
    b.className = 'node-stat-badge';
    b.title = `дошло ${count} из ${entered} вошедших (${pct}%)`;
    b.textContent = `👤 ${count}`;
    el.appendChild(b);
  });
  // клики по кнопкам — строкой внутри карточки
  const byNode = {};
  (FUNNEL_STATS.clicks || []).forEach(c => {
    (byNode[c.node_id] = byNode[c.node_id] || []).push(c);
  });
  Object.entries(byNode).forEach(([nid, clicks]) => {
    const el = document.querySelector(`#node-${nid} .drawflow_content_node`);
    const node = editor.getNodeFromId(nid);
    if (!el || !node) return;
    const labels = (node.data.buttons || []).map(b => b.label);
    const line = clicks.sort((a, b) => a.button - b.button)
      .map(c => `${esc(labels[c.button] || 'кнопка ' + (c.button + 1))}: <b>${c.count}</b>`)
      .join(' · ');
    const d = document.createElement('div');
    d.className = 'node-clicks';
    d.innerHTML = '👆 ' + line;
    el.appendChild(d);
  });
  decoratePorts();  // строка кликов меняет высоту карточки
}

// упорядоченный список шагов (BFS от старта) для панели «Шаги»
function orderedSteps() {
  const df = editor.export().drawflow.Home.data;
  let startId = null;
  Object.values(df).forEach(n => { if (n.name === 'start') startId = String(n.id); });
  const order = [], seen = new Set();
  const queue = startId ? [startId] : [];
  while (queue.length) {
    const id = queue.shift();
    if (seen.has(id)) continue;
    seen.add(id);
    const n = df[id];
    if (!n) continue;
    if (n.name !== 'start' && n.name !== 'note') order.push({ id, name: n.name, data: n.data });
    Object.values(n.outputs || {}).forEach(p =>
      (p.connections || []).forEach(c => queue.push(String(c.node))));
  }
  // узлы, до которых обход не дошёл, — в конец: так же делает выгрузка,
  // иначе номера на плитках разъедутся с колонками в таблице
  Object.keys(df).forEach(id => {
    const n = df[id];
    if (!seen.has(String(id)) && n.name !== 'start' && n.name !== 'note') {
      order.push({ id: String(id), name: n.name, data: n.data });
    }
  });
  return order;
}

// Сколько шагов помещается в выгрузку: в листе колонки Step 1 … Step 25.
const EXPORT_MAX_STEPS = 25;

// Номера шагов = позиция среди блоков «Сообщение» в порядке обхода. Ровно эти
// номера становятся колонками Step N в Google-таблице, поэтому считаются они
// тем же способом, что и на сервере.
function stepNumbers() {
  const map = {};
  let k = 0;
  orderedSteps().forEach(st => {
    if (st.name === 'message') map[String(st.id)] = ++k;
  });
  return map;
}

// Номера, посчитанные в последний раз. Нужны при отрисовке карточки: она
// собирается до того, как узел попадёт на холст, и пересчитать порядок там
// уже поздно.
let STEP_NUMBERS = {};

// Пересчитать и расставить номера. Вставка или удаление блока сдвигает
// нумерацию у всех следующих — оставить старые номера значило бы врать.
let _stepsTimer = null;
function refreshStepNumbers() {
  clearTimeout(_stepsTimer);
  _stepsTimer = setTimeout(() => {
    if (!editor) return;
    STEP_NUMBERS = stepNumbers();
    const df = editor.export().drawflow.Home.data;
    Object.keys(df).forEach(id => {
      if (df[id].name !== 'message') return;
      const el = document.querySelector(`#node-${id} .df-step`);
      if (!el) return;
      const num = STEP_NUMBERS[String(id)];
      el.textContent = num ? 'Шаг ' + num : '';
      el.classList.toggle('over', !!num && num > EXPORT_MAX_STEPS);
      el.title = !num ? ''
        : num > EXPORT_MAX_STEPS
          ? `Шаг ${num}: в выгрузку не попадёт — в таблице только ${EXPORT_MAX_STEPS} колонок`
          : `Шаг ${num} — колонка «Step ${num} users» в таблице`;
    });
  }, 0);
}

async function toggleStepsDrawer() {
  const d = document.getElementById('steps-drawer');
  const show = d.classList.contains('hidden');
  d.classList.toggle('hidden');
  if (!show) return;
  await loadFunnelStats();
  const s = FUNNEL_STATS || { unique_entered: 0, runs: 0, nodes: {}, done: 0 };
  const entered = s.unique_entered || 0;
  const steps = orderedSteps();
  let prev = entered;
  let html = `
    <div class="steps-head">
      <b>Шаги воронки</b>
      <button class="btn" onclick="toggleStepsDrawer()">✕</button>
    </div>
    <div class="step-row entry">
      <div class="step-label">▶️ Вошли в воронку</div>
      <div class="step-nums"><span class="step-count">${entered}</span><span class="step-pct">запусков: ${s.runs}</span></div>
    </div>`;
  const nums = stepNumbers();
  steps.forEach(st => {
    const count = s.nodes[st.id] || 0;
    const fromPrev = prev ? Math.round(100 * count / prev) : 0;
    const fromStart = entered ? Math.round(100 * count / entered) : 0;
    // номер — тот же, что на плитке и в колонке Step N выгрузки
    const num = nums[String(st.id)];
    const badge = num ? `<span class="step-no${num > EXPORT_MAX_STEPS ? ' over' : ''}">${num}</span>` : '';
    html += `
      <div class="step-row">
        <div class="step-label">${badge}${esc(summary(st.name, st.data)).slice(0, 60)}</div>
        <div class="step-bar"><i style="width:${fromStart}%"></i></div>
        <div class="step-nums">
          <span class="step-count">${count}</span>
          <span class="step-pct">${fromStart}% от входа · ${fromPrev}% от пред.</span>
        </div>
      </div>`;
    prev = count || prev;
  });
  html += `<div class="step-row entry"><div class="step-label">🏁 Прошли до конца</div>
    <div class="step-nums"><span class="step-count">${s.done}</span></div></div>`;
  d.innerHTML = html;
}

function closeEditor() {
  flushAutoApply();
  // черновик — до вопроса: «да, выйти» мог быть нажат по ошибке
  if (editorDirty()) saveDraft();
  if (editorDirty() &&
      !confirm('Есть несохранённые изменения — выйти без сохранения?\n\nОни останутся черновиком: при следующем открытии воронки их можно будет восстановить.\n(Сохранить: кнопка «💾 Сохранить» или Ctrl/⌘+S)')) return;
  EDITOR_SNAPSHOT = null;
  if (editorReturnTo === 'bot' && typeof BOT_ID !== 'undefined' && BOT_ID) openBot(BOT_ID);
  else go('funnels');
}

document.getElementById('funnel-trigger').addEventListener('change', () => {
  const t = document.getElementById('funnel-trigger').value;
  const cur = document.getElementById('funnel-trigger-tag').value;
  populateTriggerTag(t, cur);
  updateTriggerInputs();
});

// Заполняет селект тега под конкретный триггер:
//  tag_added — выбор тега (обязателен, «по добавлению какого тега запускать»)
//  message   — необязательный фильтр «не запускать тем, у кого есть тег»
function populateTriggerTag(triggerType, value) {
  const tagSel = document.getElementById('funnel-trigger-tag');
  const head = triggerType === 'message'
    ? '<option value="">— срабатывать всегда —</option>' : '';
  tagSel.dataset.head = head;
  tagSel.classList.add('tag-select');
  tagSel.onchange = () => maybeCreateTag(tagSel);
  tagSel.innerHTML = tagOptions(value, head);
  // сохраняем прежний выбор, если он ещё есть в списке
  if (![...tagSel.options].some(o => o.value === String(value))) tagSel.value = '';
}

function updateTriggerInputs() {
  const t = document.getElementById('funnel-trigger').value;
  if (IS_CHAIN) {
    // у цепочки триггера нет — прячем всё, что к нему относится
    ['funnel-trigger-value', 'funnel-trigger-tag', 'trigger-msg-hint', 'trigger-tag-hint']
      .forEach(id => document.getElementById(id)?.classList.add('hidden'));
    return;
  }
  document.getElementById('funnel-trigger-value').classList.toggle('hidden', t !== 'keyword');
  const tagSel = document.getElementById('funnel-trigger-tag');
  tagSel.classList.toggle('hidden', t !== 'tag_added' && t !== 'message');
  // подписи-подсказки
  document.getElementById('trigger-msg-hint').classList.toggle('hidden', t !== 'message');
  const tagHint = document.getElementById('trigger-tag-hint');
  if (tagHint) tagHint.classList.toggle('hidden', t !== 'tag_added');
}

// ---------- блоки ----------
function blockDefaults(type) {
  return {
    start: {},
    message: { text: '', photo_url: '', buttons: [] },
    delay: { amount: 1, unit: 'hours' },
    condition: { tag: TAGS[0] ? String(TAGS[0].id) : '' },
    language: { languages: ['ru'] },
    filter: { filter: { match: 'all', active_24h: false, conditions: [] } },
    action: { op: 'add_tag', tag: TAGS[0] ? String(TAGS[0].id) : '' },
    chain: { funnel_id: CHAINS[0] ? String(CHAINS[0].id) : '' },
    note: { text: '' },
  }[type];
}

// Смена операции меняет и набор полей, и число выходов. Сначала сохраняем
// выбор, потом перерисовываем панель — иначе перерисовка возьмёт старые данные
// узла и вернёт список к прежнему значению.
function onActionOpChange() {
  const id = selectedNodeId;
  applyProps();
  showProps(id);
}

// Поля, зависящие от выбранной операции блока «Действие».
function actionBody(op, d) {
  if (op === 'add_tag' || op === 'remove_tag') {
    return `<label>Тег</label>
      <select id="p-tag" class="tag-select" onchange="maybeCreateTag(this)">${tagOptions(d.tag)}</select>`;
  }
  if (op === 'unsubscribe') {
    return `<div class="hint-box">
      Человек перестанет получать рассылки и отложенные шаги воронок.
      Диалог с ботом остаётся — если он снова нажмёт <code>/start</code>,
      подписка вернётся. Обычно этот блок вешают на кнопку «Отписаться».
    </div>`;
  }
  if (op === 'check_subscription') {
    return `<label>Канал</label>
      <input id="p-channel" placeholder="@mychannel или -1001234567890"
             value="${esc(d.channel || '')}">
      <div class="hint-box">
        <b>Бот должен быть администратором этого канала</b> — иначе Telegram
        не отдаёт список участников и все уходят в выход «не подписан».<br>
        Выход 1 — подписан, выход 2 — нет.
      </div>`;
  }
  if (op === 'delete_message') {
    const nodes = messageNodes().filter(n => String(n.id) !== String(selectedNodeId));
    const cur = String(d.target || 'last');
    const missing = cur !== 'last' && !nodes.some(n => n.id === cur);
    return `<label>Какое сообщение удалить</label>
      <select id="p-target">
        <option value="last" ${cur === 'last' ? 'selected' : ''}>последнее отправленное</option>
        ${nodes.map(n =>
          `<option value="${n.id}" ${cur === n.id ? 'selected' : ''}>${esc(n.label)}</option>`).join('')}
        ${missing ? `<option value="${esc(cur)}" selected>#${esc(cur)} — блок удалён</option>` : ''}
      </select>
      <div class="hint-box">
        Удаляется то, что этот бот отправил конкретному человеку выбранным
        блоком. Telegram разрешает боту удалять свои сообщения
        <b>только 48 часов</b> — что старше, останется в чате.
      </div>`;
  }
  return '';
}

function addBlockAt(type, x, y) {
  const m = NODE_META[type];
  const defaults = blockDefaults(type);
  return editor.addNode(type, m.inputs, m.outputs, x, y, type, defaults, nodeHtml(type, defaults), false);
}

// клик по палитре — как раньше, блок появляется в видимой части холста
function addBlock(type) {
  const rect = document.getElementById('drawflow').getBoundingClientRect();
  const p = screenToCanvas(rect.left + 260 + Math.random() * 120,
                           rect.top + 120 + Math.random() * 200);
  addBlockAt(type, p.x, p.y);
}

// Точка экрана -> координаты холста Drawflow.
//
// Холст сдвигается и масштабируется CSS-трансформом
// translate(canvas_x, canvas_y) scale(zoom), а масштаб идёт от ЦЕНТРА
// холста (transform-origin по умолчанию), не от левого верхнего угла.
// Раньше формула этого не учитывала: на 100% всё совпадало, а стоило
// отдалить или приблизить холст — блок из палитры падал в сторону центра,
// хотя отпускали его в другом месте. Поэтому считаем от фактического
// положения холста на экране: его левый верхний угол уже учитывает и
// сдвиг, и масштаб с любым центром — так же считает и сам Drawflow.
function screenToCanvas(clientX, clientY) {
  const pre = editor.precanvas.getBoundingClientRect();
  const z = editor.zoom || 1;
  return { x: (clientX - pre.left) / z, y: (clientY - pre.top) / z };
}

// координаты мыши -> координаты холста Drawflow (с учётом сдвига и зума)
function canvasPoint(e) {
  return screenToCanvas(e.clientX, e.clientY);
}

// перетаскивание из палитры: блок создаётся там, где отпустили
function setupPaletteDnD() {
  const canvas = document.getElementById('drawflow');
  document.querySelectorAll('.palette-item[data-block]').forEach(item => {
    item.setAttribute('draggable', 'true');
    item.addEventListener('dragstart', e => {
      e.dataTransfer.setData('text/sb-block', item.dataset.block);
      e.dataTransfer.effectAllowed = 'copy';
    });
  });
  canvas.addEventListener('dragover', e => {
    if ([...e.dataTransfer.types].includes('text/sb-block')) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
    }
  });
  canvas.addEventListener('drop', e => {
    const type = e.dataTransfer.getData('text/sb-block');
    if (!type) return;
    e.preventDefault();
    const p = canvasPoint(e);
    // центрируем карточку под курсором
    addBlockAt(type, p.x - 110, p.y - 20);
  });
}

function refreshNodeHtml(id) {
  const node = editor.getNodeFromId(id);
  const html = nodeHtml(node.name, node.data);
  const el = document.querySelector(`#node-${id} .drawflow_content_node`);
  if (el) el.innerHTML = html;
  // Drawflow держит разметку карточки отдельно от DOM и отдаёт именно её
  // при export(). Без этой строки в базу уезжала старая карточка: на экране
  // текст новый, а после перезагрузки — снова прежний.
  const stored = editor.drawflow.drawflow.Home.data[id];
  if (stored) stored.html = html;
  // вернуть бейджи статистики и номер шага после перерисовки
  if (typeof applyStatsBadges === 'function') applyStatsBadges();
  refreshStepNumbers();
  decoratePorts();
  // связи могли сместиться из-за изменившейся высоты карточки
  try { editor.updateConnectionNodes('node-' + id); } catch (e) {}
  syncQuickActions();   // кнопки под блоком — под новой высотой
}

// ---------- подписи и выравнивание портов ----------
// У «Сообщения» порт каждой кнопки встаёт напротив её строки и подписан её
// номером; порт «далее» — со стрелкой напротив строки «1: далее».
// У «Условия» порты подписаны ✓ (да) и ✕ (нет).
function decoratePorts() {
  if (!editor) return;
  const data = editor.export().drawflow.Home.data;
  Object.values(data).forEach(n => {
    const nodeEl = document.getElementById('node-' + n.id);
    if (!nodeEl) return;
    const outs = nodeEl.querySelectorAll('.outputs .output');
    if (!outs.length) return;

    if (n.name === 'condition' || n.name === 'filter') {
      const yes = n.name === 'filter' ? 'подходит под условия' : 'да — тег есть';
      const no = n.name === 'filter' ? 'не подходит' : 'нет — тега нет';
      if (outs[0]) { outs[0].textContent = '✓'; outs[0].classList.add('port-yes'); outs[0].title = yes; }
      if (outs[1]) { outs[1].textContent = '✕'; outs[1].classList.add('port-no'); outs[1].title = no; }
      return;
    }
    if (n.name !== 'message' && n.name !== 'language') return;

    const zoom = editor.zoom || 1;
    const nodeRect = nodeEl.getBoundingClientRect();
    const nextLine = nodeEl.querySelector('.df-ports');
    const btnRows = nodeEl.querySelectorAll('.df-btn:not(.url)');

    outs.forEach((out, idx) => {
      // idx 0 = «далее», idx 1..N = кнопки по порядку
      const target = idx === 0 ? nextLine : btnRows[idx - 1];
      out.classList.add('port-labeled');
      if (idx === 0) {
        out.textContent = n.name === 'language' ? '∗' : '→';
        out.classList.add('port-next');
        out.title = n.name === 'language'
          ? 'остальные — язык не совпал ни с одной веткой'
          : 'далее (сразу после отправки)';
      } else {
        out.textContent = String(idx + 1);
        out.classList.add('port-btn');
        const label = btnRows[idx - 1] ? btnRows[idx - 1].textContent.replace(/^\d+/, '').trim() : '';
        out.title = (n.name === 'language' ? 'язык «' : 'кнопка «') + label + '»';
      }
      if (target) {
        const r = target.getBoundingClientRect();
        const top = (r.top + r.height / 2 - nodeRect.top) / zoom - out.offsetHeight / 2;
        out.style.position = 'absolute';
        out.style.top = top + 'px';
        out.style.right = '-9px';
      }
    });
    try { editor.updateConnectionNodes('node-' + n.id); } catch (e) {}
  });
}

// Перерисовать все карточки из данных узлов. Нужно после загрузки воронки:
// в базе могла остаться разметка, отставшая от данных (старый баг сохранения),
// и без этого человек видел бы прежний текст, пока не откроет каждый блок.
function redrawAllNodes() {
  const df = editor.drawflow.drawflow.Home.data;
  Object.keys(df).forEach(id => {
    try { refreshNodeHtml(id); } catch (e) { /* узел мог исчезнуть */ }
  });
}

// ---------- панель свойств ----------
function showProps(id) {
  selectedNodeId = id;
  const node = editor.getNodeFromId(id);
  const d = node.data || {};
  const props = document.getElementById('props');
  let html = `<h3>${NODE_META[node.name].title}</h3>`;

  if (node.name === 'start') {
    html += `<p style="font-size:13px;color:#7a8499">Точка входа. Триггер настраивается сверху в панели воронки.</p>`;
  } else if (node.name === 'message') {
    // инициализируем список вложений (с обратной совместимостью с photo_url)
    MEDIA_ITEMS = Array.isArray(d.media) ? d.media.map(m => ({ ...m }))
      : (d.photo_url ? [{ type: 'photo', path: d.photo_url, name: '' }] : []);
    html += `
      <label>Текст — выделите и жмите кнопку, {first_name} подставится</label>
      <div id="p-text-rt"></div>
      <label>Где текст</label>
      <select id="p-order">
        <option value="" ${d.text_first ? '' : 'selected'}>под вложением</option>
        <option value="1" ${d.text_first ? 'selected' : ''}>над вложением</option>
      </select>
      <div class="hint-box" style="margin-top:6px">
        В обоих случаях это <b>одно сообщение</b>: текст остаётся подписью,
        меняется только его место. Кнопки всегда внизу.<br>
        Сверху текст умеют показывать <b>фото и видео</b>. У аудио, голосового,
        файла и кружка Telegram такого не даёт — там текст уйдёт отдельным
        сообщением перед вложением.
      </div>

      <label>Вложения (фото, видео, аудио, голосовое, кружок, файл)</label>
      <div id="media-list"></div>
      <div id="img-drop" class="img-drop" tabindex="0">
        <div class="img-drop-hint">Перетащи файлы, вставь (Ctrl/⌘+V) или <span class="img-pick">выбери с компа</span><br><span style="font-size:11px">можно несколько — уйдут альбомом</span></div>
      </div>
      <input id="p-media-file" type="file" multiple hidden>
      <label>Кнопки</label>
      <div id="p-buttons">${(d.buttons || []).map((b, i) => btnRow(b, i)).join('')}</div>
      <button class="btn" onclick="addBtnRow()">+ кнопка</button>
      <div class="hint-box">
        <b>Подстановки в ссылку и текст кнопки:</b><br>
        <code>{source}</code> — метка последнего перехода: по какой ссылке человек пришёл в <b>последний раз</b>.<br>
        <code>{first_source}</code> — метка первого перехода: откуда он пришёл <b>впервые</b> (не меняется).<br>
        <code>{first_name}</code> — имя, <code>{username}</code> — юзернейм.<br>
        Пример: <code>https://site.com/?utm_source={source}</code> — у пришедшего по ссылке
        с меткой <code>fb</code> откроется <code>…utm_source=fb</code>.<br>
        <span style="color:#7a8499">Ссылку с меткой сгенерируй на экране бота — кнопка «🔗 Deep-link».</span>
      </div>`;
  } else if (node.name === 'note') {
    html += `
      <label>Текст заметки (не отправляется подписчикам)</label>
      <textarea id="p-note" rows="5">${esc(d.text || '')}</textarea>`;
  } else if (node.name === 'delay') {
    html += `
      <label>Ждать</label>
      <input id="p-amount" type="number" min="1" value="${esc(d.amount || 1)}">
      <label>Единица</label>
      <select id="p-unit">
        <option value="seconds" ${d.unit === 'seconds' ? 'selected' : ''}>секунд</option>
        <option value="minutes" ${d.unit === 'minutes' ? 'selected' : ''}>минут</option>
        <option value="hours" ${d.unit === 'hours' ? 'selected' : ''}>часов</option>
        <option value="days" ${d.unit === 'days' ? 'selected' : ''}>дней</option>
      </select>`;
  } else if (node.name === 'condition') {
    html += `
      <label>Если у подписчика есть тег…</label>
      <select id="p-tag" class="tag-select" onchange="maybeCreateTag(this)">${tagOptions(d.tag)}</select>
      <p style="font-size:12px;color:#7a8499;margin-top:8px">Выход 1 — «да», выход 2 — «нет».</p>`;
  } else if (node.name === 'filter') {
    html += `
      <p style="font-size:12.5px;color:#7a8499;margin-bottom:8px">
        Выход 1 — подходит под условия, выход 2 — не подходит.
        Условия те же, что в сегментах рассылок, плюс три про сам момент
        срабатывания: день недели, дата и время.
      </p>
      <div id="p-filter"></div>`;
  } else if (node.name === 'language') {
    const langs = d.languages || [];
    html += `
      <p style="font-size:12.5px;color:#7a8499;margin-bottom:8px">
        Развилка по языку Telegram-профиля. Определяется автоматически —
        подписчика ни о чём не спрашиваем.
      </p>
      <label>Ветки (код языка; несколько через запятую)</label>
      <div id="p-langs">${langs.map(l => langRow(l)).join('')}</div>
      <select id="p-lang-pick" onchange="addLangRow(this.value); this.value=''">
        <option value="">+ добавить язык из списка…</option>
        ${languageOptions()}
      </select>
      <button class="btn" onclick="addLangRow()">+ пустая ветка</button>
      <div class="hint-box">
        Коды — как в Telegram: <code>ru</code>, <code>en</code>, <code>uk</code>,
        <code>pl</code>… Подписчик с <code>pt-br</code> попадёт в ветку
        <code>pt</code>. В одну ветку можно несколько кодов:
        <code>ru, uk, be</code>. Кто не совпал ни с одной веткой — уходит
        в выход «остальные» (∗).
      </div>`;
  } else if (node.name === 'chain') {
    const cur = String(d.funnel_id || '');
    const missing = cur && !CHAINS.some(c => String(c.id) === cur);
    html += `
      <label>Какую цепочку запустить</label>
      <select id="p-chain">
        <option value="">— выберите цепочку —</option>
        ${CHAINS.map(c =>
          `<option value="${c.id}" ${cur === String(c.id) ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}
        ${missing ? `<option value="${esc(cur)}" selected>#${esc(cur)} — цепочка удалена</option>` : ''}
      </select>
      ${cur && !missing
        ? `<button class="btn" onclick="openChainEditor('${esc(cur)}')">Открыть цепочку →</button>` : ''}
      ${CHAINS.length ? '' :
        `<div class="hint-box">Цепочек пока нет. Создайте её кнопкой
         «+ Цепочка» в списке воронок — там же, где создаёте воронки.</div>`}
      <div class="hint-box">
        Человек проходит цепочку целиком и возвращается сюда, на выход блока.
        Если цепочка заканчивается ожиданием кнопки — вернётся после нажатия.
        Цепочка идёт под тем же ботом, что и эта воронка.
      </div>`;
  } else if (node.name === 'action') {
    const op = d.op || 'add_tag';
    html += `
      <label>Что сделать</label>
      <select id="p-op" onchange="onActionOpChange()">
        ${ACTION_OPS.map(([v, l]) =>
          `<option value="${v}" ${op === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select>
      <div id="p-action-body">${actionBody(op, d)}</div>`;
  }

  if (node.name !== 'start') {
    html += `<button class="btn primary" onclick="applyProps()">Применить</button>`;
  }
  html += `<button class="btn danger" onclick="deleteSelectedNode()">Удалить блок</button>`;
  props.innerHTML = html;
  props.classList.remove('hidden');
  if (node.name === 'message') {
    // редактор монтируем после вставки разметки — ему нужен живой контейнер
    RT_TEXT = mountRichText(document.getElementById('p-text-rt'), d.text || '',
                            scheduleAutoApply);
    setupImageUploader();
  } else {
    RT_TEXT = null;
  }
  if (node.name === 'filter') {
    NODE_SEG = makeSegment(document.getElementById('p-filter'), d.filter, { nodeOnly: true });
  } else {
    NODE_SEG = null;
  }
  // живое превью: любое изменение в панели свойств применяется автоматически
  if (node.name !== 'start') {
    props.oninput = scheduleAutoApply;
    props.onchange = scheduleAutoApply;
  } else {
    props.oninput = props.onchange = null;
  }
}

let RT_TEXT = null;   // визуальный редактор текущего узла «Сообщение»
let NODE_SEG = null;  // конструктор условий текущего узла «Фильтр»

let _autoApplyTimer = null;
// применить отложенные правки панели прямо сейчас (перед копированием и сохранением)
function flushAutoApply() {
  if (!_autoApplyTimer) return;
  clearTimeout(_autoApplyTimer);
  _autoApplyTimer = null;
  if (selectedNodeId != null && !document.getElementById('props').classList.contains('hidden')) {
    try { applyProps(); } catch (e) { /* панель могла закрыться */ }
  }
}
function scheduleAutoApply() {
  clearTimeout(_autoApplyTimer);
  _autoApplyTimer = setTimeout(() => {
    _autoApplyTimer = null;
    if (selectedNodeId != null && !document.getElementById('props').classList.contains('hidden')) {
      try { applyProps(); } catch (e) { /* панель могла закрыться */ }
    }
  }, 350);
}

// ---------- вложения (мультимедиа) ----------
let MEDIA_ITEMS = [];
const MEDIA_TYPES = [
  ['photo', '🖼 Фото'], ['video', '🎬 Видео'], ['audio', '🎵 Аудио'],
  ['voice', '🎤 Голосовое'], ['video_note', '⭕️ Кружок'], ['document', '📎 Файл'],
];

function mediaThumb(m) {
  const src = m.path.startsWith('http') ? m.path : '/' + m.path;
  if (m.type === 'photo') return `<img class="media-thumb" src="${esc(src)}" alt="">`;
  const icon = { video: '🎬', audio: '🎵', voice: '🎤', video_note: '⭕️', document: '📎' }[m.type] || '📎';
  return `<div class="media-thumb icon">${icon}</div>`;
}

function renderMediaList() {
  const box = document.getElementById('media-list');
  if (!box) return;
  box.innerHTML = MEDIA_ITEMS.map((m, i) => `
    <div class="media-item">
      ${mediaThumb(m)}
      <div class="media-mid">
        <select onchange="MEDIA_ITEMS[${i}].type=this.value;renderMediaList()">
          ${MEDIA_TYPES.map(([v, l]) => `<option value="${v}" ${m.type === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select>
        <div class="media-name">${esc(m.name || m.path.split('/').pop())}</div>
      </div>
      <div class="media-ord">
        <button class="btn" title="выше" onclick="moveMedia(${i},-1)" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="btn" title="ниже" onclick="moveMedia(${i},1)" ${i === MEDIA_ITEMS.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="btn danger" title="убрать" onclick="removeMedia(${i})">✕</button>
      </div>
    </div>`).join('');
}
function moveMedia(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= MEDIA_ITEMS.length) return;
  [MEDIA_ITEMS[i], MEDIA_ITEMS[j]] = [MEDIA_ITEMS[j], MEDIA_ITEMS[i]];
  renderMediaList();
  scheduleAutoApply();
}
function removeMedia(i) { MEDIA_ITEMS.splice(i, 1); renderMediaList(); scheduleAutoApply(); }

async function uploadMediaFiles(files) {
  const drop = document.getElementById('img-drop');
  for (const file of files) {
    if (drop) drop.querySelector('.img-drop-hint').textContent = `загрузка: ${file.name}…`;
    const fd = new FormData();
    fd.append('file', file, file.name || 'pasted.png');
    try {
      const r = await fetch('/api/media/upload', {
        method: 'POST', headers: { 'Authorization': 'Bearer ' + TOKEN }, body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || 'Ошибка загрузки');
      MEDIA_ITEMS.push({ type: data.kind, path: data.path, name: data.name });
      renderMediaList();
      scheduleAutoApply();
    } catch (e) { alert(e.message); }
  }
  if (drop) drop.querySelector('.img-drop-hint').innerHTML =
    'Перетащи файлы, вставь (Ctrl/⌘+V) или <span class="img-pick">выбери с компа</span><br><span style="font-size:11px">можно несколько — уйдут альбомом</span>';
}

function setupImageUploader() {
  renderMediaList();
  const drop = document.getElementById('img-drop');
  const fileInput = document.getElementById('p-media-file');
  if (!drop) return;
  drop.onclick = () => fileInput.click();
  fileInput.onchange = () => { if (fileInput.files.length) uploadMediaFiles([...fileInput.files]); fileInput.value = ''; };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('drag'); };
  drop.ondragleave = () => drop.classList.remove('drag');
  drop.ondrop = e => {
    e.preventDefault(); drop.classList.remove('drag');
    if (e.dataTransfer.files.length) uploadMediaFiles([...e.dataTransfer.files]);
  };
  drop.onpaste = e => {
    const imgs = [...(e.clipboardData.items || [])].filter(i => i.type.startsWith('image/')).map(i => i.getAsFile());
    if (imgs.length) { e.preventDefault(); uploadMediaFiles(imgs); }
  };
}

// Список языков берём оттуда же, откуда его берёт фильтр, — из справочника
// полей сегмента. Один список на весь сервис: разъехавшиеся наборы языков
// в двух местах сразу превращаются в «почему тут есть, а там нет».
function languageList() {
  const fields = (typeof SEG_FIELDS !== 'undefined' && SEG_FIELDS) || [];
  const f = fields.find(x => x.key === 'language');
  return (f && f.options) || [];
}

function languageOptions() {
  return languageList().map(o =>
    `<option value="${esc(o.v)}">${esc(o.l)} (${esc(o.v)})</option>`).join('');
}

function languageName(code) {
  const c = String(code || '').trim().toLowerCase();
  const o = languageList().find(x => String(x.v) === c);
  return o ? o.l : code;
}

function langRow(value) {
  return `<div class="btn-row-item">
    <input placeholder="ru или ru, uk" class="p-lang" value="${esc(value || '')}">
    <button class="btn danger" onclick="this.parentElement.remove();scheduleAutoApply()">✕</button>
  </div>`;
}
function addLangRow(code) {
  const box = document.getElementById('p-langs');
  if (code) {
    // не дублируем уже добавленный язык
    const have = [...box.querySelectorAll('.p-lang')].some(inp =>
      inp.value.split(',').map(x => x.trim().toLowerCase()).includes(code));
    if (have) return;
  }
  box.insertAdjacentHTML('beforeend', langRow(code || ''));
  if (code) scheduleAutoApply();
}

// Стили кнопок из Bot API 9.4. Это не палитра, а три готовых вида;
// клиенты постарше нарисуют обычную кнопку и ничего не сломают.
const BTN_STYLES = [
  ['', 'обычная'], ['primary', '🔵 основная'],
  ['success', '🟢 зелёная'], ['danger', '🔴 красная'],
];

function btnRow(b, i) {
  return `<div class="btn-row">
    <div class="btn-row-item">
      ${btnIconPicker(b.icon_custom_emoji_id)}
      <input placeholder="Текст кнопки" class="p-btn-label" value="${esc(b.label || '')}">
      <button class="btn danger" title="убрать кнопку"
        onclick="this.closest('.btn-row').remove();scheduleAutoApply()">✕</button>
    </div>
    <div class="btn-row-item">
      <input placeholder="URL (пусто = ветка воронки)" class="p-btn-url" value="${esc(b.url || '')}">
      <select class="p-btn-style" title="вид кнопки">
        ${BTN_STYLES.map(([v, l]) =>
          `<option value="${v}" ${(b.style || '') === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select>
      <label class="btn-off" title="кнопка видна, но нажать нельзя">
        <input type="checkbox" class="p-btn-disabled" ${b.disabled ? 'checked' : ''}> выкл
      </label>
    </div>
  </div>`;
}
function addBtnRow() {
  document.getElementById('p-buttons').insertAdjacentHTML('beforeend', btnRow({}, 0));
}

function applyProps() {
  const node = editor.getNodeFromId(selectedNodeId);
  const d = { ...node.data };

  if (node.name === 'message') {
    d.text = RT_TEXT ? RT_TEXT.getHtml() : (d.text || '');
    d.text_first = document.getElementById('p-order').value === '1';
    d.media = MEDIA_ITEMS.map(m => ({ type: m.type, path: m.path, name: m.name || '' }));
    d.photo_url = '';  // старое поле больше не используем (медиа в d.media)
    d.buttons = [...document.querySelectorAll('#p-buttons .btn-row')].map(row => {
      const b = {
        label: row.querySelector('.p-btn-label').value,
        url: row.querySelector('.p-btn-url').value || undefined,
      };
      const style = row.querySelector('.p-btn-style').value;
      if (style) b.style = style;
      const icon = row.querySelector('.btn-icon-pick').dataset.icon;
      if (icon) b.icon_custom_emoji_id = icon;
      if (row.querySelector('.p-btn-disabled').checked) b.disabled = true;
      return b;
    }).filter(b => b.label);
    // выходы: 1 ("далее") + по одному на каждую callback-кнопку
    const need = 1 + d.buttons.filter(b => !b.url).length;
    const cur = Object.keys(editor.getNodeFromId(selectedNodeId).outputs).length;
    for (let i = cur; i < need; i++) editor.addNodeOutput(selectedNodeId);
    for (let i = cur; i > need; i--) editor.removeNodeOutput(selectedNodeId, 'output_' + i);
  } else if (node.name === 'note') {
    d.text = document.getElementById('p-note').value;
  } else if (node.name === 'delay') {
    d.amount = +document.getElementById('p-amount').value || 1;
    d.unit = document.getElementById('p-unit').value;
  } else if (node.name === 'condition') {
    d.tag = cleanTag(document.getElementById('p-tag').value, d.tag);
  } else if (node.name === 'filter') {
    if (NODE_SEG) d.filter = NODE_SEG.getFilter();
  } else if (node.name === 'language') {
    d.languages = [...document.querySelectorAll('#p-langs .p-lang')]
      .map(inp => inp.value.trim()).filter(Boolean);
    // выходы: 1 («остальные») + по одному на каждую ветку языка
    const need = 1 + d.languages.length;
    const cur = Object.keys(editor.getNodeFromId(selectedNodeId).outputs).length;
    for (let i = cur; i < need; i++) editor.addNodeOutput(selectedNodeId);
    for (let i = cur; i > need; i--) editor.removeNodeOutput(selectedNodeId, 'output_' + i);
  } else if (node.name === 'chain') {
    d.funnel_id = document.getElementById('p-chain').value;
  } else if (node.name === 'action') {
    d.op = document.getElementById('p-op').value;
    const tagSel = document.getElementById('p-tag');
    if (tagSel) d.tag = cleanTag(tagSel.value, d.tag);
    const chan = document.getElementById('p-channel');
    if (chan) d.channel = chan.value.trim();
    const target = document.getElementById('p-target');
    if (target) d.target = target.value;
    // «проверить подписку» — развилка на два выхода, остальные операции — один
    const need = ACTION_OUTPUTS[d.op] || 1;
    const cur = Object.keys(editor.getNodeFromId(selectedNodeId).outputs).length;
    for (let i = cur; i < need; i++) editor.addNodeOutput(selectedNodeId);
    for (let i = cur; i > need; i--) editor.removeNodeOutput(selectedNodeId, 'output_' + i);
  }

  editor.updateNodeDataFromId(selectedNodeId, d);
  refreshNodeHtml(selectedNodeId);
}

// «__new__» — это пункт меню, а не тег: если пользователь открыл создание и
// отменил его, в данные должно вернуться прежнее значение
function cleanTag(value, prev) {
  return value === '__new__' ? (prev || '') : value;
}

function deleteSelectedNode() {
  if (selectedNodeId != null) editor.removeNodeId('node-' + selectedNodeId);
  hideProps();
}

function hideProps() {
  selectedNodeId = null;
  document.getElementById('props').classList.add('hidden');
}

// ---------- AI-чат воронки ----------
const AI_CHAT_HISTORY = {};  // funnelId -> [{role, content}]

function toggleAiChat() {
  const el = document.getElementById('ai-chat');
  el.classList.toggle('hidden');
  if (!el.classList.contains('hidden')) {
    setupAiChatShots();
    renderAiChat();
    document.getElementById('ai-chat-text').focus();
  }
}

function renderAiChat() {
  const box = document.getElementById('ai-chat-msgs');
  const hist = AI_CHAT_HISTORY[currentFunnelId] || [];
  if (!box._intro) box._intro = box.children[0]?.outerHTML || '';
  box.innerHTML = box._intro + hist.map(m =>
    `<div class="aim ${m.role}">${(m.images || []).length ? `<div class="aim-shots">${
      m.images.map(im => `<img src="/${esc(im.path)}" alt="">`).join('')}</div>` : ''}${esc(m.content)}</div>`).join('');
  box.scrollTop = box.scrollHeight;
  renderAiChatShots();
}

// ---------- скриншоты в AI-чате ----------
// Пример того, как должно выглядеть, объяснить словами трудно — проще
// показать. Картинки грузятся сразу (тот же /ai/screens, что у сборки по
// скриншотам) и уходят модели вместе со следующим сообщением.
const AI_CHAT_MAX_SHOTS = 4;
let AI_CHAT_SHOTS = [];   // [{path, name}]

function renderAiChatShots() {
  const box = document.getElementById('ai-chat-shots');
  if (!box) return;
  box.classList.toggle('hidden', !AI_CHAT_SHOTS.length);
  box.innerHTML = AI_CHAT_SHOTS.map((im, i) => `
    <div class="ai-shot"><img src="/${esc(im.path)}" alt="">
      <button type="button" title="убрать" onclick="AI_CHAT_SHOTS.splice(${i},1);renderAiChatShots()">✕</button></div>`).join('');
}

async function aiChatAttach(files) {
  files = files.filter(f => /^image\/(png|jpeg|webp|gif)$/.test(f.type));
  if (!files.length) return;
  const room = AI_CHAT_MAX_SHOTS - AI_CHAT_SHOTS.length;
  if (room <= 0) { alert(`Не больше ${AI_CHAT_MAX_SHOTS} скриншотов к одному сообщению`); return; }
  files = files.slice(0, room);
  const fd = new FormData();
  // у вставки из буфера нет имени — даём, иначе сервер не поймёт формат
  files.forEach((f, i) => fd.append('files', f,
    f.name && /\.\w+$/.test(f.name) ? f.name : `screen${i}.${(f.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`));
  const box = document.getElementById('ai-chat-shots');
  box.classList.remove('hidden');
  box.insertAdjacentHTML('beforeend', '<div class="ai-shot loading">загрузка…</div>');
  try {
    const r = await fetch('/api/ai/screens', {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + TOKEN }, body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || 'Не удалось загрузить картинку');
    AI_CHAT_SHOTS = AI_CHAT_SHOTS.concat(data.images.map(im => ({ path: im.path, name: im.name })));
  } catch (e) { alert(e.message); }
  renderAiChatShots();
}

function setupAiChatShots() {
  const chat = document.getElementById('ai-chat');
  if (!chat || chat._shotsReady) return;
  chat._shotsReady = true;
  // Ctrl/⌘+V картинки — прямо в поле ввода
  document.getElementById('ai-chat-text').addEventListener('paste', e => {
    const files = [...(e.clipboardData || {}).files || []].filter(f => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); aiChatAttach(files); }
  });
  // перетащить в окно чата
  chat.addEventListener('dragover', e => {
    if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); chat.classList.add('drag'); }
  });
  chat.addEventListener('dragleave', e => { if (!chat.contains(e.relatedTarget)) chat.classList.remove('drag'); });
  chat.addEventListener('drop', e => {
    if (![...e.dataTransfer.types].includes('Files')) return;
    e.preventDefault();
    chat.classList.remove('drag');
    aiChatAttach([...e.dataTransfer.files]);
  });
}

async function aiChatSend() {
  const ta = document.getElementById('ai-chat-text');
  let text = ta.value.trim();
  const shots = AI_CHAT_SHOTS;
  if (!text && !shots.length) return;
  if (!text) text = 'Сделай как на скриншоте';
  ta.value = '';
  AI_CHAT_SHOTS = [];
  flushAutoApply();
  const hist = (AI_CHAT_HISTORY[currentFunnelId] = AI_CHAT_HISTORY[currentFunnelId] || []);
  hist.push({ role: 'user', content: text, images: shots });
  renderAiChat();
  const box = document.getElementById('ai-chat-msgs');
  box.insertAdjacentHTML('beforeend', '<div class="aim assistant typing">думаю…</div>');
  box.scrollTop = box.scrollHeight;
  document.getElementById('ai-chat-send').disabled = true;
  try {
    const r = await api('/ai/edit', { method: 'POST', body: {
      funnel_id: currentFunnelId,
      // картинки модель получает только к последнему сообщению — в истории
      // остаётся пометка, что они были
      messages: hist.map(m => ({ role: m.role, content: m.content, images: (m.images || []).length })),
      image_paths: shots.map(im => im.path),
      // то, что на холсте сейчас, с несохранёнными правками
      graph_ui: editor.export(),
    } });
    hist.push({ role: 'assistant', content: r.reply });
    renderAiChat();
    if (r.updated) {
      // граф приходит прямо в ответе — рисуем сразу, без второго запроса
      const f = r.funnel || await api('/funnels/' + currentFunnelId);
      applyFunnelToCanvas(f);
      setTimeout(() => {
        EDITOR_SNAPSHOT = editorStateJson();
        if (r.layout) arrangeFunnel(r.layout);   // раскладка — поверх сохранённой
      }, 120);
      flashStatus('Воронка обновлена ✅');
    } else if (r.layout) {
      // только расположение: двигаем карточки, содержимое не трогаем.
      // На сервер не уходит само — это обычная несохранённая правка
      arrangeFunnel(r.layout);
    }
  } catch (e) {
    // картинки не пропадают: можно поправить запрос и отправить снова
    if (!AI_CHAT_SHOTS.length) AI_CHAT_SHOTS = shots;
    hist.push({ role: 'assistant', content: '⚠️ ' + (e.message || 'ошибка') });
    renderAiChat();
  } finally {
    document.getElementById('ai-chat-send').disabled = false;
  }
}

// Перерисовать канвас по данным воронки (после правки AI)
function applyFunnelToCanvas(f) {
  if (!f) return;
  document.getElementById('funnel-name').value = f.name || '';
  if (f.trigger_type) {
    document.getElementById('funnel-trigger').value = f.trigger_type;
    if (f.trigger_type === 'keyword') {
      document.getElementById('funnel-trigger-value').value = f.trigger_value || '';
    }
    populateTriggerTag(f.trigger_type, f.trigger_value || '');
    updateTriggerInputs();
  }
  clearMultiSelection();
  hideProps();
  editor.clear();
  if (f.graph_ui && f.graph_ui.drawflow) {
    editor.import(JSON.parse(JSON.stringify(f.graph_ui)));  // import мутирует объект
  }
  // порты, статистика и связи — после отрисовки узлов
  setTimeout(() => {
    redrawAllNodes();
    decoratePorts();
    refreshStepNumbers();
    loadFunnelStats();
    Object.keys(editor.drawflow.drawflow.Home.data).forEach(id => {
      try { editor.updateConnectionNodes('node-' + id); } catch (e) {}
    });
  }, 50);
}

// ---------- авторасстановка ----------
// Две раскладки одной и той же структуры:
//   vertical   — шаги идут сверху вниз, ветки (языки, кнопки) — столбцами;
//   horizontal — шаги идут слева направо, ветки — строками сверху вниз.
// Ради столбцов кнопка «Разложить» и появилась: у воронки с десятью языками
// получается десять параллельных дорожек, а не каша. Раньше все узлы одной
// глубины просто выстраивались в ряд, и на длинных ветках соседние дорожки
// перемешивались между собой.
//
// spacing — плотность: compact | normal | wide. Меняет только промежутки,
// карточки остаются своего размера.
const LAYOUT_SPACING = { compact: 0.6, normal: 1, wide: 1.6 };

function arrangeVertical() { arrangeFunnel({ direction: 'vertical' }); }

function arrangeFunnel(opts) {
  opts = opts || {};
  const direction = opts.direction === 'horizontal' ? 'horizontal' : 'vertical';
  const k = LAYOUT_SPACING[opts.spacing] || 1;
  const df = editor.export().drawflow.Home.data;
  const flow = {}, notes = {};
  Object.values(df).forEach(n => {
    (n.name === 'note' ? notes : flow)[String(n.id)] = n;
  });

  // потомки в порядке портов: output_1, output_2 … — это порядок веток
  // на карточке, и дорожки должны идти так же, как кнопки сверху вниз
  const childrenOf = id => {
    const node = df[id] || {};
    const out = [];
    Object.keys(node.outputs || {})
      .sort((a, b) => (+String(a).split('_')[1] || 0) - (+String(b).split('_')[1] || 0))
      .forEach(port => ((node.outputs[port] || {}).connections || []).forEach(c => {
        const t = String(c.node);
        if (flow[t] && !out.includes(t)) out.push(t);
      }));
    return out;
  };

  const depth = {}, col = {}, groupOf = {};
  let group = 0;

  // Стартуем от «Старта», потом отдельными группами разбираем всё, до чего
  // из него не дойти: оторванный кусок должен лечь после воронки, а не влезть
  // первым рядом в середину (раньше он получал глубину 1 и всё ломал).
  let startId = null;
  Object.values(df).forEach(n => { if (n.name === 'start') startId = String(n.id); });
  const roots = [];
  if (startId && flow[startId]) roots.push(startId);
  Object.keys(flow).sort((a, b) => +a - +b).forEach(id => roots.push(id));

  roots.forEach(root => {
    if (depth[root] !== undefined) return;
    const g = group++;
    depth[root] = 0;
    groupOf[root] = g;
    const queue = [root];
    while (queue.length) {
      const cur = queue.shift();
      childrenOf(cur).forEach(t => {
        if (depth[t] === undefined) {
          depth[t] = depth[cur] + 1;
          groupOf[t] = g;
          queue.push(t);
        }
      });
    }
    // первая ветка продолжает дорожку родителя, каждая следующая встаёт
    // за всем, что заняло предыдущее поддерево — так дорожки не лезут
    // друг на друга даже при разной длине веток
    (function lanes(id, c) {
      col[id] = c;
      let max = c;
      childrenOf(id).forEach((t, i) => {
        if (col[t] !== undefined) return;   // ветки сошлись — узел уже размещён
        max = Math.max(max, lanes(t, i === 0 ? c : max + 1));
      });
      return max;
    })(root, 0);
  });

  // заметки живут рядом со своим блоком
  const notesByAbout = {};
  const homeless = [];
  Object.entries(notes).forEach(([id, n]) => {
    const about = String((n.data || {}).about || '');
    if (flow[about]) (notesByAbout[about] = notesByAbout[about] || []).push(id);
    else homeless.push(id);
  });

  const sizeOf = id => {
    const el = document.getElementById('node-' + id);
    return { w: (el && el.offsetWidth) || 240, h: (el && el.offsetHeight) || 120 };
  };
  const place = (id, x, yy) => {
    const el = document.getElementById('node-' + id);
    if (!el) return;
    el.style.left = x + 'px';
    el.style.top = yy + 'px';
    const n = editor.drawflow.drawflow.Home.data[id];
    if (n) { n.pos_x = x; n.pos_y = yy; }
  };

  const GROUP_GAP = Math.round(140 * k);
  let farEdge = 60;   // куда класть заметки, потерявшие свой блок

  if (direction === 'vertical') {
    // глубина — ряд, дорожка — столбец
    const X_STEP = Math.round(Math.max(...Object.keys(df).map(id => sizeOf(id).w), 220) + 180 * k);
    const Y_GAP = Math.round(70 * k);
    const rows = {};                       // "группа:глубина" -> [id]
    Object.keys(flow).forEach(id => {
      const key = groupOf[id] + ':' + depth[id];
      (rows[key] = rows[key] || []).push(id);
    });
    Object.values(rows).forEach(arr => arr.sort((a, b) => col[a] - col[b] || +a - +b));

    let y = 60, prevGroup = 0;
    Object.keys(rows).sort((a, b) => {
      const [ga, da] = a.split(':').map(Number);
      const [gb, db] = b.split(':').map(Number);
      return ga - gb || da - db;
    }).forEach(key => {
      const g = +key.split(':')[0];
      if (g !== prevGroup) { y += GROUP_GAP; prevGroup = g; }
      let maxH = 0;
      let freeCol = Math.max(...rows[key].map(id => col[id])) + 1;
      rows[key].forEach(id => {
        place(id, 60 + col[id] * X_STEP, y);
        maxH = Math.max(maxH, sizeOf(id).h);
        (notesByAbout[id] || []).forEach(nid => {
          place(nid, 60 + freeCol++ * X_STEP, y);
          maxH = Math.max(maxH, sizeOf(nid).h);
        });
      });
      y += maxH + Y_GAP;
    });
    farEdge = y + GROUP_GAP;
    homeless.forEach((id, i) => place(id, 60 + i * X_STEP, farEdge));
  } else {
    // глубина — столбец, дорожка — строка; высота строки — по самой
    // высокой карточке в ней (вместе с заметками, которые висят под блоком)
    const X_STEP = Math.round(Math.max(...Object.keys(flow).map(id => sizeOf(id).w), 220) + 120 * k);
    const Y_GAP = Math.round(60 * k);
    const NOTE_GAP = Math.round(16 * k);
    const lanes = {};                      // "группа:дорожка" -> [id]
    Object.keys(flow).forEach(id => {
      const key = groupOf[id] + ':' + col[id];
      (lanes[key] = lanes[key] || []).push(id);
    });
    let y = 60, prevGroup = 0;
    Object.keys(lanes).sort((a, b) => {
      const [ga, ca] = a.split(':').map(Number);
      const [gb, cb] = b.split(':').map(Number);
      return ga - gb || ca - cb;
    }).forEach(key => {
      const g = +key.split(':')[0];
      if (g !== prevGroup) { y += GROUP_GAP; prevGroup = g; }
      let laneH = 0;
      lanes[key].forEach(id => {
        const x = 60 + depth[id] * X_STEP;
        place(id, x, y);
        let h = sizeOf(id).h;
        (notesByAbout[id] || []).forEach(nid => {
          place(nid, x, y + h + NOTE_GAP);
          h += NOTE_GAP + sizeOf(nid).h;
        });
        laneH = Math.max(laneH, h);
      });
      y += laneH + Y_GAP;
    });
    farEdge = y + GROUP_GAP;
    homeless.forEach((id, i) => place(id, 60 + i * X_STEP, farEdge));
  }

  Object.keys(df).forEach(id => {
    try { editor.updateConnectionNodes('node-' + id); } catch (e) {}
  });
  decoratePorts();
  refreshStepNumbers();
  if (typeof syncQuickActions === 'function') syncQuickActions();
  flashStatus(direction === 'vertical' ? 'Разложено столбцами' : 'Разложено строками');
}

// Меню у кнопки «Разложить»: столбцами или строками
function toggleArrangeMenu(btn) {
  let m = document.getElementById('arrange-menu');
  if (m) { m.remove(); return; }
  m = document.createElement('div');
  m.id = 'arrange-menu';
  m.className = 'arrange-menu';
  m.innerHTML = `
    <button type="button" data-d="vertical">⬇️ Столбцами <small>шаги сверху вниз, ветки рядом</small></button>
    <button type="button" data-d="horizontal">➡️ Строками <small>шаги слева направо, ветки друг под другом</small></button>
    <label><input type="checkbox" id="arrange-wide"> просторнее</label>`;
  const r = btn.getBoundingClientRect();
  m.style.left = r.left + 'px';
  m.style.top = (r.bottom + 4) + 'px';
  document.body.appendChild(m);
  m.querySelectorAll('[data-d]').forEach(b => b.onclick = () => {
    const wide = m.querySelector('#arrange-wide').checked;
    m.remove();
    arrangeFunnel({ direction: b.dataset.d, spacing: wide ? 'wide' : 'normal' });
  });
  setTimeout(() => document.addEventListener('mousedown', function off(e) {
    if (!m.contains(e.target) && e.target !== btn) { m.remove(); document.removeEventListener('mousedown', off); }
  }), 0);
}

// ---------- сохранение ----------
// silent — для автосохранения: без всплывающих окон, итог пишется рядом с
// кнопкой «Сохранить». Возвращает true, если сохранилось.
async function saveFunnel(silent) {
  flushAutoApply();   // правки панели, которые ещё не успели примениться
  const trigger = document.getElementById('funnel-trigger').value;
  let triggerValue = null;
  if (trigger === 'keyword') triggerValue = document.getElementById('funnel-trigger-value').value.trim();
  if (trigger === 'tag_added') {
    triggerValue = cleanTag(document.getElementById('funnel-trigger-tag').value, null);
  }
  if (trigger === 'message') {
    // необязательный тег «кроме»: пустое значение = срабатывать всегда
    const v = cleanTag(document.getElementById('funnel-trigger-tag').value, null);
    triggerValue = v || null;
  }
  const botIds = [...document.querySelectorAll('#funnel-bots .pill.on')].map(p => +p.dataset.id);

  const body = {
    name: document.getElementById('funnel-name').value || 'Без названия',
    trigger_type: trigger,
    trigger_value: triggerValue,
    graph_ui: editor.export(),
    bot_ids: botIds,
  };
  const state = editorStateJson();
  const funnelId = currentFunnelId;
  if (silent) {
    // не через api(): тот показывает alert, а автосохранение не должно
    // выскакивать окном посреди работы
    try {
      const r = await fetch('/api/funnels/' + funnelId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || ('ошибка ' + r.status));
      }
    } catch (e) {
      saveDraft();
      setAutosaveStatus(`⚠️ не сохранено: ${e.message}`, 'warn',
        `Не сохранилось на сервер: ${e.message}. Изменения лежат черновиком в браузере.`);
      return false;
    }
  } else {
    try {
      await api('/funnels/' + funnelId, { method: 'PUT', body });
    } catch (e) { return false; /* alert уже показан */ }
    flashStatus('Сохранено ✅');
  }
  if (funnelId !== currentFunnelId) return true;   // пока сохраняли, открыли другую
  EDITOR_SNAPSHOT = state;
  dropDraft(funnelId);
  setAutosaveStatus(silent ? 'автосохранено ' + hhmm() : 'сохранено ' + hhmm());
  return true;
}

// ---------- автосохранение ----------
// Раз в минуту несохранённые изменения сохраняются сами. Но не везде на
// сервер: включённая воронка и цепочка работают на живых подписчиках, и
// полуготовая правка ушла бы людям прямо посреди редактирования. Их
// изменения минута за минутой ложатся черновиком в браузер, а к людям
// попадают только по кнопке «Сохранить». Черновик пишется всегда — он же
// страхует от ошибки сервера и закрытой вкладки — и предлагается к
// восстановлению при следующем открытии воронки.
const AUTOSAVE_MS = 60 * 1000;
let FUNNEL_LIVE = false;   // включена или цепочка — сервер только вручную
let _autosaving = false;

function hhmm() {
  return new Date().toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
}

function setAutosaveStatus(text, kind, hint) {
  const el = document.getElementById('autosave-status');
  if (!el) return;
  el.textContent = text;
  el.title = hint || text;
  el.className = 'autosave-status' + (kind ? ' ' + kind : '');
}

function draftKey(id) { return 'sb_funnel_draft_' + id; }

function saveDraft() {
  if (currentFunnelId == null || !editor) return;
  const state = editorStateJson();
  if (!state) return;
  try {
    localStorage.setItem(draftKey(currentFunnelId),
      JSON.stringify({ state, base: EDITOR_SNAPSHOT, at: Date.now() }));
  } catch (e) { /* хранилище недоступно или переполнено — черновика не будет */ }
}

function dropDraft(id) {
  try { localStorage.removeItem(draftKey(id)); } catch (e) {}
}

function readDraft(id) {
  try { return JSON.parse(localStorage.getItem(draftKey(id)) || 'null'); } catch (e) { return null; }
}

async function autosaveTick() {
  if (_autosaving || !editor) return;
  if (document.getElementById('page-editor').classList.contains('hidden')) return;
  flushAutoApply();
  if (!editorDirty()) return;
  _autosaving = true;
  try {
    if (FUNNEL_LIVE) {
      saveDraft();
      setAutosaveStatus(`черновик ${hhmm()}`, 'draft',
        'Воронка включена: изменения сохранены черновиком в браузере. Подписчики увидят их только после «Сохранить».');
    } else {
      await saveFunnel(true);
    }
  } finally { _autosaving = false; }
}
let _autosaveTimer = null;   // заводится при первом открытии редактора

// Есть черновик, отличающийся от того, что пришло с сервера — предлагаем.
function offerDraft() {
  const bar = document.getElementById('draft-bar');
  if (!bar) return;
  bar.classList.add('hidden');
  const d = readDraft(currentFunnelId);
  if (!d || !d.state || d.state === EDITOR_SNAPSHOT) { if (d) dropDraft(currentFunnelId); return; }
  // Воронку после черновика сохранили (другой вкладкой или человеком) —
  // черновик устарел, восстанавливать его поверх новой версии опасно
  const changedOnServer = d.base && d.base !== EDITOR_SNAPSHOT;
  const when = new Date(d.at).toLocaleString('ru', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  bar.innerHTML = `
    <span>📝 Есть несохранённые изменения от ${when}${changedOnServer
      ? ' — <b>но воронку с тех пор уже сохраняли</b>, восстановление заменит более новую версию' : ''}.</span>
    <button class="btn primary" onclick="restoreDraft()">Восстановить</button>
    <button class="btn" onclick="discardDraft()">Удалить черновик</button>`;
  bar.classList.remove('hidden');
}

function restoreDraft() {
  const d = readDraft(currentFunnelId);
  document.getElementById('draft-bar').classList.add('hidden');
  if (!d) return;
  let st;
  try { st = JSON.parse(d.state); } catch (e) { return; }
  applyFunnelToCanvas({ name: st.name, trigger_type: st.trigger,
    trigger_value: st.trigger === 'keyword' ? st.triggerValue : st.triggerTag, graph_ui: st.graph });
  const on = new Set(st.bots || []);
  document.querySelectorAll('#funnel-bots .pill').forEach(pill =>
    pill.classList.toggle('on', on.has(pill.dataset.id)));
  // снимок не трогаем: восстановленное — это несохранённые изменения
  setAutosaveStatus('черновик восстановлен', 'draft', 'Черновик восстановлен — чтобы применить, нажмите «Сохранить».');
}

function discardDraft() {
  dropDraft(currentFunnelId);
  document.getElementById('draft-bar').classList.add('hidden');
}

// ---------- мультивыделение + копирование/вставка ----------
const CLIPBOARD_KEY = 'sb_node_clipboard';
let multiSelection = new Set();
let shiftHeld = false;
let lastClickCtrl = false;   // Ctrl/⌘ в момент mousedown — для выделения по одному

function onNodeSelected(id) {
  id = String(id);
  if (lastClickCtrl) {
    // Ctrl/⌘+клик — добавить/убрать из выделения, как в файловых менеджерах
    if (multiSelection.has(id)) multiSelection.delete(id);
    else multiSelection.add(id);
  } else if (shiftHeld) {
    multiSelection.add(id);
  } else if (multiSelection.size > 1 && multiSelection.has(id)) {
    // клик по блоку из выделенной группы — группу не сбрасываем (перед перетаскиванием)
  } else {
    clearMultiSelection();
    multiSelection.add(id);
  }
  paintSelection();
}

function clearMultiSelection() {
  multiSelection.clear();
  paintSelection();
}

function paintSelection() {
  document.querySelectorAll('.drawflow-node.multi-sel').forEach(el => el.classList.remove('multi-sel'));
  multiSelection.forEach(id => {
    const el = document.getElementById('node-' + id);
    if (el) el.classList.add('multi-sel');
  });
}

// Печатает ли человек прямо сейчас. Кроме полей ввода сюда обязан попадать
// contenteditable: визуальный редактор текста живёт в нём, и без этой
// проверки пробел уходил на панорамирование холста, Backspace удалял
// выделенный блок вместо буквы, а Ctrl+C копировал узлы вместо текста.
function isTypingIn(el) {
  if (!el) return false;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName || '')) return true;
  if (el.isContentEditable) return true;
  return !!(el.closest && el.closest('[contenteditable="true"]'));
}

// Сочетание Ctrl/⌘+буква на любой раскладке. e.key на русской раскладке —
// кириллица («с» вместо «c»), поэтому Ctrl+C, Ctrl+V и Ctrl+S молча не
// срабатывали, стоило переключить язык. e.code — физическая клавиша, он от
// раскладки не зависит; e.key оставлен для раскладок вроде Dvorak.
function hotkey(e, letter) {
  return e.code === 'Key' + letter.toUpperCase() || (e.key || '').toLowerCase() === letter;
}

function setupClipboard() {
  document.getElementById('drawflow').addEventListener('mousedown', e => {
    lastClickCtrl = e.ctrlKey || e.metaKey;
  }, true);
  document.addEventListener('keydown', e => {
    shiftHeld = e.shiftKey;
    // работаем только когда открыт редактор и фокус не в поле ввода
    if (document.getElementById('page-editor').classList.contains('hidden')) return;
    const inField = isTypingIn(e.target);
    const mod = e.ctrlKey || e.metaKey;
    // Ctrl/⌘+S сохраняет даже из поля ввода — рука сама тянется
    if (mod && hotkey(e, 's')) { e.preventDefault(); saveFunnel(); return; }
    if (inField) return;
    if (mod && hotkey(e, 'c')) { e.preventDefault(); copyNodes(); }
    else if (mod && hotkey(e, 'v')) { e.preventDefault(); pasteNodes(); }
    else if (mod && hotkey(e, 'd')) {
      e.preventDefault();   // дубль — мимо буфера, Ctrl/⌘+C не затирается
      duplicateNodes(multiSelection.size ? [...multiSelection]
        : (selectedNodeId != null ? [String(selectedNodeId)] : []));
    }
    else if (e.key === 'Delete' || e.key === 'Backspace') {
      // 1) выделенная связь; 2) мультивыделение; 3) одиночный блок
      if (editor && editor.connection_selected) {
        e.preventDefault();
        try { editor.removeConnection(); flashStatus('Связь удалена'); } catch (err) {}
      } else if (multiSelection.size > 1) {
        e.preventDefault();
        deleteSelectedNodes();
      } else if (editor && editor.node_selected) {
        e.preventDefault();
        editor.removeNodeId(editor.node_selected.id);
        editor.node_selected = null;   // иначе Drawflow держит удалённый блок «выделенным»
        hideProps();
        clearMultiSelection();
      }
    }
  });
  document.addEventListener('keyup', e => { shiftHeld = e.shiftKey; });
  setupCanvasNav();
  setupMarquee();
  setupGroupDrag();
  setupPaletteDnD();
  setupMagnetConnections();
  setupLinkDropMenu();

  // вставка картинки из буфера, когда открыт блок «Сообщение» (в любом месте редактора)
  document.addEventListener('paste', e => {
    if (document.getElementById('page-editor').classList.contains('hidden')) return;
    if (!document.getElementById('img-drop')) return;  // открыт не «Сообщение»
    const imgs = [...(e.clipboardData?.items || [])].filter(i => i.type.startsWith('image/')).map(i => i.getAsFile());
    if (imgs.length) { e.preventDefault(); uploadMediaFiles(imgs); }
  });
}

// удалить все блоки из мультивыделения (кроме «Старта» — защита от случайности)
function deleteSelectedNodes() {
  const ids = [...multiSelection];
  if (!ids.length) return;
  let skippedStart = false, removed = 0;
  ids.forEach(id => {
    const node = editor.getNodeFromId(id);
    if (!node) return;
    if (node.name === 'start') { skippedStart = true; return; }
    editor.removeNodeId('node-' + id);
    removed++;
  });
  clearMultiSelection();
  hideProps();
  flashStatus(`Удалено блоков: ${removed}` + (skippedStart ? ' (Старт не удаляю)' : ''));
}

// ---------- навигация по холсту (тачпад/колесо) ----------
// Двумя пальцами — прокрутка холста, Ctrl/⌘+колесо (щипок) — зум,
// пробел или средняя кнопка + перетаскивание — тоже панорамирование.
let spaceHeld = false;

function panBy(dx, dy) {
  if (!editor) return;
  editor.canvas_x += dx;
  editor.canvas_y += dy;
  const pc = editor.precanvas;
  pc.style.transform =
    `translate(${editor.canvas_x}px, ${editor.canvas_y}px) scale(${editor.zoom})`;
}

function setupCanvasNav() {
  const container = document.getElementById('drawflow');

  // перехватываем колесо ДО Drawflow (у него на wheel висит зум)
  container.addEventListener('wheel', e => {
    e.preventDefault();
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey) {          // щипок на тачпаде / Ctrl+колесо — зум
      if (e.deltaY < 0) editor.zoom_in(); else editor.zoom_out();
    } else {                                // двумя пальцами — прокрутка холста
      panBy(-e.deltaX, -e.deltaY);
    }
  }, { passive: false, capture: true });

  // пробел = временный режим «рука»
  document.addEventListener('keydown', e => {
    if (e.code === 'Space' && !isTypingIn(e.target)) {
      if (!document.getElementById('page-editor').classList.contains('hidden')) {
        spaceHeld = true;
        container.classList.add('grabbing');
        e.preventDefault();
      }
    }
  });
  document.addEventListener('keyup', e => {
    if (e.code === 'Space') { spaceHeld = false; container.classList.remove('grabbing'); }
  });

  // перетаскивание холста: пробел+ЛКМ или средняя кнопка
  let panning = false, px = 0, py = 0;
  container.addEventListener('mousedown', e => {
    if (e.button === 1 || (spaceHeld && e.button === 0)) {
      panning = true; px = e.clientX; py = e.clientY;
      e.preventDefault(); e.stopPropagation();
    }
  }, true);
  document.addEventListener('mousemove', e => {
    if (!panning) return;
    panBy(e.clientX - px, e.clientY - py);
    px = e.clientX; py = e.clientY;
  });
  document.addEventListener('mouseup', () => { panning = false; });
}

// ---------- рамка выделения (протянуть по пустому фону) ----------
function setupMarquee() {
  const container = document.getElementById('drawflow');
  let box = null, sx = 0, sy = 0, active = false;

  container.addEventListener('mousedown', e => {
    if (e.button !== 0 || spaceHeld) return;   // ЛКМ и не режим «рука»
    // только по пустому фону: не по блоку, не по связи, не по порту
    if (e.target.closest('.drawflow-node') || e.target.closest('svg')) return;
    if (e.target.closest('.df-actions')) return;   // кнопки под блоком и на связи
    if (!e.shiftKey && !e.ctrlKey && !e.metaKey) {
      clearMultiSelection();  // без модификаторов — заново
      // Клик по фону до Drawflow не доходит (ниже stopPropagation), и он не
      // снимал выделение с блока и связи: подсветка и панель оставались.
      dropDrawflowSelection();
    }
    active = true; sx = e.clientX; sy = e.clientY;
    box = document.createElement('div');
    box.className = 'marquee-box';
    document.body.appendChild(box);
    e.preventDefault();
    e.stopPropagation();  // не даём Drawflow начать перетаскивание холста
  }, true);

  document.addEventListener('mousemove', e => {
    if (!active || !box) return;
    Object.assign(box.style, {
      left: Math.min(sx, e.clientX) + 'px',
      top: Math.min(sy, e.clientY) + 'px',
      width: Math.abs(e.clientX - sx) + 'px',
      height: Math.abs(e.clientY - sy) + 'px',
    });
  });

  document.addEventListener('mouseup', () => {
    if (!active || !box) return;
    active = false;
    const r = box.getBoundingClientRect();
    box.remove(); box = null;
    if (r.width < 8 && r.height < 8) return;  // случайный клик
    document.querySelectorAll('#drawflow .drawflow-node').forEach(el => {
      const nr = el.getBoundingClientRect();
      const hit = !(nr.right < r.left || nr.left > r.right || nr.bottom < r.top || nr.top > r.bottom);
      if (hit) multiSelection.add(el.id.replace('node-', ''));
    });
    paintSelection();
    if (multiSelection.size) {
      flashStatus(`Выделено блоков: ${multiSelection.size} — Ctrl/⌘+C копировать, Del удалить`);
    }
  });
}

// ---------- групповое перетаскивание ----------
// Тянешь любой блок из выделенной группы — вся группа едет вместе.
// Drawflow сам двигает захваченный блок, остальные двигаем мы на ту же дельту.
function setupGroupDrag() {
  const container = document.getElementById('drawflow');
  let dragging = false, startX = 0, startY = 0, others = [];

  container.addEventListener('mousedown', e => {
    if (e.shiftKey || e.ctrlKey || e.metaKey) return;  // это выделение, не перетаскивание
    const nodeEl = e.target.closest('.drawflow-node');
    if (!nodeEl) return;
    const id = nodeEl.id.replace('node-', '');
    if (multiSelection.size > 1 && multiSelection.has(id)) {
      dragging = true; startX = e.clientX; startY = e.clientY;
      others = [...multiSelection].filter(x => x !== id).map(x => {
        const el = document.getElementById('node-' + x);
        return el ? { id: x, el, x: parseFloat(el.style.left) || 0, y: parseFloat(el.style.top) || 0 } : null;
      }).filter(Boolean);
    }
  });

  document.addEventListener('mousemove', e => {
    if (!dragging || !others.length) return;
    const z = editor.zoom || 1;
    const dx = (e.clientX - startX) / z;
    const dy = (e.clientY - startY) / z;
    others.forEach(o => {
      o.el.style.left = (o.x + dx) + 'px';
      o.el.style.top = (o.y + dy) + 'px';
      try { editor.updateConnectionNodes('node-' + o.id); } catch (err) {}
    });
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    // фиксируем новые координаты в данных Drawflow (иначе не сохранятся)
    others.forEach(o => {
      const n = editor.drawflow.drawflow.Home.data[o.id];
      if (n) {
        n.pos_x = parseFloat(o.el.style.left) || 0;
        n.pos_y = parseFloat(o.el.style.top) || 0;
      }
    });
    others = [];
    paintSelection();  // группа остаётся выделенной
  });
}

// ---------- «магнитные» связи ----------
// Соединение делает сам Drawflow (force_first_input): бросил на карточку —
// прицепилось к входу. Здесь только подсветка карточки-цели, пока тянешь.
function setupMagnetConnections() {
  const container = document.getElementById('drawflow');
  let hovered = null;

  function clearHover() {
    if (hovered) { hovered.classList.remove('magnet-target'); hovered = null; }
  }

  container.addEventListener('mousemove', e => {
    if (!editor || !editor.connection) { clearHover(); return; }
    const nodeEl = e.target.closest('.drawflow-node');
    const srcEl = editor.ele_selected ? editor.ele_selected.closest('.drawflow-node') : null;
    if (nodeEl && nodeEl !== srcEl && nodeEl.querySelector('.inputs .input')) {
      if (hovered !== nodeEl) { clearHover(); hovered = nodeEl; nodeEl.classList.add('magnet-target'); }
    } else {
      clearHover();
    }
  });
  container.addEventListener('mouseup', clearHover);
}

// ---------- связь в пустоту -> меню блоков ----------
// Тянешь связь от выхода и отпускаешь на пустом холсте: вместо «ничего не
// произошло» появляется список блоков. Выбранный создаётся под курсором и
// сразу соединяется — так строить длинные ветки заметно быстрее.
const LINK_MENU_BLOCKS = [
  ['message', '💬 Сообщение'], ['delay', '⏱ Задержка'],
  ['condition', '❓ Условие (тег)'], ['filter', '🔎 Фильтр'], ['language', '🌐 Язык'],
  ['action', '⚡️ Действие'], ['chain', '⛓ Цепочка'], ['note', '⚠️ Заметка'],
];

function closeLinkMenu() {
  document.getElementById('link-menu')?.remove();
}

function setupLinkDropMenu() {
  const container = document.getElementById('drawflow');

  container.addEventListener('mousedown', e => {
    // порт выхода зажат — запоминаем, откуда потянули
    const out = e.target.closest('.outputs .output');
    if (!out) return;
    const nodeEl = out.closest('.drawflow-node');
    if (!nodeEl) return;
    const ports = [...nodeEl.querySelectorAll('.outputs .output')];
    container._linkFrom = {
      node: nodeEl.id.replace('node-', ''),
      port: `output_${ports.indexOf(out) + 1}`,
    };
  }, true);

  document.addEventListener('mouseup', e => {
    const from = container._linkFrom;
    container._linkFrom = null;
    if (!from || !editor) return;
    if (document.getElementById('page-editor').classList.contains('hidden')) return;
    // отпустили на блоке или на порте — этим занимается сам Drawflow
    if (e.target.closest('.drawflow-node')) return;
    if (!e.target.closest('#drawflow')) return;

    const p = canvasPoint(e);
    closeLinkMenu();
    const menu = document.createElement('div');
    menu.id = 'link-menu';
    menu.className = 'link-menu';
    menu.style.left = e.clientX + 'px';
    menu.style.top = e.clientY + 'px';
    menu.innerHTML = '<div class="link-menu-head">Добавить и соединить</div>' +
      LINK_MENU_BLOCKS.map(([t, l]) =>
        `<div class="link-menu-item" data-type="${t}">${l}</div>`).join('');
    document.body.appendChild(menu);

    // не даём меню уехать за край экрана
    const r = menu.getBoundingClientRect();
    if (r.bottom > innerHeight) menu.style.top = Math.max(8, innerHeight - r.height - 8) + 'px';
    if (r.right > innerWidth) menu.style.left = Math.max(8, innerWidth - r.width - 8) + 'px';

    menu.querySelectorAll('.link-menu-item').forEach(item => {
      item.onclick = () => {
        const type = item.dataset.type;
        const id = addBlockAt(type, p.x, p.y - 20);
        closeLinkMenu();
        // «Заметка» без входа — соединять нечем
        if (type !== 'note') {
          try { editor.addConnection(from.node, id, from.port, 'input_1'); } catch (err) {}
        }
        decoratePorts();
        onNodeSelected(id);
        showProps(id);
      };
    });
  });

  // клик мимо меню и Esc закрывают его
  document.addEventListener('mousedown', e => {
    if (!e.target.closest('#link-menu')) closeLinkMenu();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLinkMenu(); });
}

function copyNodes() {
  // Правки в панели свойств применяются с задержкой 350 мс. Скопировать
  // блок сразу после правки — значит скопировать его без неё.
  flushAutoApply();
  const ids = multiSelection.size ? [...multiSelection] : (selectedNodeId != null ? [String(selectedNodeId)] : []);
  const payload = clipPayload(ids);
  if (!payload.length) return;
  try { localStorage.setItem(CLIPBOARD_KEY, JSON.stringify(payload)); } catch {}
  flashStatus(`Скопировано блоков: ${payload.length}`);
}

// Блоки в виде, пригодном для вставки: данные, порты, позиции и связи
// только между ними самими.
function clipPayload(ids) {
  if (!ids.length) return [];
  const nodes = ids.map(id => editor.getNodeFromId(id)).filter(Boolean);
  const idSet = new Set(ids);
  const payload = nodes.map(n => ({
    oldId: String(n.id),
    name: n.name,
    data: n.data,
    outputs: Object.keys(n.outputs).length,
    inputs: Object.keys(n.inputs).length,
    pos_x: n.pos_x, pos_y: n.pos_y,
    // связи только между скопированными нодами
    conns: Object.entries(n.outputs).flatMap(([port, pd]) =>
      pd.connections.filter(c => idSet.has(String(c.node))).map(c => ({ from_port: port, to: String(c.node) }))),
  }));
  // JSON — чтобы копия не делила объекты data с оригиналом
  return JSON.parse(JSON.stringify(payload));
}

function pasteNodes() {
  let payload;
  try { payload = JSON.parse(localStorage.getItem(CLIPBOARD_KEY) || '[]'); } catch { return; }
  pastePayload(payload);
}

// Копия рядом, мимо буфера: кнопка «Копировать» под блоком не должна
// затирать то, что человек положил в буфер через Ctrl/⌘+C.
function duplicateNodes(ids) {
  flushAutoApply();
  pastePayload(clipPayload(ids));
}

function pastePayload(payload) {
  if (!payload || !payload.length) return;

  const OFF = 60;
  const idRemap = {};
  // 1) создаём ноды
  payload.forEach(p => {
    if (p.name === 'start') return; // старт не копируем — он один
    const newId = editor.addNode(
      p.name, p.inputs, p.outputs,
      (p.pos_x || 100) + OFF, (p.pos_y || 100) + OFF,
      p.name, p.data, nodeHtml(p.name, p.data), false
    );
    idRemap[p.oldId] = newId;
  });
  // 2) восстанавливаем внутренние связи
  payload.forEach(p => {
    const src = idRemap[p.oldId];
    if (src == null) return;
    (p.conns || []).forEach(c => {
      const dst = idRemap[c.to];
      if (dst != null) {
        try { editor.addConnection(src, dst, c.from_port, 'input_1'); } catch {}
      }
    });
  });
  clearMultiSelection();
  Object.values(idRemap).forEach(id => multiSelection.add(String(id)));
  paintSelection();
  flashStatus(`Вставлено блоков: ${Object.keys(idRemap).length}`);
}

function flashStatus(text) {
  let el = document.getElementById('editor-flash');
  if (!el) {
    el = document.createElement('div');
    el.id = 'editor-flash';
    el.className = 'editor-flash';
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 1800);
}


// ---------- кнопки под выделенным блоком и на выделенной связи ----------
// То же, что Ctrl/⌘+D и Delete, но мышью: выделил блок — под ним
// «Копировать» и «Удалить»; выделил связь — на ней «Удалить». Нужно и тем,
// кто не помнит сочетаний, и на случай, когда клавиатура не доходит до
// холста (фокус застрял в поле).
//
// Кнопки живут внутри холста Drawflow (precanvas), поэтому двигаются и
// масштабируются вместе с ним. editor.clear() стирает холст целиком —
// элементы пересоздаются по требованию.

// Снять выделение блока и связи так же, как это делает сам Drawflow
function dropDrawflowSelection() {
  if (!editor) return;
  if (editor.node_selected) {
    editor.node_selected.classList.remove('selected');
    editor.node_selected = null;
    editor.dispatch('nodeUnselected', true);
  }
  if (editor.connection_selected) {
    editor.connection_selected.classList.remove('selected');
    try { editor.removeReouteConnectionSelected(); } catch (e) {}
    editor.connection_selected = null;
  }
}

// Выделенный блок, если он ещё есть: после удаления Drawflow продолжает
// держать в node_selected уже убранный элемент
function liveSelectedNode() {
  const el = editor && editor.node_selected;
  if (!el || !el.isConnected) return null;
  const id = el.id.replace('node-', '');
  const data = editor.drawflow.drawflow.Home.data[id];
  return data ? { el, id, data } : null;
}

function quickEl(id, html) {
  let el = document.getElementById(id);
  if (!el || !editor.precanvas.contains(el)) {
    if (el) el.remove();
    el = document.createElement('div');
    el.id = id;
    el.className = 'df-actions hidden';
    el.innerHTML = html;
    // клик по кнопке — не клик по холсту: Drawflow снял бы выделение, а
    // перетаскивание началось бы прямо с кнопки
    el.addEventListener('mousedown', e => { e.stopPropagation(); e.preventDefault(); });
    el.addEventListener('click', e => {
      e.stopPropagation();
      const b = e.target.closest('[data-act]');
      if (b) quickAction(b.dataset.act);
    });
    editor.precanvas.appendChild(el);
  }
  return el;
}

function nodeActionsEl() {
  return quickEl('node-actions', `
    <button type="button" class="df-act" data-act="dup" title="Копировать (Ctrl/⌘+D)">⧉</button>
    <button type="button" class="df-act danger" data-act="del" title="Удалить (Delete)">🗑</button>`);
}

function linkActionsEl() {
  return quickEl('link-actions', `
    <button type="button" class="df-act danger" data-act="unlink" title="Удалить связь (Delete)">🗑 связь</button>`);
}

// Над каким блоком сейчас кнопки и к какой группе они относятся
function quickTargetIds() {
  const live = liveSelectedNode();
  const sel = live ? live.id : null;
  if (!sel) return [];
  // блок из выделенной группы — действия на всю группу
  return multiSelection.size > 1 && multiSelection.has(sel) ? [...multiSelection] : [sel];
}

function syncQuickActions() {
  if (!editor || !editor.precanvas) return;
  const na = nodeActionsEl();
  const la = linkActionsEl();
  // холст масштабируется, а кнопки должны оставаться привычного размера:
  // на отдалённом холсте они иначе становились бы крошечными
  const k = 1 / (editor.zoom || 1);
  na.style.transform = `translate(-50%, 0) scale(${k})`;
  la.style.transform = `translate(-50%, -50%) scale(${k})`;

  // блок
  const live = liveSelectedNode();
  const nodeEl = live && live.el;
  if (live && !editor.drag && live.data.name !== 'start') {
    const ids = quickTargetIds();
    na.querySelector('[data-act="dup"]').title =
      ids.length > 1 ? `Копировать выделенные (${ids.length})` : 'Копировать (Ctrl/⌘+D)';
    na.querySelector('[data-act="del"]').title =
      ids.length > 1 ? `Удалить выделенные (${ids.length})` : 'Удалить (Delete)';
    na.style.left = (nodeEl.offsetLeft + nodeEl.offsetWidth / 2) + 'px';
    na.style.top = (nodeEl.offsetTop + nodeEl.offsetHeight + 10 * k) + 'px';
    na.classList.remove('hidden');
  } else {
    na.classList.add('hidden');
  }

  // связь: кнопка — на середине линии
  const path = editor.connection_selected;
  if (path && path.isConnected && typeof path.getTotalLength === 'function') {
    try {
      const pt = path.getPointAtLength(path.getTotalLength() / 2);
      la.style.left = pt.x + 'px';
      la.style.top = pt.y + 'px';
      la.classList.remove('hidden');
    } catch (e) { la.classList.add('hidden'); }
  } else {
    la.classList.add('hidden');
  }
}

function quickAction(act) {
  if (act === 'dup') {
    const ids = quickTargetIds();
    if (ids.length) duplicateNodes(ids);
  } else if (act === 'del') {
    const ids = quickTargetIds();
    if (ids.length > 1) {
      deleteSelectedNodes();
    } else if (ids.length) {
      editor.removeNodeId('node-' + ids[0]);
      editor.node_selected = null;
      hideProps();
      clearMultiSelection();
    }
  } else if (act === 'unlink') {
    if (editor.connection_selected) {
      try { editor.removeConnection(); flashStatus('Связь удалена'); } catch (e) {}
    }
  }
  syncQuickActions();
}

function setupQuickActions() {
  ['nodeSelected', 'nodeUnselected', 'nodeRemoved', 'nodeMoved',
   'connectionSelected', 'connectionUnselected', 'connectionRemoved']
    .forEach(ev => editor.on(ev, () => setTimeout(syncQuickActions, 0)));
  const container = document.getElementById('drawflow');
  // пока блок тащат — кнопки прячем, иначе отстают от него
  container.addEventListener('mousedown', e => {
    if (e.target.closest('.df-actions')) return;
    if (e.target.closest('.drawflow-node')) nodeActionsEl().classList.add('hidden');
  });
  // отпустили — Drawflow уже обновил выделение и позицию
  document.addEventListener('mouseup', () => setTimeout(syncQuickActions, 0));
  // клавиатура (Delete, Ctrl+D) тоже меняет состав блоков и связей
  document.addEventListener('keyup', () => setTimeout(syncQuickActions, 0));
}
