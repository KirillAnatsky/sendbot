"""Демо-воронка: механика «сегментация → лид-магнит → прогрев по дням →
оффер → условие → дожим». Тексты — плейсхолдеры-подсказки, замени на свои.

Запуск:
  локально:      cd backend && python demo_funnel.py
  в докере:      docker compose exec app python demo_funnel.py
"""
import asyncio

DEMO_SPEC = {
    "name": "Демо: прогрев 5 дней (заглушки)",
    "trigger_type": "start",
    "trigger_value": None,
    "nodes": [
        # ---- Этап 1: приветствие + квалификация (3 кнопки-сегмента)
        {"id": "welcome", "type": "message",
         "text": "Привет, {first_name}! [ПРИВЕТСТВИЕ — представление персонажа/бренда]\n\nЧто тебе интереснее всего?",
         "buttons": [
             {"label": "Вариант А", "next": "tag_a"},
             {"label": "Вариант Б", "next": "tag_b"},
             {"label": "Вариант В", "next": "tag_c"},
         ],
         "next": None},

        {"id": "tag_a", "type": "action", "op": "add_tag", "tag": "seg-a", "next": "lm_a"},
        {"id": "tag_b", "type": "action", "op": "add_tag", "tag": "seg-b", "next": "lm_b"},
        {"id": "tag_c", "type": "action", "op": "add_tag", "tag": "seg-c", "next": "lm_c"},

        # ---- Этап 2: лид-магнит по сегменту
        {"id": "lm_a", "type": "message",
         "text": "[ЛИД-МАГНИТ ДЛЯ СЕГМЕНТА А — конкретная польза сразу]\n\nЗавтра пришлю продолжение.",
         "buttons": [], "next": "d1"},
        {"id": "lm_b", "type": "message",
         "text": "[ЛИД-МАГНИТ ДЛЯ СЕГМЕНТА Б]\n\nЗавтра пришлю продолжение.",
         "buttons": [], "next": "d1"},
        {"id": "lm_c", "type": "message",
         "text": "[ЛИД-МАГНИТ ДЛЯ СЕГМЕНТА В]\n\nЗавтра пришлю продолжение.",
         "buttons": [], "next": "d1"},

        # ---- Этап 3: прогрев день 1-2
        {"id": "d1", "type": "delay", "amount": 1, "unit": "days", "next": "warm1"},
        {"id": "warm1", "type": "message",
         "text": "[ДЕНЬ 1 — история/бэкграунд, почему этому можно доверять]",
         "buttons": [], "next": "d2"},
        {"id": "d2", "type": "delay", "amount": 1, "unit": "days", "next": "warm2"},
        {"id": "warm2", "type": "message",
         "text": "[ДЕНЬ 2 — демонстрация результата/кейс]",
         "photo_url": "media/screenshot.png",
         "buttons": [], "next": "d3"},
        {"id": "note_img", "type": "note",
         "text": "⚠️ Для ДЕНЬ 2 нужен скрин media/screenshot.png — залей файл в папку media/ или поменяй путь",
         "about": "warm2"},

        # ---- Этап 4: прогрев день 3-4 (соц. доказательства)
        {"id": "d3", "type": "delay", "amount": 1, "unit": "days", "next": "warm3"},
        {"id": "warm3", "type": "message",
         "text": "[ДЕНЬ 3 — реальные отзывы/примеры других людей]",
         "buttons": [], "next": "d4"},
        {"id": "d4", "type": "delay", "amount": 1, "unit": "days", "next": "warm4"},

        # ---- Этап 5: закрытие возражений
        {"id": "warm4", "type": "message",
         "text": "[ДЕНЬ 4 — ответы на частые вопросы и возражения]",
         "buttons": [], "next": "d5"},
        {"id": "d5", "type": "delay", "amount": 1, "unit": "days", "next": "offer"},

        # ---- Этап 6: оффер (ссылка + кнопка интереса)
        {"id": "offer", "type": "message",
         "text": "[ОФФЕР — предложение + условия]\n\nСсылка ниже. Если интересно, но пока думаешь — жми «Интересно».",
         "buttons": [
             {"label": "Перейти →", "url": "https://example.com/?utm_source=bot"},
             {"label": "Интересно, но думаю", "next": "tag_hot"},
         ],
         "next": "d6"},
        {"id": "tag_hot", "type": "action", "op": "add_tag", "tag": "hot-lead", "next": "hot_msg"},
        {"id": "hot_msg", "type": "message",
         "text": "[ОТВЕТ ИНТЕРЕСУЮЩЕМУСЯ — доп. аргументы, ссылка ещё раз]",
         "buttons": [], "next": None},

        # ---- Этап 7: дожим через день, только тем, кто не нажал «Интересно»
        {"id": "d6", "type": "delay", "amount": 1, "unit": "days", "next": "cond_hot"},
        {"id": "cond_hot", "type": "condition", "tag": "hot-lead",
         "yes": None, "no": "push"},
        {"id": "push", "type": "message",
         "text": "[ДОЖИМ — честное напоминание об оффере, дедлайн только если он настоящий]",
         "buttons": [{"label": "Перейти →", "url": "https://example.com/?utm_source=bot&utm_content=push"}],
         "next": None},
    ],
}


async def main():
    from app.db import SessionLocal, init_db
    from app import ai
    from app.models import Funnel

    await init_db()
    async with SessionLocal() as session:
        tag_ids = await ai.ensure_tags(session, DEMO_SPEC)
        fields = ai.spec_to_funnel_fields(DEMO_SPEC, tag_ids)
        funnel = Funnel(is_active=False, **fields)
        session.add(funnel)
        await session.commit()
        print(f"Создана воронка #{funnel.id}: «{funnel.name}» (выключена).")
        print("Открой её в админке, замени заглушки на свои тексты и включи.")


if __name__ == "__main__":
    asyncio.run(main())
