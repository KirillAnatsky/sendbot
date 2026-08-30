// ---------- Интеграция с Google Таблицами ----------
let SHEETS_CFG = null;

async function loadSheetsPage() {
  try { SHEETS_CFG = await api('/integrations/sheets'); }
  catch (e) { return; }
  renderSheetsPage();
  await loadFunnelInput();
}

function renderSheetsPage() {
  const c = SHEETS_CFG;
  const rw = can('integrations', 'edit');
  const dis = rw ? '' : 'disabled';

  const status = c.last_run
    ? (c.last_status === 'ok'
        ? `<span class="status-active">успешно</span> · ${new Date(c.last_run + 'Z').toLocaleString('ru')}`
        : `<span style="color:#d33">ошибка</span> · ${new Date(c.last_run + 'Z').toLocaleString('ru')}<div class="sheets-err">${esc(c.last_error)}</div>`)
    : '<span style="color:#7a8499">ещё не выгружали</span>';

  document.getElementById('sheets-body').innerHTML = `
    <div class="panel">
      <div class="sheets-state">
        <div>
          <label>Подключение</label>
          ${c.connected
            ? `<div class="sheets-ok">✅ Робот подключён</div>
               <div class="sheets-mail">Дайте этой почте доступ «Редактор» к таблице:<br>
                 <code id="robot-mail">${esc(c.robot_email)}</code>
                 <button class="btn" onclick="copyRobotMail()">Скопировать</button></div>`
            : `<div class="sheets-off">Робот не подключён — вставьте ключ ниже</div>`}
        </div>
        <div>
          <label>Последняя выгрузка</label>
          <div>${status}</div>
          ${Object.keys(c.last_counts || {}).length
            ? `<div class="sheets-counts">${Object.entries(c.last_counts)
                 .map(([k, v]) => `<span class="pill gray">${esc(k)}: ${v}</span>`).join('')}</div>` : ''}
        </div>
      </div>
    </div>

    <div class="panel">
      <label>Ссылка на таблицу (или её идентификатор)</label>
      <input id="sh-id" class="inline-input" style="width:100%" ${dis}
        placeholder="https://docs.google.com/spreadsheets/d/..." value="${esc(c.spreadsheet_id || '')}">

      <label style="margin-top:14px">Ключ сервисного аккаунта (JSON)</label>
      <textarea id="sh-cred" rows="4" class="inline-input" style="width:100%;font-family:monospace;font-size:12px" ${dis}
        placeholder='${c.connected ? "ключ уже сохранён — вставьте новый, только если меняете робота" : "вставьте сюда содержимое скачанного файла целиком"}'></textarea>

      <label style="margin-top:14px">Что выгружать</label>
      <div class="sheets-list">
        ${(c.available || []).map(s => `
          <label class="sheets-item">
            <input type="checkbox" value="${s.key}" ${c.sheets[s.key] ? 'checked' : ''} ${dis}>
            <span><b>${esc(s.label)}</b><i>${esc(s.hint)}</i></span>
          </label>`).join('')}
      </div>

      <div class="row" style="margin-top:14px">
        <div>
          <label>Период отчётов</label>
          <select id="sh-days" ${dis}>
            ${[7, 14, 30, 90, 180, 365].map(d =>
              `<option value="${d}" ${c.days === d ? 'selected' : ''}>${d} дней</option>`).join('')}
          </select>
        </div>
        <div>
          <label>Обновление</label>
          <select id="sh-auto" onchange="toggleAutoFields()" ${dis}>
            <option value="0" ${!c.auto ? 'selected' : ''}>только по кнопке</option>
            <option value="1" ${c.auto ? 'selected' : ''}>автоматически</option>
          </select>
        </div>
        <div id="sh-interval-wrap" class="${c.auto ? '' : 'hidden'}">
          <label>Как часто</label>
          <select id="sh-interval" onchange="toggleAutoFields()" ${dis}>
            <option value="daily" ${c.interval === 'daily' ? 'selected' : ''}>раз в сутки</option>
            <option value="hourly" ${c.interval === 'hourly' ? 'selected' : ''}>каждый час</option>
          </select>
        </div>
        <div id="sh-hour-wrap" class="${c.auto && c.interval === 'daily' ? '' : 'hidden'}">
          <label>В котором часу (UTC)</label>
          <select id="sh-hour" ${dis}>
            ${Array.from({ length: 24 }, (_, h) =>
              `<option value="${h}" ${c.hour === h ? 'selected' : ''}>${String(h).padStart(2, '0')}:00</option>`).join('')}
          </select>
        </div>
      </div>

      ${rw ? `<div style="margin-top:14px">
        <button class="btn primary" onclick="saveSheets()">Сохранить</button>
        <button class="btn" onclick="exportSheetsNow()" id="sh-export-btn">📤 Выгрузить сейчас</button>
        <button class="btn" onclick="previewSheets()">Показать, что уйдёт</button>
        <span id="sh-status" class="sheets-inline-status"></span>
      </div>` : '<div class="hint-box">У вас доступ только на просмотр этих настроек.</div>'}
      <div id="sh-preview"></div>
    </div>

    <div class="hint-box">
      <b>Как подключить (один раз, ~10 минут).</b> Подробная инструкция со скриншотами —
      в файле <code>GOOGLE-ТАБЛИЦЫ.md</code> в проекте. Коротко:<br>
      1. console.cloud.google.com → создать проект → включить <b>Google Sheets API</b><br>
      2. Credentials → Create credentials → <b>Service account</b> → создать → вкладка Keys →
         Add key → JSON → файл скачается<br>
      3. Содержимое файла вставить в поле выше<br>
      4. Создать таблицу в Google, нажать «Поделиться» и дать почте робота права <b>Редактор</b><br>
      5. Вставить ссылку на таблицу и нажать «Выгрузить сейчас»
    </div>`;
}

