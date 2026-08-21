// ---------- Интеграция с Google Таблицами ----------
let SHEETS_CFG = null;

async function loadSheetsPage() {
  try { SHEETS_CFG = await api('/integrations/sheets'); }
  catch (e) { return; }
  renderSheetsPage();
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
