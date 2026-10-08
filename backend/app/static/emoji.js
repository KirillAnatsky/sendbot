// ---------- Библиотека премиум-эмодзи и окно выбора ----------
// Выбрать премиум-эмодзи прямо из браузера Telegram не даёт: в сообщение нужен
// их числовой id. Поэтому наборы забираются целиком по ссылке
// t.me/addemoji/<имя> (раздел на сервере — /api/emoji), а здесь из них
// выбирают: кнопкой ✨ в редакторе текста и кнопкой-иконкой у кнопок.

let EMOJI_LIB = null;          // [{emoji_id, emoji, set_name, set_title, thumb}]
let EMOJI_BY_ID = {};
let _emojiLoading = null;

async function loadEmojiLib(force) {
  if (EMOJI_LIB && !force) return EMOJI_LIB;
  if (_emojiLoading && !force) return _emojiLoading;
  _emojiLoading = (async () => {
    try {
      EMOJI_LIB = await api('/emoji');
    } catch (e) {
      EMOJI_LIB = EMOJI_LIB || [];
    }
    EMOJI_BY_ID = {};
    for (const e of EMOJI_LIB) EMOJI_BY_ID[e.emoji_id] = e;
    _emojiLoading = null;
    // всё, что уже нарисовано с заглушками, получает настоящие миниатюры
    if (typeof rtRefreshEmoji === 'function') rtRefreshEmoji();
    return EMOJI_LIB;
  })();
  return _emojiLoading;
}

// Миниатюра эмодзи для редактора и превью; null — нарисовать обычную эмодзи
function emojiSrc(id) {
  const e = EMOJI_BY_ID[id];
  return e && e.thumb ? e.thumb : null;
}

// Картинка эмодзи по id — для кнопок и карточек узлов
function emojiIconHtml(id) {
  const e = EMOJI_BY_ID[id];
  return rtEmojiHtml(id, e ? e.emoji : '⭐');
}

function _emojiSets() {
  const sets = [];
  const by = {};
  for (const e of EMOJI_LIB || []) {
    if (!by[e.set_name]) {
      by[e.set_name] = { name: e.set_name, title: e.set_title || e.set_name, items: [] };
      sets.push(by[e.set_name]);
    }
    by[e.set_name].items.push(e);
  }
  return sets;
}

let _emojiPop = null;

function closeEmojiPicker() {
  if (!_emojiPop) return;
  _emojiPop.el.remove();
  document.removeEventListener('mousedown', _emojiPop.outside, true);
  document.removeEventListener('keydown', _emojiPop.esc, true);
  _emojiPop = null;
}