function toggleAutoFields() {
  const auto = document.getElementById('sh-auto').value === '1';
  const daily = document.getElementById('sh-interval').value === 'daily';
  document.getElementById('sh-interval-wrap').classList.toggle('hidden', !auto);
  document.getElementById('sh-hour-wrap').classList.toggle('hidden', !(auto && daily));
}

function copyRobotMail() {
  const mail = document.getElementById('robot-mail').textContent;
  navigator.clipboard.writeText(mail);
  flashSheets('Почта скопирована');
}

function flashSheets(text, isError) {
  const el = document.getElementById('sh-status');
  if (!el) return;
  el.textContent = text;
  el.style.color = isError ? '#d33' : '#2a8';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.textContent = ''; }, 4000);
}

function collectSheets() {
  const sheets = {};
  document.querySelectorAll('.sheets-item input[type=checkbox]').forEach(cb => {
    sheets[cb.value] = cb.checked;
  });
  return {
    spreadsheet_id: document.getElementById('sh-id').value.trim(),
    credentials_json: document.getElementById('sh-cred').value.trim() || null,
    auto: document.getElementById('sh-auto').value === '1',
    interval: document.getElementById('sh-interval').value,
    hour: +document.getElementById('sh-hour').value,
    days: +document.getElementById('sh-days').value,
    sheets,
  };
}

async function saveSheets() {
  try {
    SHEETS_CFG = await api('/integrations/sheets', { method: 'PUT', body: collectSheets() });
  } catch (e) { return; }
  renderSheetsPage();
  flashSheets('Сохранено ✅');
}

async function exportSheetsNow() {
  const btn = document.getElementById('sh-export-btn');
  btn.disabled = true;
  flashSheets('Выгружаю…');
  try {
    // сначала сохраняем — чтобы выгружалось то, что на экране
    SHEETS_CFG = await api('/integrations/sheets', { method: 'PUT', body: collectSheets() });
    const r = await api('/integrations/sheets/export', { method: 'POST' });
    SHEETS_CFG = await api('/integrations/sheets');
    renderSheetsPage();
    const total = Object.entries(r.counts).map(([k, v]) => `${k}: ${v}`).join(', ');
    flashSheets('Готово — ' + total);
    if (confirm('Выгружено. Открыть таблицу?')) window.open(r.url, '_blank');
  } catch (e) {
    try { SHEETS_CFG = await api('/integrations/sheets'); renderSheetsPage(); } catch {}
  } finally {
    const b = document.getElementById('sh-export-btn');
    if (b) b.disabled = false;
  }
}

async function previewSheets() {
  let data;
  try {
    await api('/integrations/sheets', { method: 'PUT', body: collectSheets() });
    data = await api('/integrations/sheets/preview');
  } catch (e) { return; }
  document.getElementById('sh-preview').innerHTML = Object.entries(data).map(([title, d]) => `
    <div class="sheets-preview">
      <b>${esc(title)}</b> <span style="color:#7a8499">— строк: ${d.rows}</span>
      <table>${d.sample.map((row, i) => `<tr>${row.map(cell =>
        i === 0 ? `<th>${esc(String(cell))}</th>` : `<td>${esc(String(cell)).slice(0, 40)}</td>`
      ).join('')}</tr>`).join('')}</table>
    </div>`).join('');
}

