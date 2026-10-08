// Проверка редактора текста в настоящем Chromium: execCommand и
// contenteditable в jsdom не работают, а вся суть багов — именно в них.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

// Стенд собираем во временной папке: харнесс + сам richtext.js рядом,
// чтобы страница грузила его обычным <script src>, как в админке.
const STATIC = path.join(__dirname, '..', '..', 'app', 'static');
const DIR = fs.mkdtempSync(path.join(require('os').tmpdir(), 'rt-stand-'));
fs.copyFileSync(path.join(STATIC, 'richtext.js'), path.join(DIR, 'richtext.js'));
fs.copyFileSync(path.join(STATIC, 'emoji.js'), path.join(DIR, 'emoji.js'));
fs.copyFileSync(path.join(__dirname, 'rt_editor.harness.html'), path.join(DIR, 'index.html'));

// путь к Chromium: у Playwright свой, в контейнере — заранее распакованный
const CHROME = process.env.CHROMIUM_PATH || undefined;

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log((ok ? '  ok  ' : 'FAIL  ') + name + (ok ? '' : `\n        получено: ${JSON.stringify(got)}\n        ожидалось: ${JSON.stringify(want)}`));
}

(async () => {
  const browser = await chromium.launch(CHROME ? { executablePath: CHROME } : {});
  const page = await browser.newPage();
  await page.goto('file://' + path.join(DIR, 'index.html'));

  // помощник: с нуля набрать текст, что-то сделать, вернуть итоговый html
  async function scenario(fn) {
    await page.evaluate(() => { RT.setHtml(''); rtArea.focus(); });
    return await fn();
  }
  const html = () => page.evaluate(() => RT.getHtml());
  const type = t => page.keyboard.type(t);

  console.log('\n--- 1. Оформление применяется к выделенному ---');
  await scenario(async () => {
    await type('привет мир');
    await page.evaluate(() => selectText('привет'));
    await page.evaluate(() => clickBtn('b'));
  });
  check('жирный на выделении', await html(), '<b>привет</b> мир');

  console.log('\n--- 2. Повторное нажатие снимает оформление ---');
  for (const [cmd, tag] of [['b', 'b'], ['i', 'i'], ['u', 'u'], ['s', 's'],
                            ['code', 'code'], ['spoiler', 'tg-spoiler']]) {
    await scenario(async () => {
      await type('привет мир');
      await page.evaluate(c => { selectText('привет'); clickBtn(c); }, cmd);
    });
    check(`${cmd}: применилось`, await html(), `<${tag}>привет</${tag}> мир`);
    await page.evaluate(c => { selectText('привет'); clickBtn(c); }, cmd);
    check(`${cmd}: снялось повторным нажатием`, await html(), 'привет мир');
  }

  console.log('\n--- 3. Кнопка «убрать оформление» ---');
  for (const cmd of ['b', 'i', 'u', 's', 'code', 'spoiler', 'link']) {
    await scenario(async () => {
      await type('привет мир');
      await page.evaluate(c => {
        selectText('привет');
        if (c === 'link') { window.prompt = () => 'https://ya.ru'; }
        clickBtn(c);
      }, cmd);
    });
    await page.evaluate(() => { selectText('привет'); clickBtn('clear'); });
    check(`✕ убирает ${cmd}`, await html(), 'привет мир');
  }

  console.log('\n--- 4. Каретка в конце оформленного куска: его можно выключить ---');
  // ровно случай со скриншота: слово под замазкой в конце, дальше печатаем
  for (const [cmd, tag] of [['b', 'b'], ['i', 'i'], ['u', 'u'], ['s', 's'],
                            ['code', 'code'], ['spoiler', 'tg-spoiler']]) {
    await scenario(async () => {
      await type('молодец да');
      await page.evaluate(c => { selectText('да'); clickBtn(c); }, cmd);
      await page.evaluate(() => caretToEnd());
      await page.evaluate(c => clickBtn(c), cmd);   // выключаем
      await type(' и дальше');
    });
    check(`${cmd}: печать после выключения идёт без оформления`,
          await html(), `молодец <${tag}>да</${tag}> и дальше`);
  }

  console.log('\n--- 4б. Пустая каретка: включил и печатаешь ---');
  for (const [cmd, tag] of [['b', 'b'], ['i', 'i'], ['u', 'u'], ['s', 's']]) {
    await scenario(async () => {
      await type('вот ');
      await page.evaluate(c => clickBtn(c), cmd);
      await type('это');
    });
    check(`${cmd}: включается на пустой каретке`, await html(), `вот <${tag}>это</${tag}>`);
  }

  console.log('\n--- 5. Оформление вложенное и частичное ---');
  await scenario(async () => {
    await type('раз два три');
    await page.evaluate(() => { selectText('раз два'); clickBtn('b'); });
    await page.evaluate(() => { selectText('два'); clickBtn('i'); });
  });
  check('курсив внутри жирного', await html(), '<b>раз <i>два</i></b> три');
  await page.evaluate(() => { selectText('два'); clickBtn('clear'); });
  check('✕ на куске внутри жирного снимает всё с этого куска',
        await html(), '<b>раз </b>два три');

  console.log('\n--- 6. Кнопки показывают, что сейчас включено ---');
  await scenario(async () => {
    await type('привет');
    await page.evaluate(() => { selectText('привет'); clickBtn('b'); selectText('привет'); });
  });
  check('кнопка Ж подсвечена на жирном выделении',
        await page.evaluate(() => document.querySelector('.rt-btn[data-cmd="b"]').classList.contains('on')), true);
  check('кнопка К не подсвечена',
        await page.evaluate(() => document.querySelector('.rt-btn[data-cmd="i"]').classList.contains('on')), false);

  console.log('\n--- 7. Что не должно было сломаться ---');

  await scenario(async () => {
    await type('первая');
    await page.keyboard.press('Enter');
    await type('вторая');
  });
  check('переносы строк', await html(), 'первая\nвторая');

  await scenario(async () => {
    await type('раз два');
    await page.evaluate(() => selectText('раз'));
    await page.keyboard.press('Control+b');
  });
  check('Ctrl+B на выделении', await html(), '<b>раз</b> два');

  await scenario(async () => {
    await type('тут ссылка');
    await page.evaluate(() => { window.prompt = () => 'https://ya.ru/a?b=1&c=2'; selectText('ссылка'); clickBtn('link'); });
  });
  check('ссылка с параметрами', await html(), 'тут <a href="https://ya.ru/a?b=1&amp;c=2">ссылка</a>');

  await scenario(async () => {
    await type('опасно');
    await page.evaluate(() => { window.prompt = () => 'javascript:alert(1)'; selectText('опасно'); clickBtn('link'); });
  });
  check('javascript: не вставляется', await html(), 'опасно');

  await scenario(async () => {
    await type('привет 👨‍👩‍👧 мир');
    await page.evaluate(() => { selectText('привет'); clickBtn('b'); });
  });
  check('эмодзи не разваливается', await html(), '<b>привет</b> 👨‍👩‍👧 мир');

  const round = '<b>жирный</b> и <i>курсив</i>, <a href="https://ya.ru">ссылка</a>\nвторая <tg-spoiler>тайна</tg-spoiler>';
  await page.evaluate(h => RT.setHtml(h), round);
  check('текст из базы читается и отдаётся без изменений', await html(), round);

  // после правки оформления содержимое не должно осыпаться
  await page.evaluate(() => { selectText('курсив'); clickBtn('i'); });
  check('снятие курсива не трогает остальное', await html(),
        '<b>жирный</b> и курсив, <a href="https://ya.ru">ссылка</a>\nвторая <tg-spoiler>тайна</tg-spoiler>');

  await scenario(async () => {
    await type('было');
    await page.evaluate(() => { selectText('было'); clickBtn('b'); });
    await page.keyboard.press('Control+z');
  });
  check('Ctrl+Z после оформления не стирает текст',
        (await html()).includes('было'), true);

  console.log('\n--- 8. Арабский: направление письма не портит текст ---');

  const ar = 'اضغط على الزر لتفعيل العجلة';
  await scenario(async () => {
    await type(ar);
  });
  check('арабский набирается и отдаётся как есть', await html(), ar);

  await page.evaluate(a => { RT.setHtml(a); }, ar);
  check('арабский из базы читается без изменений', await html(), ar);

  await scenario(async () => {
    await type(ar);
    await page.evaluate(() => { selectText('الزر'); clickBtn('b'); });
  });
  check('жирный на арабском слове', await html(), ar.replace('الزر', '<b>الزر</b>'));

  // смешанный текст: латиница внутри арабского не должна перевернуть строку
  await scenario(async () => {
    await type('اضغط WHEEL هنا');
  });
  check('арабский с латиницей внутри', await html(), 'اضغط WHEEL هنا');

  await scenario(async () => {
    await type('اضغط هنا');
    await page.evaluate(() => { selectText('هنا'); clickBtn('spoiler'); });
    await page.evaluate(() => caretToEnd());
    await page.evaluate(() => clickBtn('spoiler'));
    await type(' الآن');
  });
  check('спойлер на арабском выключается так же, как на русском',
        await html(), 'اضغط <tg-spoiler>هنا</tg-spoiler> الآن');

  check('в тексте не появилось служебных символов направления',
        /[\u200e\u200f\u202a-\u202e\u2066-\u2069]/.test(await html()), false);

  console.log('\n--- 9. Премиум-эмодзи ---');

  const E1 = '<tg-emoji emoji-id="111">🎁</tg-emoji>';
  const E2 = '<tg-emoji emoji-id="222">🔥</tg-emoji>';

  await page.evaluate(h => RT.setHtml(h), `Акция ${E1} <b>сегодня ${E2}</b>`);
  check('эмодзи из базы читаются и отдаются без изменений', await html(),
        `Акция ${E1} <b>сегодня ${E2}</b>`);
  check('в редакторе это картинки', await page.evaluate(() =>
        rtArea.querySelectorAll('img.rt-emoji').length), 2);
  check('у эмодзи без миниатюры — заглушка с обычной эмодзи', await page.evaluate(() =>
        rtArea.querySelector('img[data-emoji-id="222"]').getAttribute('src').startsWith('data:image/svg')), true);

  // выбор из окна: каретка там, где стояла, эмодзи встаёт ровно туда
  await scenario(async () => {
    await type('скидка  сегодня');
    await page.evaluate(() => {
      const t = rtArea.firstChild;
      const r = document.createRange();
      r.setStart(t, 7); r.collapse(true);
      getSelection().removeAllRanges(); getSelection().addRange(r);
      clickBtn('emoji');
    });
    await page.waitForSelector('.emoji-cell[data-id="111"]');
    await page.click('.emoji-cell[data-id="111"]');
  });
  check('✨ вставляет эмодзи в место каретки', await html(), `скидка ${E1} сегодня`);
  check('окно выбора закрылось', await page.evaluate(() => !document.querySelector('.emoji-pop')), true);

  await page.keyboard.type('!');
  check('печать продолжается сразу после эмодзи', await html(), `скидка ${E1}! сегодня`);

  await page.keyboard.press('Backspace');
  await page.keyboard.press('Backspace');
  check('Backspace стирает эмодзи целиком', await html(), 'скидка  сегодня');

  // оформление поверх эмодзи
  await page.evaluate(h => RT.setHtml(h), `раз ${E1} два`);
  await page.evaluate(() => {
    const r = document.createRange();
    r.selectNodeContents(rtArea);
    getSelection().removeAllRanges(); getSelection().addRange(r);
    rtArea.focus();
    clickBtn('b');
  });
  check('жирный захватывает и эмодзи', await html(), `<b>раз ${E1} два</b>`);
  await page.evaluate(() => {
    const r = document.createRange();
    r.selectNodeContents(rtArea);
    getSelection().removeAllRanges(); getSelection().addRange(r);
    clickBtn('clear');
  });
  check('✕ снимает оформление, эмодзи остаётся', await html(), `раз ${E1} два`);

  // окно открывается и без выделения в поле — эмодзи встаёт в конец
  await page.evaluate(h => { RT.setHtml(h); document.activeElement.blur(); getSelection().removeAllRanges(); }, 'конец');
  await page.evaluate(() => clickBtn('emoji'));
  await page.waitForSelector('.emoji-cell[data-id="222"]');
  await page.click('.emoji-cell[data-id="222"]');
  check('без каретки эмодзи встаёт в конец', await html(), `конец${E2}`);

  // мусор и подделки
  await page.evaluate(() => RT.setHtml('x <tg-emoji emoji-id="abc">🎁</tg-emoji> <img src="y" onerror="window.HACK=1"> <tg-emoji emoji-id="5">&lt;b&gt;</tg-emoji>'));
  check('id не число — остаётся обычная эмодзи, чужие картинки выброшены',
        await html(), 'x 🎁  <tg-emoji emoji-id="5">⭐</tg-emoji>');
  await page.waitForTimeout(100);
  check('onerror из текста не выполнился', await page.evaluate(() => window.HACK), undefined);

  // вставка куска из своего же редактора сохраняет премиум-эмодзи
  await scenario(async () => {
    await page.evaluate(E => {
      const dt = new DataTransfer();
      dt.setData('text/html', `<meta charset="utf-8"><span style="color:red">ну </span><img class="rt-emoji" data-emoji-id="111" alt="🎁" src="x"><b> да</b>`);
      dt.setData('text/plain', 'ну 🎁 да');
      rtArea.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    });
  });
  check('вставка своего куска сохраняет эмодзи и оформление', await html(), `ну ${E1}<b> да</b>`);

  await scenario(async () => {
    await page.evaluate(() => {
      const dt = new DataTransfer();
      dt.setData('text/html', '<b style="font-size:40px">из Word</b>');
      dt.setData('text/plain', 'из Word');
      rtArea.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    });
  });
  check('обычная вставка по-прежнему только текстом', await html(), 'из Word');

  check('plainText показывает обычную эмодзи', await page.evaluate(E =>
        plainText(`Акция ${E}!`), E1), 'Акция 🎁!');

  // иконка у кнопки: выбор и «без иконки»
  await page.evaluate(() => {
    document.body.insertAdjacentHTML('beforeend', `<div id="brow">${btnIconPicker('')}</div>`);
    document.querySelector('#brow .btn-icon-pick').click();
  });
  await page.waitForSelector('.emoji-cell[data-id="111"]');
  await page.click('.emoji-cell[data-id="111"]');
  check('иконка кнопки выбрана', await page.evaluate(() =>
        document.querySelector('#brow .btn-icon-pick').dataset.icon), '111');
  await page.evaluate(() => document.querySelector('#brow .btn-icon-pick').click());
  await page.waitForSelector('.emoji-clear');
  await page.click('.emoji-clear');
  check('«Без иконки» снимает иконку', await page.evaluate(() =>
        document.querySelector('#brow .btn-icon-pick').dataset.icon), '');

  await browser.close();
  console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
  process.exit(failed ? 1 : 0);
})();
