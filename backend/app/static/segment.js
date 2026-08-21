// ---------- конструктор сегментов (как в SendPulse) ----------
let SEG_FIELDS = null;
async function loadSegFields() {
  if (!SEG_FIELDS) SEG_FIELDS = await api('/segment/fields');
  return SEG_FIELDS;
}

// Создаёт конструктор внутри containerEl. Возвращает { getFilter, reset }.
function makeSegment(containerEl) {
  const state = { match: 'all', active_24h: false, rows: [] };

  const fieldDef = key => SEG_FIELDS.find(f => f.key === key) || SEG_FIELDS[0];

  function readDom() {
    // подтягиваем текущие значения инпутов в state (перед перерисовкой)
    containerEl.querySelectorAll('.seg-row').forEach((rowEl, i) => {
      if (!state.rows[i]) return;
      const v = rowEl.querySelector('.seg-value');
      if (v) state.rows[i].value = v.value;
    });
  }

  function valueInput(row) {
    const fd = fieldDef(row.field);
    if (fd.type === 'text') {
      return `<input class="seg-value inline-input" value="${esc(row.value ?? '')}" placeholder="значение">`;
    }
    if (fd.type === 'choice' || fd.type === 'select') {
      const opts = (fd.options || []).map(o =>
        `<option value="${o.v}" ${String(row.value) === String(o.v) ? 'selected' : ''}>${esc(o.l)}</option>`).join('');
      return `<select class="seg-value inline-input">${opts || '<option value="">—</option>'}</select>`;
    }
    if (fd.type === 'date') {
      if (row.op === 'last_days' || row.op === 'inactive_days') {
        return `<input class="seg-value inline-input" type="number" min="1" value="${esc(row.value ?? 7)}" style="width:90px"> дней`;
      }
      return `<input class="seg-value inline-input" type="date" value="${esc(row.value ?? '')}">`;
    }
    return `<input class="seg-value inline-input" value="${esc(row.value ?? '')}">`;
  }

  function render() {
    const rowsHtml = state.rows.map((row, i) => {
      const fd = fieldDef(row.field);
      const fieldOpts = SEG_FIELDS.map(f =>
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
      <button class="btn seg-add">+ условие</button>`;

    // события
    containerEl.querySelector('.seg-match-sel').onchange = e => { state.match = e.target.value; };
    containerEl.querySelector('.seg-24h-chk').onchange = e => { readDom(); state.active_24h = e.target.checked; };
    containerEl.querySelector('.seg-add').onclick = () => {
      readDom();
      const f = SEG_FIELDS[0];
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