// ---------- Конверсии по шагам в готовый лист (08B_FUNNEL_INPUT) ----------
// Отдельный блок: пишет не в наши листы, а в чужую таблицу — построчно,
// не затирая ручные колонки.
let FI_CFG = null;

async function loadFunnelInput() {
  try { FI_CFG = await api('/integrations/funnel-input'); }
  catch (e) { return; }
  renderFunnelInput();
}

function renderFunnelInput() {
  const c = FI_CFG;
  const rw = can('integrations', 'edit');
  const dis = rw ? '' : 'disabled';
  const r = c.last_result || {};

  const status = c.last_run
    ? (c.last_status === 'ok'
        ? `<span class="status-active">успешно</span> · ${new Date(c.last_run + 'Z').toLocaleString('ru')}`
        : `<span style="color:#d33">ошибка</span> · ${new Date(c.last_run + 'Z').toLocaleString('ru')}<div class="sheets-err">${esc(c.last_error)}</div>`)
    : '<span style="color:#7a8499">ещё не выгружали</span>';

  let host = document.getElementById('fi-block');
  if (!host) {
    host = document.createElement('div');
    host.id = 'fi-block';
    document.getElementById('sheets-body').appendChild(host);
  }

  host.innerHTML = `
    <h3 style="margin:26px 0 10px">Конверсии по шагам — в готовый лист</h3>

    <div class="panel">
      <div class="hint-box" style="margin-top:0">
        Одна строка = <b>неделя × бот × воронка</b>. Шагами считаются сообщения
        воронки по порядку, в колонки <code>Step N users</code> уходит число
        уникальных подписчиков, дошедших до шага за эту неделю.
        Колонки <code>Site transitions</code>, <code>GEO</code> и
        <code>Custom metrics</code> выгрузка не трогает — они ваши.
        Повторный запуск обновляет строку за ту же неделю, а не добавляет новую.
        Недели считаются с понедельника по UTC.
      </div>

      <label>Ссылка на таблицу</label>
      <input id="fi-id" class="inline-input" style="width:100%" ${dis}
        placeholder="https://docs.google.com/spreadsheets/d/..." value="${esc(c.spreadsheet_id || '')}">

      <div class="row" style="margin-top:14px">
        <div>
          <label>Название листа</label>
          <input id="fi-sheet" class="inline-input" ${dis} value="${esc(c.sheet_name || '')}">
        </div>
        <div>
          <label>Обновление</label>
          <select id="fi-auto" onchange="toggleFiFields()" ${dis}>
            <option value="0" ${!c.auto ? 'selected' : ''}>только по кнопке</option>
            <option value="1" ${c.auto ? 'selected' : ''}>автоматически</option>
          </select>
        </div>
        <div id="fi-interval-wrap" class="${c.auto ? '' : 'hidden'}">
          <label>Как часто</label>
          <select id="fi-interval" onchange="toggleFiFields()" ${dis}>
            <option value="weekly" ${c.interval === 'weekly' ? 'selected' : ''}>раз в неделю (пн)</option>
            <option value="daily" ${c.interval === 'daily' ? 'selected' : ''}>раз в сутки</option>
            <option value="hourly" ${c.interval === 'hourly' ? 'selected' : ''}>каждый час</option>
          </select>
        </div>
        <div id="fi-hour-wrap" class="${c.auto && c.interval !== 'hourly' ? '' : 'hidden'}">
          <label>В котором часу (UTC)</label>
          <select id="fi-hour" ${dis}>
            ${Array.from({ length: 24 }, (_, h) =>
              `<option value="${h}" ${c.hour === h ? 'selected' : ''}>${String(h).padStart(2, '0')}:00</option>`).join('')}
          </select>
        </div>
      </div>

      <div style="margin-top:14px">
        <label>Последняя выгрузка</label>
        <div>${status}</div>
        ${r.rows !== undefined
          ? `<div class="sheets-counts">
               <span class="pill gray">строк: ${r.rows}</span>
               <span class="pill gray">обновлено: ${r.updated || 0}</span>
               <span class="pill gray">добавлено: ${r.appended || 0}</span>
               ${r.steps ? `<span class="pill gray">колонок-шагов: ${r.steps}</span>` : ''}
             </div>` : ''}
      </div>

      ${rw ? `<div style="margin-top:14px">
        <button class="btn primary" onclick="saveFunnelInput()">Сохранить</button>
        <button class="btn" onclick="exportFunnelInputNow()" id="fi-export-btn">📤 Выгрузить сейчас</button>
        <button class="btn" onclick="previewFunnelInput()">Показать, что уйдёт</button>
        <span id="fi-status" class="sheets-inline-status"></span>
      </div>` : ''}
      <div id="fi-preview"></div>

      ${!c.connected ? `<div class="hint-box" style="margin-top:14px">
        Робот ещё не подключён — вставьте ключ сервисного аккаунта в блоке выше.
        Ключ общий для обеих выгрузок.</div>` : `<div class="hint-box" style="margin-top:14px">
        Не забудьте дать <code>${esc(c.robot_email || '')}</code> права
        <b>Редактор</b> на эту таблицу — кнопкой «Поделиться».</div>`}
    </div>`;
}

