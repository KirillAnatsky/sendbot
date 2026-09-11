// Номер на плитке обязан совпадать с колонкой «Step N users» в Google-таблице.
// Это два разных куска кода на двух языках, поэтому сверяем их напрямую:
// один и тот же граф прогоняем через JS-редактор и через питоновскую выгрузку.
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { JSDOM } = require('jsdom');

const base = path.join(__dirname, '..', '..', 'app', 'static') + '/';
const src = fs.readFileSync(base + 'richtext.js', 'utf8') + '\n'
          + fs.readFileSync(base + 'editor.js', 'utf8');

const dom = new JSDOM('<!doctype html><body><div id="props"></div></body>');
const stub = new Proxy({}, { get: (t, k) => (k === 'classList'
  ? { toggle() {}, add() {}, remove() {}, contains: () => false } : () => {}) });
const docProxy = new Proxy(dom.window.document, {
  get(t, k) {
    if (k === 'getElementById') return id => t.getElementById(id) || stub;
    if (k === 'querySelector' || k === 'querySelectorAll') {
      return sel => (k === 'querySelector' ? null : []);
    }
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
vm.runInContext(src + '\n;__api = {stepNumbers, orderedSteps};', ctx);

let failed = 0;
function check(name, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failed++;
  console.log((ok ? '  ok  ' : 'FAIL  ') + name
    + (ok ? '' : `\n        редактор: ${JSON.stringify(got)}\n        выгрузка: ${JSON.stringify(want)}`));
}

// ---------- как строится граф ----------
// Данные узлов настоящие: compile_graph их проверяет, и на пустых падает.
const NODE_DATA = {
  start: {}, note: { text: 'заметка' },
  message: { text: 'текст', buttons: [] },
  delay: { amount: 1, unit: 'hours' },
  condition: { tag: '1' },
  action: { op: 'add_tag', tag: '1' },
  language: { languages: ['ru'] },
  chain: { funnel_id: '1' },
  filter: { filter: { match: 'all', conditions: [{ field: 'language', op: 'equals', value: 'ru' }] } },
};

// узел: [id, тип, {порт: [id назначения, ...]}]
function gui(nodes) {
  const data = {};
  nodes.forEach(([id, name, outs]) => {
    const outputs = {};
    Object.keys(outs || {}).forEach(port => {
      outputs[port] = { connections: outs[port].map(t => ({ node: String(t), output: 'input_1' })) };
    });
    data[id] = { id: Number(id), name, data: JSON.parse(JSON.stringify(NODE_DATA[name])),
                 inputs: { input_1: { connections: [] } }, outputs };
  });
  return { drawflow: { Home: { data } } };
}

// питоновская сторона: compile_graph -> funnel_steps, как в реальной выгрузке
const PY_HELPER = path.join(__dirname, 'steps_match_helper.py');
function pythonSteps(guiObj) {
  const out = execFileSync('python3', [PY_HELPER], { input: JSON.stringify(guiObj) });
  return JSON.parse(out.toString());
}

function compare(name, nodes) {
  const g = gui(nodes);
  vm.runInContext(`editor = { export: () => (${JSON.stringify(g)}) };`, ctx);
  check(name, ctx.__api.stepNumbers(), pythonSteps(g));
}

// ---------- сценарии ----------

compare('прямая цепочка сообщений', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'message', { output_1: ['3'] }],
  ['3', 'message', { output_1: ['4'] }],
  ['4', 'message', {}],
]);

compare('между сообщениями есть задержки и действия', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'message', { output_1: ['3'] }],
  ['3', 'delay', { output_1: ['4'] }],
  ['4', 'action', { output_1: ['5'] }],
  ['5', 'message', {}],
]);

compare('развилка по кнопкам: обе ветки нумеруются по обходу', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'message', { output_2: ['3'], output_3: ['4'] }],
  ['3', 'message', { output_1: ['5'] }],
  ['4', 'message', {}],
  ['5', 'message', {}],
]);

compare('условие с двумя выходами', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'condition', { output_1: ['3'], output_2: ['4'] }],
  ['3', 'message', {}],
  ['4', 'message', {}],
]);

compare('id идут не по порядку — важен обход, а не номера блоков', [
  ['1', 'start', { output_1: ['9'] }],
  ['9', 'message', { output_1: ['3'] }],
  ['3', 'message', { output_1: ['7'] }],
  ['7', 'message', {}],
]);

compare('оторванный блок уходит в конец нумерации', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'message', { output_1: ['3'] }],
  ['3', 'message', {}],
  ['8', 'message', {}],          // ни с чем не соединён
]);

compare('заметки и старт в нумерацию не попадают', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'message', { output_1: ['4'] }],
  ['3', 'note', {}],
  ['4', 'message', {}],
]);

compare('петля назад не зацикливает обход', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'message', { output_1: ['3'] }],
  ['3', 'message', { output_1: ['2'] }],
]);

compare('цепочка и фильтр не сдвигают номера сообщений', [
  ['1', 'start', { output_1: ['2'] }],
  ['2', 'message', { output_1: ['3'] }],
  ['3', 'filter', { output_1: ['4'], output_2: ['5'] }],
  ['4', 'chain', { output_1: ['5'] }],
  ['5', 'message', {}],
]);

// больше 25 сообщений: выгрузка обрежет, редактор обязан показать это же
const many = [['1', 'start', { output_1: ['2'] }]];
for (let i = 2; i <= 31; i++) {
  many.push([String(i), 'message', i < 31 ? { output_1: [String(i + 1)] } : {}]);
}
const g = gui(many);
vm.runInContext(`editor = { export: () => (${JSON.stringify(g)}) };`, ctx);
const js = ctx.__api.stepNumbers();
const py = pythonSteps(g);
check('первые 25 шагов совпадают с выгрузкой',
  Object.fromEntries(Object.entries(js).filter(([, v]) => v <= 25)), py);
check('после 25-го редактор продолжает считать (и пометит как «не в выгрузке»)',
  js['27'], 26);
check('выгрузка на 25 обрывается', py['27'], undefined);

console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
process.exit(failed ? 1 : 0);
