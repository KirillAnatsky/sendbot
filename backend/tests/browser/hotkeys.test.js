// Сочетания клавиш редактора работают на любой раскладке.
//
// Баг: Ctrl/⌘+C и Ctrl/⌘+V «иногда» не копировали блоки — ровно тогда,
// когда была включена русская раскладка: e.key приходил кириллицей («с»),
// а сверялся с латинской «c». Теперь сверяется физическая клавиша (e.code).
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', '..', 'app', 'static', 'editor.js'), 'utf8');
const fn = src.match(/function hotkey\([\s\S]*?\n}\n/);
if (!fn) { console.error('hotkey() не найдена в editor.js'); process.exit(1); }
const hotkey = new Function(fn[0] + '; return hotkey;')();

let failed = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) failed++;
  console.log((ok ? '  ok  ' : 'FAIL  ') + name + (ok ? '' : `  получено ${got}, ожидалось ${want}`));
}

console.log('\n--- Ctrl+C / Ctrl+V / Ctrl+S / Ctrl+D ---');
for (const [letter, ru] of [['c', 'с'], ['v', 'м'], ['s', 'ы'], ['d', 'в']]) {
  const code = 'Key' + letter.toUpperCase();
  check(`${letter}: английская раскладка`, hotkey({ key: letter, code }, letter), true);
  check(`${letter}: русская раскладка («${ru}»)`, hotkey({ key: ru, code }, letter), true);
  check(`${letter}: Caps Lock`, hotkey({ key: letter.toUpperCase(), code }, letter), true);
}
check('чужая клавиша не срабатывает', hotkey({ key: 'x', code: 'KeyX' }, 'c'), false);
check('без code (старые браузеры) — по букве', hotkey({ key: 'c' }, 'c'), true);

console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
process.exit(failed ? 1 : 0);
