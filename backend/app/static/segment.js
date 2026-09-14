// ---------- конструктор сегментов (как в SendPulse) ----------
let SEG_FIELDS = null;
let SEG_TZ = '';           // часовой пояс проекта — подписываем им условия про время
async function loadSegFields() {
  if (!SEG_FIELDS) {
    const r = await api('/segment/fields');
    SEG_FIELDS = r.fields || r;      // старый формат — просто массив
    SEG_TZ = r.timezone || '';
  }
  return SEG_FIELDS;
}

// Создаёт конструктор внутри containerEl. Возвращает { getFilter, reset }.
// initial — уже сохранённый фильтр (нода «Фильтр» открывается со своими
// условиями, а не пустой).
// opts.nodeOnly — это нода «Фильтр»: показываем и условия про момент
// срабатывания. В сегменте рассылки их нет: там они врали бы.
function makeSegment(containerEl, initial, opts) {
  const inNode = !!(opts && opts.nodeOnly);
  const FIELDS = SEG_FIELDS.filter(f => inNode || !f.node_only);
  const state = {
    match: (initial && initial.match) || 'all',
    active_24h: !!(initial && initial.active_24h),
    rows: ((initial && initial.conditions) || []).map(c => ({
      field: c.field, op: c.op, value: c.value,
    })),
  };

  const fieldDef = key => FIELDS.find(f => f.key === key) || SEG_FIELDS.find(f => f.key === key) || FIELDS[0];

  function readDom() {
    // подтягиваем текущие значения инпутов в state (перед перерисовкой)
    containerEl.querySelectorAll('.seg-row').forEach((rowEl, i) => {
      const row = state.rows[i];
      if (!row) return;
      const fd = fieldDef(row.field);
      if (fd.type === 'weekdays') {
        row.value = [...rowEl.querySelectorAll('.seg-day.on')].map(d => d.dataset.d).join(',');
        return;
      }
      if (fd.type === 'time' && row.op === 'between') {
        // промежуток лежит одной строкой «10:00-18:00»: значение условия
        // везде скалярное, отдельный формат ради двух полей заводить незачем
        const [a, b] = rowEl.querySelectorAll('.seg-value');
        row.value = `${(a && a.value) || ''}-${(b && b.value) || ''}`;
        return;
      }
      const v = rowEl.querySelector('.seg-value');
      if (v) row.value = v.value;
    });
  }

  function valueInput(row) {
    const fd = fieldDef(row.field);
    if (fd.type === 'text') {
      return `<input class="seg-value inline-input" value="${esc(row.value ?? '')}" placeholder="значение">`;
    }
    if (fd.type === 'choice' || fd.type === 'select') {
      const list = fd.options || [];
      const cur = String(row.value ?? '');
      // Значение, которого нет в списке (старый фильтр с кодом, набранным
      // руками), оставляем отдельным пунктом. Иначе select молча подставил бы
      // первый вариант и подменил условие.
      const unknown = cur && !list.some(o => String(o.v) === cur);
      const opts = list.map(o =>
        `<option value="${esc(o.v)}" ${cur === String(o.v) ? 'selected' : ''}>${esc(o.l)}</option>`).join('')
        + (unknown ? `<option value="${esc(cur)}" selected>${esc(cur)} — не из списка</option>` : '');
      return `<select class="seg-value inline-input">${opts || '<option value="">—</option>'}</select>`;
    }
    if (fd.type === 'weekdays') {
      const on = new Set(String(row.value ?? '').split(',').filter(Boolean));
      return `<span class="seg-days">${(fd.options || []).map(o =>
        `<span class="seg-day pill gray ${on.has(String(o.v)) ? 'on' : ''}" data-d="${o.v}"
               onclick="this.classList.toggle('on')">${esc(o.l)}</span>`).join('')}</span>`;
    }
    if (fd.type === 'time') {
      if (row.op === 'between') {
        const [a, b] = String(row.value ?? '').split('-');
        return `<input class="seg-value inline-input" type="time" value="${esc(a || '')}" style="width:110px">
                <span class="seg-dash">–</span>
                <input class="seg-value inline-input" type="time" value="${esc(b || '')}" style="width:110px">`;
      }
      return `<input class="seg-value inline-input" type="time" value="${esc(row.value ?? '')}" style="width:110px">`;
    }
    if (fd.type === 'date') {
      if (row.op === 'last_days' || row.op === 'inactive_days') {
        return `<input class="seg-value inline-input" type="number" min="1" value="${esc(row.value ?? 7)}" style="width:90px"> дней`;
      }
      return `<input class="seg-value inline-input" type="date" value="${esc(row.value ?? '')}">`;
    }
    return `<input class="seg-value inline-input" value="${esc(row.value ?? '')}">`;
  }

  // «10:00» без указания пояса — это гадание. Пишем прямо, чей это час,
  // и куда идти его менять.
  function tzNote() {
    const uses = state.rows.some(r => {
      const fd = fieldDef(r.field);
      return fd && fd.node_only;
    });
    if (!uses || !SEG_TZ) return '';
    return `<div class="seg-tz">Время и дата считаются по часовому поясу
      <b>${esc(SEG_TZ)}</b> — поменять можно в разделе «Настройки».</div>`;
  }

  function render() {
    const rowsHtml = state.rows.map((row, i) => {
      const fd = fieldDef(row.field);
      const fieldOpts = FIELDS.map(f =>
        `<option value="${f.key}" ${f.key === row.field ? 'selected' : ''}>${esc(f.label)}</option>`).join('');
      const opOpts = fd.ops.map(([v, l]) =>
        `<option value="${v}" ${v === row.op ? 'selected' : ''}>${esc(l)}</option>`).join('');
      return `<div class="seg-row" data-i="${i}">
        <select class="seg-field inline-input">${fieldOpts}</select>
        <select class="seg-op inline-input">${opOpts}</select>
        ${valueInput(row)}
        <button class="btn danger seg-del" title="удалить">✕</button>
      </div>`;
    }).join('');

    containerEl.innerHTML = `
      <div class="seg-head">
        <label class="seg-match">Совпадение:
          <select class="seg-match-sel inline-input">
            <option value="all" ${state.match === 'all' ? 'selected' : ''}>все условия (И)</option>
            <option value="any" ${state.match === 'any' ? 'selected' : ''}>любое (ИЛИ)</option>
          </select>
        </label>
        <label class="seg-24h"><input type="checkbox" class="seg-24h-chk" ${state.active_24h ? 'checked' : ''}> активен за 24 часа</label>
      </div>
      <div class="seg-rows">${rowsHtml || '<div class="seg-empty">Без условий — вся база бота.</div>'}</div>
      ${tzNote()}
      <button class="btn seg-add">+ условие</button>`;

    // события
    containerEl.querySelector('.seg-match-sel').onchange = e => { state.match = e.target.value; };
    containerEl.querySelector('.seg-24h-chk').onchange = e => { readDom(); state.active_24h = e.target.checked; };
    containerEl.querySelector('.seg-add').onclick = () => {
      readDom();
      const f = FIELDS[0];
      state.rows.push({ field: f.key, op: f.ops[0][0], value: '' });
      render();
    };
    containerEl.querySelectorAll('.seg-row').forEach((rowEl, i) => {
      rowEl.querySelector('.seg-field').onchange = e => {
        readDom();
        const fd = fieldDef(e.target.value);
        state.rows[i] = { field: e.target.value, op: fd.ops[0][0], value: '' };
        render();
      };
      rowEl.querySelector('.seg-op').onchange = e => {
        readDom();
        state.rows[i].op = e.target.value;
        render();
      };
      rowEl.querySelector('.seg-del').onclick = () => {
        readDom();
        state.rows.splice(i, 1);
        render();
      };
    });
  }

  render();

  return {
    getFilter() {
      readDom();
      return {
        match: state.match,
        active_24h: state.active_24h,
        conditions: state.rows
          .filter(r => r.field && r.op)
          .map(r => ({ field: r.field, op: r.op, value: r.value })),
      };
    },
    reset() { state.rows = []; state.active_24h = false; state.match = 'all'; render(); },
  };
}
