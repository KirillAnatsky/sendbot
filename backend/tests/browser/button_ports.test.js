// Выходы кнопок «Сообщения» на настоящем Drawflow в Chromium: связь едет
// вместе с кнопкой при перестановке, удалении и добавлении, выход «нет
// реакции» всегда последний, а воронки старого формата (выход был только у
// кнопок без ссылки) открываются с теми же связями, что были нарисованы.
//
//   cd backend/tests/browser && npm i playwright drawflow@0.0.60
//   node button_ports.test.js
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const STATIC = path.join(__dirname, '..', '..', 'app', 'static');
const read = f => fs.readFileSync(path.join(STATIC, f), 'utf8');
const DF = path.dirname(require.resolve('drawflow/dist/drawflow.min.js'));
const CHROME = process.env.CHROMIUM_PATH || undefined;

let failed = 0;
function check(name, ok, info) {
  if (!ok) failed++;
  console.log((ok ? '  ok  ' : 'FAIL  ') + name + (ok ? '' : '  ' + JSON.stringify(info)));
}

(async () => {
  const browser = await chromium.launch(CHROME ? { executablePath: CHROME } : {});
  const page = await browser.newPage({ viewport: { width: 1300, height: 800 } });
  page.on('pageerror', e => { failed++; console.log('FAIL  ошибка на странице: ' + e.message); });
  await page.setContent(`<!doctype html><style>${fs.readFileSync(path.join(DF, 'drawflow.min.css'), 'utf8')}
    #drawflow{position:absolute;left:0;top:0;width:900px;height:700px}</style>
    <div id="drawflow"></div><div id="props" class="props"></div><select id="funnel-trigger"></select><textarea id="ai-chat-text"></textarea>
    <script>${fs.readFileSync(path.join(DF, 'drawflow.min.js'), 'utf8')}</script>
    <script>
      const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
      var TAGS = [];
    </script>
    <script>${read('richtext.js')}</script>
    <script>${read('emoji.js')}</script>
    <script>${read('editor.js')}</script>
    <script>
      editor = new Drawflow(document.getElementById('drawflow'));
      editor.start();
      // что куда ведёт: {выход: подпись цели}
      window.links = id => {
        const n = editor.getNodeFromId(id), out = {};
        Object.entries(n.outputs).forEach(([p, pd]) => {
          out[p] = pd.connections.map(c => editor.getNodeFromId(c.node).data.text).join(',');
        });
        return out;
      };
      window.target = t => editor.addNode('message', 1, 1, 600, 50, 'message',
        { text: t, buttons: [], ports_v: 2 }, '<div>' + t + '</div>', false);
    </script>`);

  console.log('\n--- перестановка, удаление и добавление кнопок ---');
  const r1 = await page.evaluate(() => {
    const data = { text: 'm', ports_v: 2, buttons: [{ label: 'A' }, { label: 'B', url: 'https://b' }, { label: 'C' }] };
    const m = editor.addNode('message', 1, 4, 50, 50, 'message', data, nodeHtml('message', data), false);
    const tA = target('tA'), tB = target('tB'), tC = target('tC');
    editor.addConnection(m, tA, 'output_2', 'input_1');
    editor.addConnection(m, tB, 'output_3', 'input_1');
    editor.addConnection(m, tC, 'output_4', 'input_1');
    window.M = m;
    showProps(m);
    const rows = () => [...document.querySelectorAll('#p-buttons .btn-row')];
    // C на самый верх: два раза «выше»
    moveBtnRow(rows()[2].querySelector('.btn-move'), -1);
    moveBtnRow(rows()[1].querySelector('.btn-move'), -1);
    const afterMove = { order: editor.getNodeFromId(m).data.buttons.map(b => b.label), links: links(m) };
    rows()[1].remove(); applyProps();   // A — теперь вторая
    const afterDel = { order: editor.getNodeFromId(m).data.buttons.map(b => b.label), links: links(m) };
    return { afterMove, afterDel };
  });
  check('C поднялась наверх вместе со своей связью',
    JSON.stringify(r1.afterMove) === JSON.stringify({ order: ['C', 'A', 'B'],
      links: { output_1: '', output_2: 'tC', output_3: 'tA', output_4: 'tB' } }), r1.afterMove);
  check('удалённая кнопка забрала свою связь, остальные на месте',
    JSON.stringify(r1.afterDel) === JSON.stringify({ order: ['C', 'B'],
      links: { output_1: '', output_2: 'tC', output_3: 'tB' } }), r1.afterDel);

  console.log('\n--- выход «нет реакции» ---');
  const r2 = await page.evaluate(() => {
    const m = M;
    document.getElementById('p-noact').checked = true;
    document.getElementById('p-noact-amount').value = '3';
    document.getElementById('p-noact-unit').value = 'days';
    applyProps();
    const tN = target('tN');
    editor.addConnection(m, tN, 'output_4', 'input_1');
    const on = { na: editor.getNodeFromId(m).data.no_action, links: links(m),
                 card: document.querySelector('#node-' + m + ' .df-noact')?.textContent };
    addBtnRow();
    const rows = [...document.querySelectorAll('#p-buttons .btn-row')];
    rows[rows.length - 1].querySelector('.p-btn-label').value = 'D';
    applyProps();
    const added = links(m);
    document.getElementById('p-noact').checked = false;
    applyProps();
    return { on, added, off: links(m), na: editor.getNodeFromId(m).data.no_action };
  });
  check('таймер включён: выход 4, на карточке «3 дн»',
    r2.on.na && r2.on.na.amount === 3 && r2.on.na.unit === 'days'
    && r2.on.links.output_4 === 'tN' && /3 дн/.test(r2.on.card || ''), r2.on);
  check('новая кнопка встала перед «нет реакции», его связь съехала на выход 5',
    JSON.stringify(r2.added) === JSON.stringify({ output_1: '', output_2: 'tC', output_3: 'tB', output_4: '', output_5: 'tN' }), r2.added);
  check('таймер выключен — выход и его связь убраны',
    JSON.stringify(r2.off) === JSON.stringify({ output_1: '', output_2: 'tC', output_3: 'tB', output_4: '' })
    && r2.na === undefined, r2);

  console.log('\n--- воронка старого формата ---');
  const r3 = await page.evaluate(() => {
    editor.clear();
    const old = { drawflow: { Home: { data: {
      2: { id: 2, name: 'message', class: 'message', html: '', typenode: false, pos_x: 0, pos_y: 0,
           data: { text: 'm', buttons: [{ label: 'Сайт', url: 'https://x' }, { label: 'Да' }, { label: 'Нет' }] },
           inputs: { input_1: { connections: [] } },
           outputs: { output_1: { connections: [] },
                      output_2: { connections: [{ node: '3', output: 'input_1' }] },
                      output_3: { connections: [{ node: '4', output: 'input_1' }] } } },
      3: { id: 3, name: 'message', class: 'message', html: '', typenode: false, pos_x: 400, pos_y: 0,
           data: { text: 'после Да', buttons: [] },
           inputs: { input_1: { connections: [{ node: '2', input: 'output_2' }] } }, outputs: { output_1: { connections: [] } } },
      4: { id: 4, name: 'message', class: 'message', html: '', typenode: false, pos_x: 400, pos_y: 200,
           data: { text: 'после Нет', buttons: [] },
           inputs: { input_1: { connections: [{ node: '2', input: 'output_3' }] } }, outputs: { output_1: { connections: [] } } },
    } } } };
    editor.import(upgradeGraphPorts(old));
    redrawAllNodes();
    return { links: links(2), svg: document.querySelectorAll('.connection.output_3, .connection.output_4').length,
             ports: document.querySelectorAll('#node-2 .outputs .output').length };
  });
  check('«Да» и «Нет» ведут туда же, куда вели', JSON.stringify(r3.links) ===
    JSON.stringify({ output_1: '', output_2: '', output_3: 'после Да', output_4: 'после Нет' }), r3);
  check('у ссылки появился свой выход, линии нарисованы', r3.ports === 4 && r3.svg === 2, r3);

  console.log('\n--- вставка блока, скопированного до обновления ---');
  const r4 = await page.evaluate(() => {
    editor.clear();
    pastePayload([
      { oldId: '7', name: 'message', data: { text: 'm', buttons: [{ label: 'Сайт', url: 'https://x' }, { label: 'Да' }] },
        outputs: 2, inputs: 1, pos_x: 0, pos_y: 0, conns: [{ from_port: 'output_2', to: '8' }] },
      { oldId: '8', name: 'message', data: { text: 'после Да', buttons: [], ports_v: 2 },
        outputs: 1, inputs: 1, pos_x: 300, pos_y: 0, conns: [] },
    ]);
    const id = Object.values(editor.export().drawflow.Home.data).find(n => n.data.text === 'm').id;
    return links(id);
  });
  check('связь «Да» переехала на выход 3', JSON.stringify(r4) ===
    JSON.stringify({ output_1: '', output_2: '', output_3: 'после Да' }), r4);

  await browser.close();
  console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
  process.exit(failed ? 1 : 0);
})();
