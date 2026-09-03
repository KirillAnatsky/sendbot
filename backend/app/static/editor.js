// ---------- визуальный редактор воронок (Drawflow) ----------
let editor = null;
let currentFunnelId = null;
let selectedNodeId = null;

const NODE_META = {
  start:     { title: '▶️ Старт',     inputs: 0, outputs: 1 },
  message:   { title: '💬 Сообщение', inputs: 1, outputs: 1 },
  delay:     { title: '⏱ Задержка',   inputs: 1, outputs: 1 },
  condition: { title: '❓ Условие',    inputs: 1, outputs: 2 },
  action:    { title: '🏷 Тег',       inputs: 1, outputs: 1 },
  language:  { title: '🌐 Язык',      inputs: 1, outputs: 2 },
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
      mediaHtml = `<div class="df-media">` + media.slice(0, 6).map(m => {
        if (m.type === 'photo') {
          const src = m.path.startsWith('http') ? m.path : '/' + m.path;
          return `<img class="df-media-thumb" src="${esc(src)}" alt="">`;
        }
        return `<span class="df-media-thumb icon" title="${esc(m.type)}">${MEDIA_ICON[m.type] || '📎'}</span>`;
      }).join('') + (media.length > 6 ? `<span class="df-media-more">+${media.length - 6}</span>` : '') + `</div>`;
    }
    const text = (data.text || '').slice(0, 500);
    const toggle = text.length > 120
      ? `<span class="df-toggle" onclick="toggleNodeExpand(event, this)">развернуть ▾</span>` : '';
    const textHtml = text
      ? `<div class="df-sub">${esc(text)}</div>${toggle}`
      : (media.length ? '' : `<div class="df-sub empty">нет текста</div>`);
    // кнопки — как в Telegram, с номером выхода
    let btnsHtml = '';
    const buttons = data.buttons || [];
    if (buttons.length) {
      let port = 2;
      btnsHtml = `<div class="df-btns">` + buttons.map(b => {
        const st = b.style ? ` st-${b.style}` : '';
        const off = b.disabled ? ' st-off' : '';
        if (b.url) return `<div class="df-btn url${st}${off}">🔗 ${esc(b.label || 'ссылка')}</div>`;
        return `<div class="df-btn${st}${off}"><span class="df-btn-port">${port++}</span>${esc(b.label || 'кнопка')}</div>`;
      }).join('') + `</div>`;
    }
    return `<div class="df-title">${NODE_META.message.title}</div>${mediaHtml}${textHtml}${btnsHtml}` +
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
  if (type === 'language') return `Язык: ${(d.languages || []).join(' / ') || '?'} / остальные`;
  if (type === 'note') return d.text || 'пустая заметка';
  if (type === 'action') return (d.op === 'remove_tag' ? 'Снять тег: ' : 'Добавить тег: ') + tagName(d.tag);
  return '';
}

function portsHint(type, d) {
  d = d || {};
  if (type === 'condition') return '1: да  •  2: нет';
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
    e.preventDefault();
    e.returnValue = '';
  }
});