function toggleFiFields() {
  const auto = document.getElementById('fi-auto').value === '1';
  const hourly = document.getElementById('fi-interval').value === 'hourly';
  document.getElementById('fi-interval-wrap').classList.toggle('hidden', !auto);
  document.getElementById('fi-hour-wrap').classList.toggle('hidden', !(auto && !hourly));
}

function flashFi(text, isError) {
  const el = document.getElementById('fi-status');
  if (!el) return;
  el.textContent = text;
  el.style.color = isError ? '#d33' : '#2a8';
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.textContent = ''; }, 5000);
}

function collectFunnelInput() {
  return {
    spreadsheet_id: document.getElementById('fi-id').value.trim(),
    sheet_name: document.getElementById('fi-sheet').value.trim(),
    auto: document.getElementById('fi-auto').value === '1',
    interval: document.getElementById('fi-interval').value,
    hour: +document.getElementById('fi-hour').value,
  };
}

async function saveFunnelInput() {
  try {
    FI_CFG = await api('/integrations/funnel-input', { method: 'PUT', body: collectFunnelInput() });
  } catch (e) { return; }
  FI_CFG = await api('/integrations/funnel-input');
  renderFunnelInput();
  flashFi('Сохранено ✅');
}

async function exportFunnelInputNow() {
  const btn = document.getElementById('fi-export-btn');
  if (btn) btn.disabled = true;
  flashFi('Выгружаю…');
  try {
    await api('/integrations/funnel-input', { method: 'PUT', body: collectFunnelInput() });
    const r = await api('/integrations/funnel-input/export', { method: 'POST' });
    FI_CFG = await api('/integrations/funnel-input');
    renderFunnelInput();
    const d = r.result || {};
    flashFi(`Готово — строк ${d.rows || 0} (обновлено ${d.updated || 0}, добавлено ${d.appended || 0})`);
    if (confirm('Выгружено. Открыть таблицу?')) window.open(r.url, '_blank');
  } catch (e) {
    try { FI_CFG = await api('/integrations/funnel-input'); renderFunnelInput(); } catch {}
  } finally {
    const b = document.getElementById('fi-export-btn');
    if (b) b.disabled = false;
  }
}

async function previewFunnelInput() {
  let d;
  try {
    await api('/integrations/funnel-input', { method: 'PUT', body: collectFunnelInput() });
    d = await api('/integrations/funnel-input/preview');
  } catch (e) { return; }

  const box = document.getElementById('fi-preview');
  if (!d.rows) {
    box.innerHTML = `<div class="hint-box">За ${d.weeks.length === 1 ? 'текущую неделю' : 'выбранные недели'}
      данных пока нет — ни один подписчик не прошёл ни одного шага.
      Строки появятся, как только по воронке пойдут люди.</div>`;
    return;
  }
  box.innerHTML = `
    <div class="sheets-preview">
      <b>Уйдёт строк: ${d.rows}</b>
      <span style="color:#7a8499">— недели: ${d.weeks.join(', ')}</span>
      <table>
        <tr><th>Week start</th><th>Funnel / Bot</th>
          ${d.sample[0].steps.map((_, i) => `<th>Step ${i + 1}</th>`).join('')}</tr>
        ${d.sample.map(r => `<tr><td>${esc(r.week)}</td><td>${esc(r.label)}</td>
          ${r.steps.map(v => `<td>${v}</td>`).join('')}</tr>`).join('')}
      </table>
    </div>`;
}
