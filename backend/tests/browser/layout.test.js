// «Разложить» должно строить СТОЛБЦЫ: каждая ветка — своя вертикальная
// дорожка. Именно этого не хватало на воронке с десятью языками.
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const base = path.join(__dirname, '..', '..', 'app', 'static') + '/';
const src = fs.readFileSync(base + 'richtext.js', 'utf8') + '\n'
          + fs.readFileSync(base + 'editor.js', 'utf8');

const dom = new JSDOM('<!doctype html><body></body>');
const stub = new Proxy({}, { get: (t, k) => (k === 'classList'
  ? { toggle() {}, add() {}, remove() {}, contains: () => false } : () => {}) });

// карточки узлов: у каждой своя высота, как в настоящем редакторе
const CARD_H = {};
const els = {};
// узел редактора: нам важны только позиция и высота, остальное — заглушки,
// чтобы decoratePorts и перерисовка номеров не спотыкались
function makeEl(id) {
  return {
    style: {},
    get offsetHeight() { return CARD_H[id] || 120; },
    querySelector: () => null,
    querySelectorAll: () => [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 300, height: CARD_H[id] || 120 }),
  };
}
const docProxy = new Proxy(dom.window.document, {
  get(t, k) {
    if (k === 'getElementById') return id => {
      if (String(id).startsWith('node-')) {
        const nid = String(id).slice(5);
        return (els[nid] = els[nid] || makeEl(nid));
      }
      return t.getElementById(id) || stub;
    };
    if (k === 'querySelector') return () => null;
    if (k === 'querySelectorAll') return () => [];
    const v = t[k];
    return typeof v === 'function' ? v.bind(t) : v;
  },
});

const ctx = {
  esc: s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
  segSummaryText: () => '', mountRichText: () => null, makeSegment: () => null,
  flashStatus: () => {}, decoratePorts: () => {},
  TAGS: [], document: docProxy, window: dom.window, Node: dom.window.Node,
  setTimeout, clearTimeout,
};
const vm = require('vm');
vm.createContext(ctx);
vm.runInContext(src + '\n;__api = {arrangeVertical};', ctx);

let failed = 0;
function check(name, fn) {
  try { fn(); console.log('  ok  ' + name); }
  catch (e) { failed++; console.log('FAIL  ' + name + ' — ' + e.message); }
}
function eq(a, b, msg) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(`${msg || ''} ожидалось ${JSON.stringify(b)}, получено ${JSON.stringify(a)}`);
  }
}

// граф: [id, тип, {порт: [цели]}]
function build(nodes) {
  const data = {};
  nodes.forEach(([id, name, outs]) => {
    const outputs = {};
    Object.keys(outs || {}).forEach(port => {
      outputs[port] = { connections: outs[port].map(t => ({ node: String(t), output: 'input_1' })) };
    });
    data[id] = { id: Number(id), name, data: { text: 'т', buttons: [] },
                 inputs: {}, outputs, pos_x: 0, pos_y: 0 };
  });
  const editor = {
    drawflow: { drawflow: { Home: { data } } },
    export: () => ({ drawflow: { Home: { data } } }),
    updateConnectionNodes() {},
    getNodeFromId: id => data[id],
  };
  ctx.__ed = editor;
  vm.runInContext('editor = __ed;', ctx);
  return data;
}

const pos = data => Object.fromEntries(
  Object.keys(data).map(id => [id, { x: data[id].pos_x, y: data[id].pos_y }]));

// ---------- сценарии ----------

check('линейная воронка идёт одним столбцом вниз', () => {
  const d = build([
    ['1', 'start', { output_1: ['2'] }],
    ['2', 'message', { output_1: ['3'] }],
    ['3', 'message', { output_1: ['4'] }],
    ['4', 'message', {}],
  ]);
  ctx.__api.arrangeVertical();
  const p = pos(d);
  eq([p['1'].x, p['2'].x, p['3'].x, p['4'].x], [60, 60, 60, 60], 'один столбец:');
  const ys = ['1', '2', '3', '4'].map(id => p[id].y);
  eq(ys.every((v, i) => i === 0 || v > ys[i - 1]), true, 'строго вниз:');
});

