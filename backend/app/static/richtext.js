// ---------- Визуальный редактор текста сообщения ----------
// Выделил кусок, нажал «Ж» — стало жирным. Вместо того чтобы вручную
// расставлять <b></b> и считать, где какой тег закрыт.
//
// Telegram принимает не любой HTML, а короткий белый список тегов. Поэтому
// редактор — не «любая разметка», а ровно эти теги и ничего больше: всё
// лишнее вырезается на выходе. Иначе вставка из Word прилетает со стилями,
// span'ами и классами, и Telegram отвечает «can't parse entities».

const RT_TAGS = {
  b: 'b', strong: 'b', i: 'i', em: 'i', u: 'u', ins: 'u',
  s: 's', strike: 's', del: 's',
  code: 'code', pre: 'pre', a: 'a',
  'tg-spoiler': 'tg-spoiler',
};

// Эти элементы выбрасываем вместе с содержимым: их текст — не текст
// сообщения. Иначе вставка страницы целиком приносит в письмо код скриптов.
const RT_DROP = new Set([
  'script', 'style', 'noscript', 'iframe', 'object', 'embed',
  'template', 'head', 'title', 'meta', 'link',
]);

// Из HTML редактора — в HTML, который поймёт Telegram.
function rtToTelegram(root) {
  const out = [];

  function walk(node) {
    for (const n of node.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) {
        out.push(esc(n.nodeValue));
        continue;
      }
      if (n.nodeType !== Node.ELEMENT_NODE) continue;

      const tag = n.tagName.toLowerCase();
      if (RT_DROP.has(tag)) continue;
      if (tag === 'br') { out.push('\n'); continue; }

      // блочные переводим в перенос строки: <div>, <p> из вставки
      const block = tag === 'div' || tag === 'p';
      let keep = RT_TAGS[tag];

      // <span class="tg-spoiler"> и подсветка через style — приводим к тегам
      if (!keep && tag === 'span') {
        const st = (n.getAttribute('style') || '').toLowerCase();
        if (n.classList.contains('tg-spoiler')) keep = 'tg-spoiler';
        else if (/font-weight:\s*(bold|[6-9]00)/.test(st)) keep = 'b';
        else if (/font-style:\s*italic/.test(st)) keep = 'i';
        else if (/text-decoration:[^;]*underline/.test(st)) keep = 'u';
        else if (/text-decoration:[^;]*line-through/.test(st)) keep = 's';
      }

      if (keep === 'a') {
        const href = (n.getAttribute('href') || '').trim();
        // ссылка без адреса — просто текст; javascript: не пускаем
        if (!href || /^javascript:/i.test(href)) { walk(n); continue; }
        out.push(`<a href="${esc(href)}">`);
        walk(n);
        out.push('</a>');
        continue;
      }

      if (keep) {
        out.push(`<${keep}>`); walk(n); out.push(`</${keep}>`);
      } else {
        if (block && out.length && !/\n$/.test(out[out.length - 1])) out.push('\n');
        walk(n);
        if (block) out.push('\n');
      }
    }
  }

  walk(root);
  return out.join('').replace(/\n{3,}/g, '\n\n').replace(/\s+$/, '');
}

// Обратно: HTML из базы — в содержимое редактора. Чужие теги режем здесь же,
// чтобы в редактор не попало то, что мы потом не сможем отправить.
function rtFromTelegram(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = String(html || '').replace(/\n/g, '<br>');

  (function clean(node) {
    for (const n of [...node.childNodes]) {
      if (n.nodeType === Node.ELEMENT_NODE) {
        const tag = n.tagName.toLowerCase();
        if (RT_DROP.has(tag)) { n.parentNode.removeChild(n); continue; }
        if (tag !== 'br' && !RT_TAGS[tag]) {
          // не наш тег — оставляем содержимое, сам тег убираем
          const parent = n.parentNode;
          while (n.firstChild) parent.insertBefore(n.firstChild, n);
          parent.removeChild(n);
          continue;
        }
        if (tag === 'a') {
          const href = n.getAttribute('href') || '';
          [...n.attributes].forEach(a => n.removeAttribute(a.name));
          if (href && !/^javascript:/i.test(href)) n.setAttribute('href', href);
        } else {
          [...n.attributes].forEach(a => n.removeAttribute(a.name));
        }
        clean(n);
      }
    }
  })(tmp);

  return tmp.innerHTML;
}

