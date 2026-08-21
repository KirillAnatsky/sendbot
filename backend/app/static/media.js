// ---------- переиспользуемый загрузчик вложений ----------
const MEDIA_TYPE_LIST = [
  ['photo', '🖼 Фото'], ['video', '🎬 Видео'], ['audio', '🎵 Аудио'],
  ['voice', '🎤 Голосовое'], ['video_note', '⭕️ Кружок'], ['document', '📎 Файл'],
];

function mediaThumbHtml(m) {
  const src = m.path.startsWith('http') ? m.path : '/' + m.path;
  if (m.type === 'photo') return `<img class="media-thumb" src="${esc(src)}" alt="">`;
  const icon = { video: '🎬', audio: '🎵', voice: '🎤', video_note: '⭕️', document: '📎' }[m.type] || '📎';
  return `<div class="media-thumb icon">${icon}</div>`;
}

// Монтирует загрузчик в контейнер. Возвращает { getItems }.
function mountMediaUploader(container, initial) {
  const items = (initial || []).map(m => ({ ...m }));
  container.innerHTML = `
    <div class="mu-list"></div>
    <div class="img-drop mu-drop" tabindex="0">
      <div class="img-drop-hint">Перетащи файлы, вставь (Ctrl/⌘+V) или <span class="img-pick">выбери с компа</span><br><span style="font-size:11px">можно несколько — уйдут альбомом</span></div>
    </div>
    <input type="file" class="mu-file" multiple hidden>`;
  const listEl = container.querySelector('.mu-list');
  const drop = container.querySelector('.mu-drop');
  const fileInput = container.querySelector('.mu-file');

  function render() {
    listEl.innerHTML = items.map((m, i) => `
      <div class="media-item">
        ${mediaThumbHtml(m)}
        <div class="media-mid">
          <select data-i="${i}" class="mu-type">
            ${MEDIA_TYPE_LIST.map(([v, l]) => `<option value="${v}" ${m.type === v ? 'selected' : ''}>${l}</option>`).join('')}
          </select>
          <div class="media-name">${esc(m.name || m.path.split('/').pop())}</div>
        </div>
        <div class="media-ord">
          <button class="btn mu-up" data-i="${i}" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="btn mu-down" data-i="${i}" ${i === items.length - 1 ? 'disabled' : ''}>↓</button>
          <button class="btn danger mu-del" data-i="${i}">✕</button>
        </div>
      </div>`).join('');
    listEl.querySelectorAll('.mu-type').forEach(s => s.onchange = e => { items[+e.target.dataset.i].type = e.target.value; render(); });
    listEl.querySelectorAll('.mu-up').forEach(b => b.onclick = () => { const i = +b.dataset.i; [items[i - 1], items[i]] = [items[i], items[i - 1]]; render(); });
    listEl.querySelectorAll('.mu-down').forEach(b => b.onclick = () => { const i = +b.dataset.i; [items[i + 1], items[i]] = [items[i], items[i + 1]]; render(); });
    listEl.querySelectorAll('.mu-del').forEach(b => b.onclick = () => { items.splice(+b.dataset.i, 1); render(); });
  }

  async function upload(files) {
    const hint = drop.querySelector('.img-drop-hint');
    for (const file of files) {
      hint.textContent = `загрузка: ${file.name}…`;
      const fd = new FormData();
      fd.append('file', file, file.name || 'pasted.png');
      try {
        const r = await fetch('/api/media/upload', { method: 'POST', headers: { 'Authorization': 'Bearer ' + TOKEN }, body: fd });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'Ошибка загрузки');
        items.push({ type: data.kind, path: data.path, name: data.name });
        render();
      } catch (e) { alert(e.message); }
    }
    hint.innerHTML = 'Перетащи файлы, вставь (Ctrl/⌘+V) или <span class="img-pick">выбери с компа</span><br><span style="font-size:11px">можно несколько — уйдут альбомом</span>';
  }

  drop.onclick = () => fileInput.click();
  fileInput.onchange = () => { if (fileInput.files.length) upload([...fileInput.files]); fileInput.value = ''; };
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('drag'); };
  drop.ondragleave = () => drop.classList.remove('drag');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('drag'); if (e.dataTransfer.files.length) upload([...e.dataTransfer.files]); };
  drop.onpaste = e => {
    const imgs = [...(e.clipboardData.items || [])].filter(i => i.type.startsWith('image/')).map(i => i.getAsFile());
    if (imgs.length) { e.preventDefault(); upload(imgs); }
  };
  render();
  return { getItems: () => items.map(m => ({ type: m.type, path: m.path, name: m.name || '' })) };
}
