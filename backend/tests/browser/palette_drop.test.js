// Блок из палитры падает туда, где его отпустили, — на любом масштабе.
//
// Баг: на 100% всё совпадало, а стоило отдалить или приблизить холст, блок
// уезжал к центру. Холст масштабируется CSS-трансформом от своего ЦЕНТРА,
// а пересчёт координат считал от левого верхнего угла. Проверяем на
// настоящем Drawflow в Chromium: геометрия трансформов в jsdom не считается.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const STATIC = path.join(__dirname, '..', '..', 'app', 'static');
const editorSrc = fs.readFileSync(path.join(STATIC, 'editor.js'), 'utf8');
// берём ровно ту функцию, что работает в редакторе
const fn = editorSrc.match(/function screenToCanvas\([\s\S]*?\n}\n/);
if (!fn) { console.error('screenToCanvas не найдена в editor.js'); process.exit(1); }

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
  await page.setContent(`<!doctype html><style>${fs.readFileSync(path.join(DF, 'drawflow.min.css'), 'utf8')}
    body{margin:0} #drawflow{position:absolute;left:240px;top:90px;width:900px;height:640px}
    .drawflow-node{width:220px}</style>
    <div id="drawflow"></div>
    <script>${fs.readFileSync(path.join(DF, 'drawflow.min.js'), 'utf8')}</script>
    <script>
      window.editor = new Drawflow(document.getElementById('drawflow'));
      editor.start();
      ${fn[0]}
    </script>`);

  console.log('\n--- блок встаёт под курсор при любом масштабе и сдвиге ---');
  for (const [zoom, cx, cy] of [[1, 0, 0], [0.6, 0, 0], [0.35, 0, 0], [1.6, 0, 0],
                                [0.6, -300, 120], [1.3, 250, -180]]) {
    const r = await page.evaluate(([z, cx, cy]) => {
      editor.zoom = z; editor.canvas_x = cx; editor.canvas_y = cy;
      editor.precanvas.style.transform = `translate(${cx}px, ${cy}px) scale(${z})`;
      const c = document.getElementById('drawflow').getBoundingClientRect();
      const X = c.left + 170, Y = c.top + 130;
      const p = screenToCanvas(X, Y);
      const id = editor.addNode('t', 1, 1, p.x, p.y, 't', {}, '<div>блок</div>', false);
      const n = document.getElementById('node-' + id).getBoundingClientRect();
      editor.removeNodeId('node-' + id);
      return { dx: Math.round(n.left - X), dy: Math.round(n.top - Y) };
    }, [zoom, cx, cy]);
    check(`масштаб ${zoom * 100}%, сдвиг ${cx}/${cy}`, Math.abs(r.dx) <= 1 && Math.abs(r.dy) <= 1, r);
  }

  await browser.close();
  console.log(failed ? `\n${failed} проверок упало` : '\nвсе проверки прошли');
  process.exit(failed ? 1 : 0);
})();