async function openEditor(id) {
  // запоминаем, откуда пришли (список воронок или экран бота)
  editorReturnTo = !document.getElementById('page-bot').classList.contains('hidden') ? 'bot' : 'funnels';
  await loadTags();
  currentFunnelId = id;
  const [f, bots] = await Promise.all([api('/funnels/' + id), api('/bots')]);

  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.getElementById('page-editor').classList.remove('hidden');
  document.getElementById('funnel-name').value = f.name;
  document.getElementById('funnel-trigger').value = f.trigger_type;

  if (f.trigger_type === 'keyword') document.getElementById('funnel-trigger-value').value = f.trigger_value || '';
  populateTriggerTag(f.trigger_type, f.trigger_value || '');
  updateTriggerInputs();

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
    // Drawflow шлёт nodeUnselected и при перевыборе другого блока — группу
    // при этом сбрасывать нельзя (это и ломало групповое перетаскивание).
    // Пустой фон снимает выделение сам (см. setupMarquee).
    editor.on('nodeUnselected', () => { hideProps(); });
    editor.on('nodeRemoved', () => hideProps());
    setupClipboard();
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
  decoratePorts();
  loadFunnelStats();
  EDITOR_SNAPSHOT = editorStateJson();
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
  return order;
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
  steps.forEach(st => {
    const count = s.nodes[st.id] || 0;
    const fromPrev = prev ? Math.round(100 * count / prev) : 0;
    const fromStart = entered ? Math.round(100 * count / entered) : 0;
    html += `
      <div class="step-row">
        <div class="step-label">${esc(summary(st.name, st.data)).slice(0, 60)}</div>
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
  if (editorDirty() &&
      !confirm('Есть несохранённые изменения — выйти без сохранения?\n\n(Сохранить: кнопка «💾 Сохранить» или Ctrl/⌘+S)')) return;
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
    action: { op: 'add_tag', tag: TAGS[0] ? String(TAGS[0].id) : '' },
    note: { text: '' },
  }[type];
}

function addBlockAt(type, x, y) {
  const m = NODE_META[type];
  const defaults = blockDefaults(type);
  return editor.addNode(type, m.inputs, m.outputs, x, y, type, defaults, nodeHtml(type, defaults), false);
}

// клик по палитре — как раньше, блок появляется в видимой части холста
function addBlock(type) {
  const x = (-editor.canvas_x + 260 + Math.random() * 120) / (editor.zoom || 1);
  const y = (-editor.canvas_y + 120 + Math.random() * 200) / (editor.zoom || 1);
  addBlockAt(type, x, y);
}

// координаты мыши -> координаты холста Drawflow (с учётом сдвига и зума)
function canvasPoint(e) {
  const rect = document.getElementById('drawflow').getBoundingClientRect();
  const z = editor.zoom || 1;
  return {
    x: (e.clientX - rect.left - editor.canvas_x) / z,
    y: (e.clientY - rect.top - editor.canvas_y) / z,
  };
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
  const el = document.querySelector(`#node-${id} .drawflow_content_node`);
  if (el) el.innerHTML = nodeHtml(node.name, node.data);
  // вернуть бейджи статистики после перерисовки
  if (typeof applyStatsBadges === 'function') applyStatsBadges();
  decoratePorts();
  // связи могли сместиться из-за изменившейся высоты карточки
  try { editor.updateConnectionNodes('node-' + id); } catch (e) {}
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

    if (n.name === 'condition') {
      if (outs[0]) { outs[0].textContent = '✓'; outs[0].classList.add('port-yes'); outs[0].title = 'да — тег есть'; }
      if (outs[1]) { outs[1].textContent = '✕'; outs[1].classList.add('port-no'); outs[1].title = 'нет — тега нет'; }
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
      <label>Текст (HTML, {first_name})</label>
      <textarea id="p-text" rows="6">${esc(d.text || '')}</textarea>
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
  } else if (node.name === 'language') {
    const langs = d.languages || [];
    html += `
      <p style="font-size:12.5px;color:#7a8499;margin-bottom:8px">
        Развилка по языку Telegram-профиля. Определяется автоматически —
        подписчика ни о чём не спрашиваем.
      </p>
      <label>Ветки (код языка; несколько через запятую)</label>
      <div id="p-langs">${langs.map(l => langRow(l)).join('')}</div>
      <button class="btn" onclick="addLangRow()">+ язык</button>
      <div class="lang-quick">
        ${['ru', 'en', 'uk', 'pl', 'de', 'es', 'pt'].map(c =>
          `<span class="pill gray" onclick="addLangRow('${c}')">${c}</span>`).join('')}
      </div>
      <div class="hint-box">
        Коды — как в Telegram: <code>ru</code>, <code>en</code>, <code>uk</code>,
        <code>pl</code>… Подписчик с <code>pt-br</code> попадёт в ветку
        <code>pt</code>. В одну ветку можно несколько кодов:
        <code>ru, uk, be</code>. Кто не совпал ни с одной веткой — уходит
        в выход «остальные» (∗).
      </div>`;
  } else if (node.name === 'action') {
    html += `
      <label>Операция</label>
      <select id="p-op">
        <option value="add_tag" ${d.op !== 'remove_tag' ? 'selected' : ''}>Добавить тег</option>
        <option value="remove_tag" ${d.op === 'remove_tag' ? 'selected' : ''}>Снять тег</option>
      </select>
      <label>Тег</label>
      <select id="p-tag" class="tag-select" onchange="maybeCreateTag(this)">${tagOptions(d.tag)}</select>`;
  }

  if (node.name !== 'start') {
    html += `<button class="btn primary" onclick="applyProps()">Применить</button>`;
  }
  html += `<button class="btn danger" onclick="deleteSelectedNode()">Удалить блок</button>`;
  props.innerHTML = html;
  props.classList.remove('hidden');
  if (node.name === 'message') setupImageUploader();
  // живое превью: любое изменение в панели свойств применяется автоматически
  if (node.name !== 'start') {
    props.oninput = scheduleAutoApply;
    props.onchange = scheduleAutoApply;
  } else {
    props.oninput = props.onchange = null;
  }
}

let _autoApplyTimer = null;
function scheduleAutoApply() {
  clearTimeout(_autoApplyTimer);
  _autoApplyTimer = setTimeout(() => {
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
    d.text = document.getElementById('p-text').value;
    d.media = MEDIA_ITEMS.map(m => ({ type: m.type, path: m.path, name: m.name || '' }));
    d.photo_url = '';  // старое поле больше не используем (медиа в d.media)
    d.buttons = [...document.querySelectorAll('#p-buttons .btn-row')].map(row => {
      const b = {
        label: row.querySelector('.p-btn-label').value,
        url: row.querySelector('.p-btn-url').value || undefined,
      };
      const style = row.querySelector('.p-btn-style').value;
      if (style) b.style = style;
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
  } else if (node.name === 'language') {
    d.languages = [...document.querySelectorAll('#p-langs .p-lang')]
      .map(inp => inp.value.trim()).filter(Boolean);
    // выходы: 1 («остальные») + по одному на каждую ветку языка
    const need = 1 + d.languages.length;
    const cur = Object.keys(editor.getNodeFromId(selectedNodeId).outputs).length;
    for (let i = cur; i < need; i++) editor.addNodeOutput(selectedNodeId);
    for (let i = cur; i > need; i--) editor.removeNodeOutput(selectedNodeId, 'output_' + i);
  } else if (node.name === 'action') {
    d.op = document.getElementById('p-op').value;
    d.tag = cleanTag(document.getElementById('p-tag').value, d.tag);
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
    renderAiChat();
    document.getElementById('ai-chat-text').focus();
  }
}

function renderAiChat() {
  const box = document.getElementById('ai-chat-msgs');
  const hist = AI_CHAT_HISTORY[currentFunnelId] || [];
  const intro = box.querySelector('.aim.intro-keep') ? '' : box.children[0]?.outerHTML || '';
  box.innerHTML = intro + hist.map(m =>
    `<div class="aim ${m.role}">${esc(m.content)}</div>`).join('');
  box.scrollTop = box.scrollHeight;
}

async function aiChatSend() {
  const ta = document.getElementById('ai-chat-text');
  const text = ta.value.trim();
  if (!text) return;
  ta.value = '';
  const hist = (AI_CHAT_HISTORY[currentFunnelId] = AI_CHAT_HISTORY[currentFunnelId] || []);
  hist.push({ role: 'user', content: text });
  renderAiChat();
  const box = document.getElementById('ai-chat-msgs');
  box.insertAdjacentHTML('beforeend', '<div class="aim assistant typing">думаю…</div>');
  box.scrollTop = box.scrollHeight;
  document.getElementById('ai-chat-send').disabled = true;
  try {
    const r = await api('/ai/edit', { method: 'POST', body: {
      funnel_id: currentFunnelId, messages: hist } });
    hist.push({ role: 'assistant', content: r.reply });
    renderAiChat();
    if (r.updated) {
      // граф приходит прямо в ответе — рисуем сразу, без второго запроса
      const f = r.funnel || await api('/funnels/' + currentFunnelId);
      applyFunnelToCanvas(f);
      setTimeout(() => { EDITOR_SNAPSHOT = editorStateJson(); }, 120);
      flashStatus('Воронка обновлена ✅');
    }
  } catch (e) {
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
    decoratePorts();
    loadFunnelStats();
    Object.keys(editor.drawflow.drawflow.Home.data).forEach(id => {
      try { editor.updateConnectionNodes('node-' + id); } catch (e) {}
    });
  }, 50);
}

// ---------- вертикальная авторасстановка ----------
function arrangeVertical() {
  const df = editor.export().drawflow.Home.data;
  const flow = {}, notes = {};
  Object.values(df).forEach(n => {
    (n.name === 'note' ? notes : flow)[String(n.id)] = n;
  });
  // глубина BFS от старта
  let startId = null;
  Object.values(df).forEach(n => { if (n.name === 'start') startId = String(n.id); });
  const depth = {};
  if (startId != null) depth[startId] = 0;
  const queue = startId != null ? [startId] : [];
  while (queue.length) {
    const cur = queue.shift();
    Object.values(df[cur].outputs || {}).forEach(p => (p.connections || []).forEach(c => {
      const t = String(c.node);
      if (flow[t] && depth[t] === undefined) { depth[t] = depth[cur] + 1; queue.push(t); }
    }));
  }
  Object.keys(flow).forEach(id => { if (depth[id] === undefined) depth[id] = 1; });
  // заметки — в строку своего блока
  const notesByAbout = {};
  Object.entries(notes).forEach(([id, n]) => {
    const about = String((n.data || {}).about || '');
    (notesByAbout[about] = notesByAbout[about] || []).push(id);
  });
  const rows = {};
  Object.keys(flow).sort((a, b) => +a - +b).forEach(id => {
    (rows[depth[id]] = rows[depth[id]] || []).push(id);
    (notesByAbout[id] || []).forEach(nid => rows[depth[id]].push(nid));
  });
  const orphan = Object.keys(notes).filter(id => !Object.values(notesByAbout).flat().includes(id) ||
    !flow[String((notes[id].data || {}).about || '')]);

  const X_STEP = 400, Y_GAP = 70;
  let y = 60;
  const place = (id, x, yy) => {
    const el = document.getElementById('node-' + id);
    if (!el) return 0;
    el.style.left = x + 'px'; el.style.top = yy + 'px';
    const n = editor.drawflow.drawflow.Home.data[id];
    if (n) { n.pos_x = x; n.pos_y = yy; }
    return el.offsetHeight || 120;
  };
  Object.keys(rows).map(Number).sort((a, b) => a - b).forEach(d => {
    let maxH = 0;
    rows[d].forEach((id, i) => { maxH = Math.max(maxH, place(id, 60 + i * X_STEP, y)); });
    y += maxH + Y_GAP;
  });
  orphan.forEach((id, i) => place(id, 60 + i * X_STEP, y));
  // перерисовать все связи
  Object.keys(df).forEach(id => { try { editor.updateConnectionNodes('node-' + id); } catch (e) {} });
  decoratePorts();
  flashStatus('Разложено сверху вниз');
}

// ---------- сохранение ----------
async function saveFunnel() {
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

  try {
    await api('/funnels/' + currentFunnelId, {
      method: 'PUT',
      body: {
        name: document.getElementById('funnel-name').value || 'Без названия',
        trigger_type: trigger,
        trigger_value: triggerValue,
        graph_ui: editor.export(),
        bot_ids: botIds,
      },
    });
    EDITOR_SNAPSHOT = editorStateJson();
    flashStatus('Сохранено ✅');
  } catch (e) { /* alert уже показан */ }
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

function setupClipboard() {
  document.getElementById('drawflow').addEventListener('mousedown', e => {
    lastClickCtrl = e.ctrlKey || e.metaKey;
  }, true);
  document.addEventListener('keydown', e => {
    shiftHeld = e.shiftKey;
    // работаем только когда открыт редактор и фокус не в поле ввода
    if (document.getElementById('page-editor').classList.contains('hidden')) return;
    const inField = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ''));
    const mod = e.ctrlKey || e.metaKey;
    // Ctrl/⌘+S сохраняет даже из поля ввода — рука сама тянется
    if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); saveFunnel(); return; }
    if (inField) return;
    if (mod && e.key.toLowerCase() === 'c') { e.preventDefault(); copyNodes(); }
    else if (mod && e.key.toLowerCase() === 'v') { e.preventDefault(); pasteNodes(); }
    else if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); copyNodes(); pasteNodes(); }
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
    if (e.code === 'Space' && !/^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName || '')) {
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
    if (!e.shiftKey && !e.ctrlKey && !e.metaKey) clearMultiSelection();  // без модификаторов — заново
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
  ['condition', '❓ Условие (тег)'], ['language', '🌐 Язык'],
  ['action', '🏷 Тег +/−'], ['note', '⚠️ Заметка'],
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
  const ids = multiSelection.size ? [...multiSelection] : (selectedNodeId != null ? [String(selectedNodeId)] : []);
  if (!ids.length) return;
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
  try { localStorage.setItem(CLIPBOARD_KEY, JSON.stringify(payload)); } catch {}
  flashStatus(`Скопировано блоков: ${payload.length}`);
}

function pasteNodes() {
  let payload;
  try { payload = JSON.parse(localStorage.getItem(CLIPBOARD_KEY) || '[]'); } catch { return; }
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
