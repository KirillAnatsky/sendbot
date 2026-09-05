// Чистые функции редактора воронок: блоки «Действие» и «Цепочка» —
// какие поля показываются, сколько у блока выходов, что видит пользователь,
// когда выбранная цепочка или блок-адресат уже удалены.
const fs = require('fs');
const { JSDOM } = require('jsdom');

const base = require('path').join(__dirname, '..', '..', 'app', 'static') + '/';
// editor.js берёт plainText/rtPreview из richtext.js — грузим оба, как браузер
const src = fs.readFileSync(base + 'richtext.js', 'utf8') + '\n'
          + fs.readFileSync(base + 'editor.js', 'utf8');
const dom = new JSDOM('<!doctype html><body><div id="props"></div></body>');
global.window = dom.window;
global.document = dom.window.document;

// заглушки того, что editor.js берёт из соседних файлов; недостающие элементы
// страницы подменяем пустышкой — нас интересуют чистые функции блока «Действие»
const stub = new Proxy({}, { get: (t, k) => (k === 'classList' ? { toggle() {}, add() {}, remove() {}, contains: () => false } : () => {}) });
const docProxy = new Proxy(dom.window.document, {
  get(t, k) {
    if (k === 'getElementById') return id => t.getElementById(id) || stub;
    const v = t[k];
    return typeof v === 'function' ? v.bind(t) : v;
  },
});
const ctx = { esc: s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),
              segSummaryText: () => '', mountRichText: () => null,
              makeSegment: () => null, TAGS: [{id: 1, name: 'VIP'}],
              document: docProxy, window: dom.window, Node: dom.window.Node,
              setTimeout, clearTimeout };

// выполняем файл в песочнице и достаём нужные функции
const vm = require('vm');
vm.createContext(ctx);
vm.runInContext(src + '\n;__api = {ACTION_OPS, ACTION_OUTPUTS, actionSummary, actionBody, messageNodes, msgNodeName, portsHint, summary, NODE_META, chainName};', ctx);
const A = ctx.__api;

let failed = 0;
function check(name, fn) {
  try { fn(); console.log('  ok  ' + name); }
  catch (e) { failed++; console.log('FAIL  ' + name + ' — ' + e.message); }
}
function eq(a, b, msg) { if (a !== b) throw new Error((msg || '') + ` ожидалось ${JSON.stringify(b)}, получено ${JSON.stringify(a)}`); }
function has(s, sub) { if (!String(s).includes(sub)) throw new Error(`нет «${sub}» в: ${String(s).slice(0, 200)}`); }

// граф с двумя блоками «Сообщение» — источник для выпадающего списка.
// editor/selectedNodeId объявлены через let, поэтому присваиваем их внутри
// контекста: снаружи через ctx.* до лексической привязки не достучаться.
vm.runInContext(`
  editor = { export: () => ({ drawflow: { Home: { data: {
    2: { name: 'message', data: { text: '<b>Привет</b>, друг' } },
    3: { name: 'message', data: { text: '' } },
    4: { name: 'action', data: { op: 'unsubscribe' } },
  } } } }) };
  selectedNodeId = 9;
`, ctx);

check('операций пять, у «проверить подписку» два выхода', () => {
  eq(A.ACTION_OPS.length, 5);
  eq(A.ACTION_OUTPUTS.check_subscription, 2);
  eq(A.ACTION_OUTPUTS.unsubscribe, 1);
  eq(A.ACTION_OUTPUTS.delete_message, 1);
  eq(A.ACTION_OUTPUTS.add_tag, 1);
});

check('старый формат «Тег» читается как раньше', () => {
  eq(A.actionSummary({ op: 'add_tag', tag: '1' }), 'Добавить тег: VIP');
  eq(A.actionSummary({ op: 'remove_tag', tag: '1' }), 'Снять тег: VIP');
  eq(A.actionSummary({}), 'Добавить тег: ?');  // op отсутствует -> прежнее поведение
});

check('подпись блока для новых операций', () => {
  eq(A.actionSummary({ op: 'unsubscribe' }), 'Отписать от рассылок');
  eq(A.actionSummary({ op: 'check_subscription', channel: '@ch' }), 'Подписан на @ch?');
  has(A.actionSummary({ op: 'delete_message', target: 'last' }), 'последнее');
});

check('подсказка по портам только у развилки', () => {
  eq(A.portsHint('action', { op: 'check_subscription' }), '1: подписан  •  2: нет');
  eq(A.portsHint('action', { op: 'unsubscribe' }), '');
});

check('поля: у каждой операции свои, и только свои', () => {
  const ids = op => (A.actionBody(op, {}).match(/id="([^"]+)"/g) || []).sort().join(',');
  eq(ids('add_tag'), 'id="p-tag"');
  eq(ids('remove_tag'), 'id="p-tag"');
  eq(ids('unsubscribe'), '');
  eq(ids('check_subscription'), 'id="p-channel"');
  eq(ids('delete_message'), 'id="p-target"');
});

check('список сообщений: html вычищен, себя в списке нет', () => {
  const nodes = A.messageNodes();
  eq(nodes.length, 2);                      // блок «Действие» в список не попал
  has(nodes[0].label, 'Привет, друг');      // теги вырезаны
  has(nodes[1].label, 'без текста');
  eq(A.msgNodeName('2').includes('<b>'), false);
});

check('удалённый блок не теряется молча', () => {
  has(A.msgNodeName('77'), 'блок удалён');
  const html = A.actionBody('delete_message', { target: '77' });
  has(html, 'блок удалён');                 // выбор сохранён и виден как проблема
  has(html, 'value="77" selected');
});

check('выбранное значение подставляется в поля', () => {
  has(A.actionBody('check_subscription', { channel: '@my' }), 'value="@my"');
  has(A.actionBody('delete_message', { target: '2' }), 'value="2" selected');
  has(A.actionBody('delete_message', {}), 'value="last" selected');
});

check('канал экранируется, а не подставляется как html', () => {
  const html = A.actionBody('check_subscription', { channel: '"><img src=x>' });
  eq(html.includes('<img'), false);
});

check('блок в палитре называется «Действие»', () => {
  has(A.NODE_META.action.title, 'Действие');
  has(A.summary('action', { op: 'unsubscribe' }), 'Отписать');
});

// ---------- блок «Цепочка» ----------
vm.runInContext("CHAINS = [{id: 5, name: 'Прогрев', is_chain: true}];", ctx);

check('цепочка есть в палитре и в меню связи', () => {
  has(A.NODE_META.chain.title, 'Цепочка');
  eq(A.NODE_META.chain.outputs, 1);   // один выход: «после цепочки»
  eq(A.NODE_META.chain.inputs, 1);
});

check('подпись блока — имя цепочки', () => {
  eq(A.chainName('5'), 'Цепочка: Прогрев');
  eq(A.summary('chain', { funnel_id: '5' }), 'Цепочка: Прогрев');
});

check('удалённая и невыбранная цепочка видны как проблема', () => {
  has(A.chainName('77'), 'удалена');
  has(A.chainName(''), 'не выбрана');
});

console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
process.exit(failed ? 1 : 0);
