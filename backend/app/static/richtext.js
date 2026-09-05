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
  return out.join('')
    // \u200b — наш служебный якорь каретки, \u00a0 браузер сам ставит вместо
    // обычного пробела в конце строки; ни то ни другое в сообщении не нужно
    .replace(/\u200b/g, '')
    .replace(/\u00a0/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/\s+$/, '');
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

// ---------- модель текста: символы с набором оформлений ----------
// Всё, что делает кнопка на выделении, считается на этой модели, а не через
// document.execCommand. Причина: браузерные команды не знают про спойлер и
// <code>, поэтому раньше их можно было навесить, но нельзя снять — повторное
// нажатие просто вкладывало тег в тег, а «убрать оформление» их не видело.
// На модели любое оформление снимается тем же действием, каким ставится.

const RT_MARKS = ['b', 'i', 'u', 's', 'code', 'spoiler'];

// Символ нулевой ширины: им «прибиваем» каретку снаружи оформленного куска.
// В текст сообщения он не попадает — модель его не видит, выгрузка вырезает.
const RT_ZWSP = '\u200b';

const RT_TAG_MARK = {
  b: 'b', strong: 'b', i: 'i', em: 'i', u: 'u', ins: 'u',
  s: 's', strike: 's', del: 's', code: 'code', pre: 'code',
  'tg-spoiler': 'spoiler', a: 'link',
};

function rtMarkOf(el) {
  const tag = el.tagName.toLowerCase();
  if (tag === 'span') return el.classList.contains('tg-spoiler') ? 'spoiler' : null;
  return RT_TAG_MARK[tag] || null;
}

// Текст области как плоский список символов. У каждого — своё оформление,
// поэтому вложенность и частичные выделения перестают быть особым случаем.
function rtChars(root) {
  const chars = [];
  const push = (ch, marks, href, node, at) =>
    chars.push({ ch, marks: new Set(marks), href, node, at });

  (function walk(node, marks, href) {
    for (const n of node.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) {
        let at = 0;
        // по кодовым точкам, иначе эмодзи разваливается на половинки
        for (const ch of n.nodeValue) {
          if (ch !== RT_ZWSP) push(ch, marks, href, n, at);
          at += ch.length;
        }
        continue;
      }
      if (n.nodeType !== Node.ELEMENT_NODE) continue;
      const tag = n.tagName.toLowerCase();
      if (RT_DROP.has(tag)) continue;
      if (tag === 'br') { push('\n', marks, href, n, 0); continue; }

      // Enter в contenteditable заворачивает строку в <div> — это перенос
      if ((tag === 'div' || tag === 'p') && chars.length
          && chars[chars.length - 1].ch !== '\n') push('\n', [], null, n, 0);

      const mark = rtMarkOf(n);
      const next = new Set(marks);
      let nextHref = href;
      if (mark === 'link') nextHref = (n.getAttribute('href') || '').trim() || href;
      else if (mark) next.add(mark);
      walk(n, next, nextHref);
    }
  })(root, new Set(), null);

  return chars;
}

// Обратно в разметку редактора. Собираем рекурсивно, от внешнего оформления
// к внутреннему: иначе «жирное, внутри курсив» распалось бы на два соседних
// <b>, и каждая правка перетасовывала бы теги заново.
function rtOuterMark(c, applied) {
  const m = RT_MARKS.find(x => c.marks.has(x) && !applied.has(x));
  if (m) return m;
  return c.href && !applied.has('link') ? 'link' : null;
}

function rtHtml(chars, applied) {
  applied = applied || new Set();
  let out = '';
  for (let i = 0; i < chars.length;) {
    const c = chars[i];
    const mark = rtOuterMark(c, applied);
    if (!mark) {
      let j = i;
      while (j < chars.length && !rtOuterMark(chars[j], applied)) j++;
      out += esc(chars.slice(i, j).map(x => x.ch).join('')).replace(/\n/g, '<br>');
      i = j;
      continue;
    }
    let j = i;
    while (j < chars.length && (mark === 'link'
             ? chars[j].href === c.href : chars[j].marks.has(mark))) j++;
    const inner = rtHtml(chars.slice(i, j), new Set([...applied, mark]));
    out += mark === 'link' ? `<a href="${esc(c.href)}">${inner}</a>`
         : mark === 'spoiler' ? `<span class="tg-spoiler">${inner}</span>`
         : `<${mark}>${inner}</${mark}>`;
    i = j;
  }
  return out;
}

function rtPointRange(c) {
  const r = document.createRange();
  if (c.node.nodeType === Node.TEXT_NODE) {
    r.setStart(c.node, Math.min(c.at, c.node.nodeValue.length));
  } else {
    r.setStartBefore(c.node);
  }
  r.collapse(true);
  return r;
}

