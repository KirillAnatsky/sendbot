// Карточка узла хранится в Drawflow отдельно от DOM, и в базу уезжает именно
// она. Пока refreshNodeHtml правил только DOM, после перезагрузки страницы
// человек снова видел старый текст и старую картинку.
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const base = path.join(__dirname, '..', '..', 'app', 'static') + '/';
const src = fs.readFileSync(base + 'richtext.js', 'utf8') + '\n'
          + fs.readFileSync(base + 'editor.js', 'utf8');

const dom = new JSDOM(`<!doctype html><body>
  <div id="props"></div>
  <div id="node-2"><div class="drawflow_content_node">старая разметка</div></div>
  <div id="node-3"><div class="drawflow_content_node">старая разметка</div></div>
</body>`);

// недостающие элементы страницы подменяем пустышкой: тест про карточки узлов,
// а не про разметку всей админки
const stub = new Proxy({}, { get: (t, k) => (k === 'classList'
  ? { toggle() {}, add() {}, remove() {}, contains: () => false } : () => {}) });
const docProxy = new Proxy(dom.window.document, {
  get(t, k) {
    if (k === 'getElementById') return id => t.getElementById(id) || stub;
    const v = t[k];
    return typeof v === 'function' ? v.bind(t) : v;
  },
});
const ctx = {
  esc: s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
  segSummaryText: () => '', mountRichText: () => null, makeSegment: () => null,
  TAGS: [], document: docProxy, window: dom.window, Node: dom.window.Node,
  setTimeout, clearTimeout,
};
const vm = require('vm');
vm.createContext(ctx);
vm.runInContext(src + '\n;__api = {refreshNodeHtml, redrawAllNodes, nodeHtml};', ctx);
const A = ctx.__api;

let failed = 0;
function check(name, fn) {
  try { fn(); console.log('  ok  ' + name); }
  catch (e) { failed++; console.log('FAIL  ' + name + ' — ' + e.message); }
}
function has(s, sub) {
  if (!String(s).includes(sub)) throw new Error(`нет «${sub}» в: ${String(s).slice(0, 160)}`);
}
function hasNot(s, sub) {
  if (String(s).includes(sub)) throw new Error(`осталось «${sub}» в: ${String(s).slice(0, 160)}`);
}

// Drawflow-заглушка: данные узлов и отдельно сохранённая разметка карточек —
// ровно так устроен настоящий редактор
function makeEditor() {
  const data = {
    2: { id: 2, name: 'message', data: { text: 'НОВЫЙ текст', buttons: [] },
         html: '<div class="df-sub">СТАРЫЙ текст</div>', outputs: {}, inputs: {} },
    3: { id: 3, name: 'message',
         data: { text: 'с картинкой', media: [{ type: 'photo', path: 'media/new.png' }], buttons: [] },
         html: '<div class="df-sub">СТАРЫЙ</div><img src="/media/old.png">', outputs: {}, inputs: {} },
  };
  return {
    drawflow: { drawflow: { Home: { data } } },
    getNodeFromId: id => data[id],
    updateConnectionNodes() {},
    export: () => ({ drawflow: { Home: { data } } }),
  };
}

ctx.__setEditor = makeEditor;
vm.runInContext('editor = __setEditor();', ctx);

// editor объявлен через let — снаружи это лексическая привязка, а не поле
// контекста, поэтому читаем её тем же способом, что и задаём
const ed = () => vm.runInContext('editor', ctx);

check('refreshNodeHtml обновляет и сохранённую разметку, а не только DOM', () => {
  A.refreshNodeHtml('2');
  const stored = ed().drawflow.drawflow.Home.data[2].html;
  has(stored, 'НОВЫЙ текст');
  hasNot(stored, 'СТАРЫЙ текст');
  // в DOM тоже новое
  has(dom.window.document.querySelector('#node-2 .drawflow_content_node').innerHTML, 'НОВЫЙ текст');
});

check('export() отдаёт уже новую разметку — именно она уходит в базу', () => {
  const saved = ed().export().drawflow.Home.data[2].html;
  has(saved, 'НОВЫЙ текст');
});

check('картинка в карточке тоже обновляется', () => {
  A.refreshNodeHtml('3');
  const stored = ed().drawflow.drawflow.Home.data[3].html;
  has(stored, 'media/new.png');
  hasNot(stored, 'media/old.png');
});

check('redrawAllNodes чинит воронку, где старая разметка уже лежит в базе', () => {
  vm.runInContext('editor = __setEditor();', ctx);   // снова «пришло из базы»
  A.redrawAllNodes();
  const d = ed().drawflow.drawflow.Home.data;
  has(d[2].html, 'НОВЫЙ текст');
  has(d[3].html, 'media/new.png');
  hasNot(d[2].html, 'СТАРЫЙ');
  hasNot(d[3].html, 'old.png');
});

check('одиночная картинка рисуется крупной плиткой', () => {
  const html = A.nodeHtml('message', { text: 'т', media: [{ type: 'photo', path: 'media/a.png' }] });
  has(html, 'df-media one');
});

check('несколько картинок — сеткой', () => {
  const two = A.nodeHtml('message', { text: 'т', media: [
    { type: 'photo', path: 'a.png' }, { type: 'photo', path: 'b.png' }] });
  has(two, 'df-media two');
  const many = A.nodeHtml('message', { text: 'т', media: [
    { type: 'photo', path: 'a.png' }, { type: 'photo', path: 'b.png' },
    { type: 'photo', path: 'c.png' }] });
  has(many, 'df-media many');
});

check('картинку нельзя утащить вместо самого блока', () => {
  const html = A.nodeHtml('message', { text: 'т', media: [{ type: 'photo', path: 'media/a.png' }] });
  has(html, 'draggable="false"');   // иначе браузер тянет картинку, а блок стоит
});

console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
process.exit(failed ? 1 : 0);