check('десять языковых веток становятся десятью столбцами', () => {
  const outs = {};
  const nodes = [['1', 'start', { output_1: ['2'] }]];
  // сообщение с 10 кнопками -> 10 действий -> у каждого своё сообщение
  for (let i = 0; i < 10; i++) {
    outs['output_' + (i + 2)] = [String(10 + i)];
    nodes.push([String(10 + i), 'action', { output_1: [String(30 + i)] }]);
    nodes.push([String(30 + i), 'message', {}]);
  }
  nodes.splice(1, 0, ['2', 'message', outs]);
  const d = build(nodes);
  ctx.__api.arrangeVertical();
  const p = pos(d);

  // десять действий — десять разных столбцов, в одном ряду
  const actX = [...Array(10)].map((_, i) => p[String(10 + i)].x);
  eq(new Set(actX).size, 10, 'все в разных столбцах:');
  eq(new Set([...Array(10)].map((_, i) => p[String(10 + i)].y)).size, 1, 'один ряд:');
  // столбцы идут в порядке кнопок, слева направо
  eq(actX, [...actX].sort((a, b) => a - b), 'по порядку кнопок:');

  // сообщение каждой ветки стоит ПОД своим действием, в том же столбце
  for (let i = 0; i < 10; i++) {
    eq(p[String(30 + i)].x, p[String(10 + i)].x, `ветка ${i} в своём столбце:`);
    eq(p[String(30 + i)].y > p[String(10 + i)].y, true, `ветка ${i} ниже:`);
  }
});

check('ветки разной длины не залезают друг на друга', () => {
  const d = build([
    ['1', 'start', { output_1: ['2'] }],
    // левая ветка длинная, правая короткая
    ['2', 'message', { output_2: ['3'], output_3: ['7'] }],
    ['3', 'message', { output_1: ['4'] }],
    ['4', 'message', { output_1: ['5'] }],
    ['5', 'message', {}],
    ['7', 'message', {}],
  ]);
  ctx.__api.arrangeVertical();
  const p = pos(d);
  eq(p['3'].x, p['2'].x, 'первая ветка продолжает столбец родителя:');
  eq(p['7'].x > p['3'].x, true, 'вторая ветка правее:');
  // вся длинная ветка в одном столбце
  eq([p['3'].x, p['4'].x, p['5'].x], [p['3'].x, p['3'].x, p['3'].x], 'длинная ветка ровно:');
});

check('сошедшиеся ветки не дублируют узел', () => {
  const d = build([
    ['1', 'start', { output_1: ['2'] }],
    ['2', 'message', { output_2: ['3'], output_3: ['4'] }],
    ['3', 'message', { output_1: ['5'] }],
    ['4', 'message', { output_1: ['5'] }],
    ['5', 'message', {}],           // общий финал двух веток
  ]);
  ctx.__api.arrangeVertical();
  const p = pos(d);
  eq(p['5'].x, p['3'].x, 'общий узел остаётся в столбце первой ветки:');
  eq(p['5'].y > p['3'].y && p['5'].y > p['4'].y, true, 'и ниже обеих:');
});

check('оторванный кусок уходит вниз, а не влезает первым рядом', () => {
  const d = build([
    ['1', 'start', { output_1: ['2'] }],
    ['2', 'message', { output_1: ['3'] }],
    ['3', 'message', {}],
    // ни с чем не соединённая цепочка — как бывает после правок AI
    ['8', 'action', { output_1: ['9'] }],
    ['9', 'action', {}],
  ]);
  ctx.__api.arrangeVertical();
  const p = pos(d);
  const lowest = Math.max(p['1'].y, p['2'].y, p['3'].y);
  eq(p['8'].y > lowest, true, 'оторванное ниже воронки:');
  eq(p['9'].y > p['8'].y, true, 'и само по себе разложено:');
  eq(p['8'].x, 60, 'со своего левого края:');
});

check('петля назад не вешает раскладку', () => {
  const d = build([
    ['1', 'start', { output_1: ['2'] }],
    ['2', 'message', { output_1: ['3'] }],
    ['3', 'message', { output_1: ['2'] }],
  ]);
  ctx.__api.arrangeVertical();
  const p = pos(d);
  eq(p['3'].y > p['2'].y, true, 'узлы всё равно разложены:');
});

check('высокая карточка раздвигает ряды, а не наезжает', () => {
  const d = build([
    ['1', 'start', { output_1: ['2'] }],
    ['2', 'message', { output_1: ['3'] }],
    ['3', 'message', {}],
  ]);
  CARD_H['2'] = 400;               // карточка с крупной картинкой
  ctx.__api.arrangeVertical();
  const p = pos(d);
  eq(p['3'].y - p['2'].y > 400, true, 'следующий ряд ниже высокой карточки:');
  delete CARD_H['2'];
});

check('заметка встаёт рядом со своим блоком, а не в его столбце', () => {
  const d = build([
    ['1', 'start', { output_1: ['2'] }],
    ['2', 'message', {}],
  ]);
  d['7'] = { id: 7, name: 'note', data: { text: 'внимание', about: '2' },
             inputs: {}, outputs: {}, pos_x: 0, pos_y: 0 };
  ctx.__api.arrangeVertical();
  const p = pos(d);
  eq(p['7'].y, p['2'].y, 'в одном ряду со своим блоком:');
  eq(p['7'].x > p['2'].x, true, 'и правее него:');
});

console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
process.exit(failed ? 1 : 0);