const RT_TOOLS = [
  ['b', '<b>Ж</b>', 'Жирный (Ctrl/⌘+B)'],
  ['i', '<i>К</i>', 'Курсив (Ctrl/⌘+I)'],
  ['u', '<u>Ч</u>', 'Подчёркнутый (Ctrl/⌘+U)'],
  ['s', '<s>З</s>', 'Зачёркнутый'],
  ['code', '<code>{ }</code>', 'Моноширинный'],
  ['spoiler', '👁', 'Спойлер — текст под замазкой'],
  ['link', '🔗', 'Ссылка (Ctrl/⌘+K)'],
  ['clear', '✕', 'Убрать оформление'],
];

// Монтирует редактор в контейнер. Возвращает { getHtml, setHtml, focus }.
function mountRichText(container, initialHtml, onChange) {
  container.innerHTML = `
    <div class="rt">
      <div class="rt-bar">
        ${RT_TOOLS.map(([cmd, label, title]) =>
          `<button type="button" class="rt-btn" data-cmd="${cmd}" title="${esc(title)}">${label}</button>`).join('')}
      </div>
      <div class="rt-area" contenteditable="true" spellcheck="true"></div>
    </div>`;

  const area = container.querySelector('.rt-area');
  area.innerHTML = rtFromTelegram(initialHtml);

  const fire = () => { if (onChange) onChange(); };

  function exec(cmd) {
    area.focus();
    if (cmd === 'link') {
      const sel = document.getSelection();
      if (!sel || sel.isCollapsed) { alert('Сначала выделите текст, который станет ссылкой'); return; }
      const url = (prompt('Адрес ссылки:', 'https://') || '').trim();
      if (!url || url === 'https://') return;
      document.execCommand('createLink', false, url);
    } else if (cmd === 'spoiler') {
      // своего execCommand у спойлера нет — оборачиваем выделение руками
      const sel = document.getSelection();
      if (!sel || sel.isCollapsed) { alert('Сначала выделите текст'); return; }
      const span = document.createElement('span');
      span.className = 'tg-spoiler';
      try { span.appendChild(sel.getRangeAt(0).extractContents()); sel.getRangeAt(0).insertNode(span); }
      catch (e) { return; }
    } else if (cmd === 'clear') {
      document.execCommand('removeFormat');
      document.execCommand('unlink');
    } else {
      const map = { b: 'bold', i: 'italic', u: 'underline', s: 'strikeThrough' };
      if (map[cmd]) document.execCommand(map[cmd]);
      else if (cmd === 'code') {
        // <code> тоже без своей команды — оборачиваем выделение
        const sel = document.getSelection();
        if (!sel || sel.isCollapsed) return;
        const el = document.createElement('code');
        try { el.appendChild(sel.getRangeAt(0).extractContents()); sel.getRangeAt(0).insertNode(el); }
        catch (e) { return; }
      }
    }
    fire();
  }

  container.querySelectorAll('.rt-btn').forEach(btn => {
    // mousedown, а не click: клик успевает снять выделение в поле
    btn.addEventListener('mousedown', e => { e.preventDefault(); exec(btn.dataset.cmd); });
  });

  area.addEventListener('input', fire);
  area.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = e.key.toLowerCase();
    if (k === 'k') { e.preventDefault(); exec('link'); }
    else if ('biu'.includes(k)) { setTimeout(fire, 0); }   // браузер сам применит
  });
  // вставка — только текстом: из Word иначе прилетает мусорная разметка
  area.addEventListener('paste', e => {
    const text = (e.clipboardData || window.clipboardData).getData('text/plain');
    if (text == null) return;
    e.preventDefault();
    document.execCommand('insertText', false, text);
  });

  return {
    getHtml: () => rtToTelegram(area),
    setHtml: html => { area.innerHTML = rtFromTelegram(html); },
    focus: () => area.focus(),
  };
}

// Показ текста сообщения в админке. Просто esc() рисовал бы «<b>Привет</b>»
// как есть, а вставлять сырое из базы нельзя — оформление приходит от
// операторов. Пропускаем через тот же белый список, что и на отправку.
function rtPreview(html) {
  return rtFromTelegram(html);
}

// Только текст, без разметки — для узких мест вроде строки в списке рассылок.
function plainText(html) {
  const d = document.createElement('div');
  d.innerHTML = rtFromTelegram(html);
  return d.textContent || '';
}