// Номер символа, перед которым стоит точка (узел, смещение).
function rtIndexAt(chars, container, offset) {
  const point = document.createRange();
  try { point.setStart(container, offset); } catch (e) { return chars.length; }
  point.collapse(true);
  for (let i = 0; i < chars.length; i++) {
    if (point.compareBoundaryPoints(Range.START_TO_START, rtPointRange(chars[i])) <= 0) return i;
  }
  return chars.length;
}

function rtSelectRange(area, from, to) {
  const chars = rtChars(area);
  const point = i => {
    if (i < chars.length) return rtPointRange(chars[i]);
    const r = document.createRange();
    r.selectNodeContents(area);
    r.collapse(false);
    return r;
  };
  const a = point(from), b = point(to);
  const range = document.createRange();
  range.setStart(a.startContainer, a.startOffset);
  range.setEnd(b.startContainer, b.startOffset);
  const sel = document.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
}

function rtSelectionBounds(area) {
  const sel = document.getSelection();
  if (!sel || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  if (!area.contains(range.commonAncestorContainer)) return null;
  const chars = rtChars(area);
  return {
    chars,
    from: rtIndexAt(chars, range.startContainer, range.startOffset),
    to: rtIndexAt(chars, range.endContainer, range.endOffset),
  };
}

// Что включено на выделении (а если выделения нет — на символе слева от
// каретки). Из этого подсвечиваются кнопки: пока не видно, что оформление
// включено, непонятно и то, что его надо выключить.
function rtActiveMarks(area) {
  const b = rtSelectionBounds(area);
  if (!b) return new Set();
  let { chars, from, to } = b;
  if (from === to) { if (from === 0) return new Set(); from -= 1; to = from + 1; }
  const part = chars.slice(from, to);
  if (!part.length) return new Set();
  const out = new Set(RT_MARKS.filter(m => part.every(c => c.marks.has(m))));
  if (part.every(c => c.href)) out.add('link');
  return out;
}

// Применить/снять оформление на выделении. Возвращает false, если выделения
// нет — тогда вызывающий решает, что делать с кареткой.
function rtApply(area, kind, value) {
  const b = rtSelectionBounds(area);
  if (!b || b.to <= b.from) return false;
  const { chars, from, to } = b;
  const part = chars.slice(from, to);

  if (kind === 'clear') {
    part.forEach(c => { c.marks.clear(); c.href = null; });
  } else if (kind === 'link') {
    part.forEach(c => { c.href = value; });
  } else {
    // оформлено целиком — снимаем, иначе дооформляем остаток
    const on = part.every(c => c.marks.has(kind));
    part.forEach(c => (on ? c.marks.delete(kind) : c.marks.add(kind)));
  }

  area.innerHTML = rtHtml(chars);
  rtSelectRange(area, from, to);
  return true;
}

// Элемент с таким оформлением, внутри которого стоит каретка.
function rtEnclosing(area, node, kind) {
  while (node && node !== area) {
    if (node.nodeType === Node.ELEMENT_NODE && rtMarkOf(node) === kind) return node;
    node = node.parentNode;
  }
  return null;
}

// Оформленный кусок, из которого каретке надо выйти. Каретка в конце такого
// куска может стоять и внутри тега, и формально сразу за ним — на глаз это
// одно и то же место, и печатать браузер в обоих случаях будет внутрь.
// Поэтому смотрим не на узлы, а на символ слева от каретки.
function rtExitTarget(area, kind) {
  const b = rtSelectionBounds(area);
  if (!b || b.from !== b.to || b.from === 0) return null;
  const el = rtEnclosing(area, b.chars[b.from - 1].node, kind);
  if (!el) return null;
  const right = b.chars[b.from];
  if (right && el.contains(right.node)) return null;  // это середина, а не конец
  return el;
}

// Каретка стоит в конце оформленного куска — выводим её наружу, чтобы дальше
// печаталось обычным текстом. Пустого текстового узла Chrome не замечает и
// возвращает каретку внутрь тега, поэтому ставим символ нулевой ширины;
// в текст сообщения он не попадёт — и модель, и выгрузка его выбрасывают.
function rtStepOut(area, el) {
  const anchor = document.createTextNode(RT_ZWSP);
  el.parentNode.insertBefore(anchor, el.nextSibling);
  const r = document.createRange();
  r.setStart(anchor, anchor.nodeValue.length);
  r.collapse(true);
  const sel = document.getSelection();
  sel.removeAllRanges();
  sel.addRange(r);
}

// Команды браузера — только там, где нужна его память о «включено на
// будущее»: пустая каретка, с которой человек начинает печатать жирным.
const RT_EXEC = { b: 'bold', i: 'italic', u: 'underline', s: 'strikeThrough' };

const RT_TOOLS = [
  ['b', '<b>Ж</b>', 'Жирный (Ctrl/⌘+B)'],
  ['i', '<i>К</i>', 'Курсив (Ctrl/⌘+I)'],
  ['u', '<u>Ч</u>', 'Подчёркнутый (Ctrl/⌘+U)'],
  ['s', '<s>З</s>', 'Зачёркнутый'],
  ['code', '<code>{ }</code>', 'Моноширинный'],
  ['spoiler', '👁', 'Спойлер — текст под замазкой'],
  ['link', '🔗', 'Ссылка (Ctrl/⌘+K)'],
  ['clear', '✕', 'Убрать всё оформление с выделенного'],
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

  // Оформление, из которого только что вышли кареткой: слева от неё символ
  // всё ещё оформлен, но кнопку надо погасить — иначе она горит, хотя
  // печатается уже обычный текст.
  let stepped = new Set();

  function paintButtons() {
    const active = rtActiveMarks(area);
    const sel = document.getSelection();
    const collapsed = !sel || !sel.rangeCount || sel.isCollapsed;
    // у каретки жирность может быть «включена на будущее» — в тексте её ещё
    // нет, а следующая буква уже будет жирной. Про это знает только браузер
    if (collapsed && document.activeElement === area) {
      for (const m of Object.keys(RT_EXEC)) {
        try { document.queryCommandState(RT_EXEC[m]) ? active.add(m) : active.delete(m); }
        catch (e) { /* команда не поддержана — оставляем как есть */ }
      }
    }
    container.querySelectorAll('.rt-btn').forEach(btn => {
      const cmd = btn.dataset.cmd;
      btn.classList.toggle('on', active.has(cmd) && !stepped.has(cmd));
    });
  }

  function exec(cmd) {
    area.focus();
    const sel = document.getSelection();
    const collapsed = !sel || !sel.rangeCount || sel.isCollapsed;

    if (collapsed) {
      // Каретка без выделения. Единственное осмысленное действие — выйти
      // из оформления, внутри которого стоим, если стоим в его конце.
      if (cmd === 'clear') {
        let done = false;
        for (const m of [...RT_MARKS, 'link']) {
          const el = rtExitTarget(area, m);
          if (!el) continue;
          if (RT_EXEC[m]) document.execCommand(RT_EXEC[m], false, null);
          else rtStepOut(area, el);
          stepped.add(m);
          done = true;
        }
        if (!done) alert('Выделите текст, с которого нужно снять оформление');
        paintButtons();
        return;
      }
      // «включить и печатать дальше жирным» умеет сам браузер — у него для
      // этого есть состояние, которого нет у нашей модели: в тексте ещё
      // ничего не оформлено, а следующая буква уже будет жирной.
      if (RT_EXEC[cmd]) {
        document.execCommand(RT_EXEC[cmd]);
        stepped.delete(cmd);
        paintButtons();
        return;
      }
      // У спойлера и моноширинного такой команды нет. Зато есть понятное
      // действие: если каретка в конце такого куска — вывести её наружу.
      const el = rtExitTarget(area, cmd === 'link' ? 'link' : cmd);
      if (el) {
        rtStepOut(area, el);
        stepped.add(cmd);
        paintButtons();
        return;
      }
      alert(cmd === 'link' ? 'Сначала выделите текст, который станет ссылкой'
                           : 'Сначала выделите текст');
      return;
    }

    stepped.clear();
    if (cmd === 'link') {
      const url = (prompt('Адрес ссылки:', 'https://') || '').trim();
      if (!url || url === 'https://') return;
      if (/^javascript:/i.test(url)) { alert('Такую ссылку вставить нельзя'); return; }
      rtApply(area, 'link', url);
    } else {
      rtApply(area, cmd);
    }
    paintButtons();
    fire();
  }

  container.querySelectorAll('.rt-btn').forEach(btn => {
    // mousedown, а не click: клик успевает снять выделение в поле
    btn.addEventListener('mousedown', e => { e.preventDefault(); exec(btn.dataset.cmd); });
  });

  area.addEventListener('input', () => { stepped.clear(); paintButtons(); fire(); });
  area.addEventListener('keyup', paintButtons);
  area.addEventListener('mouseup', () => { stepped.clear(); paintButtons(); });
  area.addEventListener('focus', paintButtons);
  area.addEventListener('keydown', e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const k = e.key.toLowerCase();
    if (k === 'k') { e.preventDefault(); exec('link'); }
    else if ('biu'.includes(k)) {
      // перехватываем сами: браузерная команда сделала бы то же, но мимо
      // модели, и кнопки показывали бы не то состояние
      e.preventDefault();
      exec(k === 'b' ? 'b' : k === 'i' ? 'i' : 'u');
    }
  });
  // вставка — только текстом: из Word иначе прилетает мусорная разметка
  area.addEventListener('paste', e => {
    const text = (e.clipboardData || window.clipboardData).getData('text/plain');
    if (text == null) return;
    e.preventDefault();
    document.execCommand('insertText', false, text);
  });

  paintButtons();

  return {
    getHtml: () => rtToTelegram(area),
    setHtml: html => { area.innerHTML = rtFromTelegram(html); paintButtons(); },
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