// Открыть выбор рядом с anchor. onPick(e) получает эмодзи из библиотеки,
// а при clearable — ещё и null («без иконки»).
async function openEmojiPicker(anchor, onPick, opts = {}) {
  const reopen = _emojiPop && _emojiPop.anchor === anchor;
  closeEmojiPicker();
  if (reopen) return;   // повторное нажатие на ту же кнопку — закрыть

  const el = document.createElement('div');
  el.className = 'emoji-pop';
  document.body.appendChild(el);

  const outside = ev => { if (!el.contains(ev.target) && ev.target !== anchor && !anchor.contains(ev.target)) closeEmojiPicker(); };
  const esc_ = ev => { if (ev.key === 'Escape') { ev.stopPropagation(); closeEmojiPicker(); } };
  document.addEventListener('mousedown', outside, true);
  document.addEventListener('keydown', esc_, true);
  _emojiPop = { el, anchor, outside, esc: esc_ };

  function place() {
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = Math.min(r.left, window.innerWidth - w - 8);
    let top = r.bottom + 4;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
    el.style.left = Math.max(8, left) + 'px';
    el.style.top = top + 'px';
  }

  function render(msg) {
    const sets = _emojiSets();
    const del = typeof canDelete === 'function' && canDelete();
    el.innerHTML = `
      <div class="emoji-pop-add">
        <input class="emoji-link" placeholder="https://t.me/addemoji/НазваниеНабора">
        <button type="button" class="btn emoji-add-btn">Добавить набор</button>
      </div>
      ${msg ? `<div class="emoji-pop-msg">${msg}</div>` : ''}
      ${opts.clearable ? `<button type="button" class="btn emoji-clear">Без иконки</button>` : ''}
      <div class="emoji-pop-sets">
        ${sets.length ? sets.map(st => `
          <div class="emoji-set">
            <div class="emoji-set-head">
              <span>${esc(st.title)} <span class="emoji-set-n">${st.items.length}</span></span>
              ${del ? `<button type="button" class="emoji-set-del" data-set="${esc(st.name)}"
                 title="убрать набор из библиотеки">✕</button>` : ''}
            </div>
            <div class="emoji-grid">
              ${st.items.map(e => `<button type="button" class="emoji-cell" data-id="${esc(e.emoji_id)}"
                  title="${esc(e.emoji)}">${e.thumb
                    ? `<img src="${esc(e.thumb)}" alt="${esc(e.emoji)}" loading="lazy" draggable="false">`
                    : `<span>${esc(e.emoji)}</span>`}</button>`).join('')}
            </div>
          </div>`).join('') : `
          <div class="emoji-empty">
            Библиотека пуста. Найдите набор эмодзи в Telegram, нажмите
            «Поделиться» и вставьте ссылку сюда — заберём его целиком.
          </div>`}
      </div>`;

    // клик по эмодзи не должен уводить фокус и выделение из редактора
    el.querySelectorAll('.emoji-cell').forEach(b => {
      b.addEventListener('mousedown', ev => ev.preventDefault());
      b.addEventListener('click', () => {
        const e = EMOJI_BY_ID[b.dataset.id];
        closeEmojiPicker();
        if (e) onPick(e);
      });
    });
    const clear = el.querySelector('.emoji-clear');
    if (clear) clear.onclick = () => { closeEmojiPicker(); onPick(null); };

    const input = el.querySelector('.emoji-link');
    const addBtn = el.querySelector('.emoji-add-btn');
    async function add() {
      const link = input.value.trim();
      if (!link) { input.focus(); return; }
      addBtn.disabled = true;
      addBtn.textContent = 'Загружаю…';
      try {
        const r = await api('/emoji/import', { method: 'POST', body: { link } });
        await loadEmojiLib(true);
        if (_emojiPop && _emojiPop.el === el) {
          render(`Набор «${esc(r.title)}»: ${r.total} эмодзи` +
                 (r.added < r.total ? `, новых ${r.added}` : ''));
        }
      } catch (e) {
        addBtn.disabled = false;
        addBtn.textContent = 'Добавить набор';
      }
    }
    addBtn.onclick = add;
    input.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); add(); } });

    el.querySelectorAll('.emoji-set-del').forEach(b => {
      b.onclick = async () => {
        if (!confirm('Убрать набор из библиотеки? В уже написанных текстах эмодзи останутся.')) return;
        try {
          await api('/emoji/set/' + encodeURIComponent(b.dataset.set), { method: 'DELETE' });
          await loadEmojiLib(true);
          if (_emojiPop && _emojiPop.el === el) render();
        } catch (e) { /* alert показан в api() */ }
      };
    });
    place();
  }

  el.innerHTML = '<div class="emoji-pop-msg">Загружаю библиотеку…</div>';
  place();
  await loadEmojiLib();
  if (_emojiPop && _emojiPop.el === el) render();
}

// ---------- иконка на кнопке (icon_custom_emoji_id) ----------
// Telegram рисует премиум-эмодзи перед текстом кнопки. Значение хранится в
// data-icon у кнопки выбора, а собирается вместе с остальными полями кнопки.

function btnIconPicker(id) {
  return `<button type="button" class="btn btn-icon-pick" data-icon="${esc(id || '')}"
    title="Премиум-эмодзи перед текстом кнопки" onclick="pickBtnIcon(this)">${
      id ? emojiIconHtml(id) : '<span class="btn-icon-none">☺</span>'}</button>`;
}

function pickBtnIcon(el) {
  openEmojiPicker(el, e => {
    el.dataset.icon = e ? e.emoji_id : '';
    el.innerHTML = e ? emojiIconHtml(e.emoji_id) : '<span class="btn-icon-none">☺</span>';
    // в редакторе воронки панель применяется сама — по input/change
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }, { clearable: true });
}
