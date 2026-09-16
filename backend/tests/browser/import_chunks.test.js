// Большой файл базы уходит на сервер частями: один длинный запрос на 34 тысячи
// строк обрывался, и браузер показывал «Failed to fetch». Резать файл можно
// только по настоящим границам строк — внутри кавычек перенос является частью
// значения, и если порвать запись пополам, в базу уедет мусор.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(
  path.join(__dirname, '..', '..', 'app', 'static', 'app.js'), 'utf8');

// app.js на верхнем уровне трогает страницу и хранилище; тест про чистую
// функцию разбора, поэтому всё окружение — заглушка
const stub = new Proxy({}, { get: (t, k) => (k === 'classList'
  ? { toggle() {}, add() {}, remove() {}, contains: () => false } : () => {}) });
const ctx = {
  localStorage: { getItem: () => '', setItem() {}, removeItem() {} },
  document: new Proxy({}, { get: (t, k) => (
    k === 'getElementById' || k === 'querySelector' ? () => stub : () => {}) }),
  location: { hash: '' },
  setTimeout, clearTimeout, setInterval, clearInterval,
  console, fetch: () => Promise.resolve({ ok: true, json: async () => ({}) }),
  addEventListener() {}, removeEventListener() {},
};
ctx.document.addEventListener = () => {};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(src + '\n;__api = { csvLines, IMPORT_CHUNK };', ctx);
const { csvLines, IMPORT_CHUNK } = ctx.__api;

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

check('обычный файл режется по строкам', () => {
  eq(csvLines('id,name\n1,Аня\n2,Борис'), ['id,name', '1,Аня', '2,Борис']);
});

check('CRLF из Windows не оставляет хвостов', () => {
  eq(csvLines('id,name\r\n1,Аня\r\n2,Борис\r\n'), ['id,name', '1,Аня', '2,Борис']);
});

check('перенос внутри кавычек не режет запись', () => {
  const csv = 'id,about\n1,"первая строка\nвторая строка"\n2,просто';
  eq(csvLines(csv), ['id,about', '1,"первая строка\nвторая строка"', '2,просто']);
});

check('удвоенная кавычка внутри значения не сбивает счёт', () => {
  const csv = 'id,name\n1,"он сказал ""привет""\nи ушёл"\n2,Борис';
  eq(csvLines(csv), ['id,name', '1,"он сказал ""привет""\nи ушёл"', '2,Борис']);
});

check('пустые строки в середине выбрасываются', () => {
  eq(csvLines('id\n1\n\n2\n'), ['id', '1', '2']);
});

check('файл без последнего перевода строки не теряет запись', () => {
  eq(csvLines('id\n1\n2'), ['id', '1', '2']);
});

check('пустой файл — пустой список, а не падение', () => {
  eq(csvLines(''), []);
});

// главное: собранные части в сумме дают исходный файл без потерь и дублей
check('части покрывают весь файл ровно один раз', () => {
  const rows = [...Array(9500)].map((_, i) => `${1000 + i},имя${i}`);
  const lines = csvLines('telegram_id,name\n' + rows.join('\n'));
  const header = lines[0];
  const body = lines.slice(1);
  eq(body.length, 9500, 'все строки на месте:');

  const parts = [];
  for (let from = 0; from < body.length; from += IMPORT_CHUNK) {
    parts.push([header, ...body.slice(from, from + IMPORT_CHUNK)].join('\n'));
  }
  eq(parts.length, Math.ceil(9500 / IMPORT_CHUNK), 'число частей:');
  // в каждой части свой заголовок — сервер разбирает её как самостоятельный файл
  eq(parts.every(p => p.split('\n')[0] === header), true, 'заголовок в каждой части:');
  const back = parts.flatMap(p => p.split('\n').slice(1));
  eq(back, body, 'склеенные части совпадают с исходником:');
});

check('последняя часть не добирает лишнего', () => {
  const body = [...Array(IMPORT_CHUNK + 3)].map((_, i) => String(i));
  const last = body.slice(IMPORT_CHUNK, IMPORT_CHUNK * 2);
  eq(last.length, 3, 'хвост ровно такой, какой остался:');
});

console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
process.exit(failed ? 1 : 0);
