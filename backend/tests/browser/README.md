# Тесты фронтенда

Питоновские тесты (`backend/tests/test_core.py`) проверяют сервер. Здесь то,
что живёт в браузере и на сервере не проверяется никак.

## rt_editor.test.js — редактор текста сообщения

Гоняет `app/static/richtext.js` в настоящем Chromium. Именно настоящем:
`contenteditable`, выделение и `document.execCommand` в jsdom не работают, а
все баги редактора — ровно в них. Оба бага, из-за которых этот тест появился
(оформление нельзя было снять, а в конце строки — выключить), jsdom не ловил.

```
cd backend/tests/browser
npm i playwright && npx playwright install chromium
node rt_editor.test.js
```

Если Chromium уже распакован отдельно, путь к нему можно передать так:
`CHROMIUM_PATH=/путь/к/chrome node rt_editor.test.js`.

## action_node.test.js — блок «Действие» в редакторе воронок

Чистые функции из `app/static/editor.js` на jsdom: набор полей у каждой
операции, число выходов, экранирование, список блоков «Сообщение».

```
cd backend/tests/browser
npm i jsdom
node action_node.test.js
```
