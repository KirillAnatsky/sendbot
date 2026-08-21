"""Нагрузочный тест по HTTP — запускается С ДРУГОЙ МАШИНЫ (ноутбук, тест-стенд).

Бьёт реальными запросами в API: логин, дашборд, сегменты, списки, статистика.
Меряет время ответа (p50/p95/p99) под параллельной нагрузкой.

    pip install aiohttp
    python apitest.py --url https://funnels.win --login kirill --password ПАРОЛЬ \
        --users 20 --requests 200

    --users N       сколько параллельных пользователей (по умолчанию 10)
    --requests N    сколько запросов на пользователя (50)
    --scenario X    all | dashboard | segments | subscribers
"""
import argparse
import asyncio
import statistics
import time

import aiohttp


async def login(session, url, login_name, password):
    async with session.post(f"{url}/api/auth/login",
                            json={"login": login_name, "password": password}) as r:
        if r.status != 200:
            raise SystemExit(f"Не удалось войти: HTTP {r.status}")
        return (await r.json())["token"]


SCENARIOS = {
    "dashboard": [("GET", "/api/analytics?days=30", None),
                  ("GET", "/api/analytics?days=7", None)],
    "segments": [("POST", "/api/subscribers/search",
                  {"filter": {"match": "all", "conditions": [
                      {"field": "status", "op": "equals", "value": "active"}]},
                   "count_only": True}),
                 ("POST", "/api/subscribers/search",
                  {"filter": {"conditions": []}, "limit": 100})],
    "subscribers": [("POST", "/api/subscribers/search",
                     {"filter": {"conditions": []}, "limit": 500}),
                    ("GET", "/api/bots", None),
                    ("GET", "/api/tags", None)],
}


async def worker(session, url, token, plan, n, results, errors):
    headers = {"Authorization": f"Bearer {token}"}
    for i in range(n):
        method, path, body = plan[i % len(plan)]
        t0 = time.perf_counter()
        try:
            if method == "GET":
                async with session.get(url + path, headers=headers) as r:
                    await r.read()
                    ok = r.status == 200
            else:
                async with session.post(url + path, headers=headers, json=body) as r:
                    await r.read()
                    ok = r.status == 200
            dt = (time.perf_counter() - t0) * 1000
            (results if ok else errors).append(dt)
        except Exception:  # noqa: BLE001
            errors.append((time.perf_counter() - t0) * 1000)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="https://funnels.win")
    ap.add_argument("--login", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--requests", type=int, default=50)
    ap.add_argument("--scenario", default="all")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    plan = (sum(SCENARIOS.values(), []) if args.scenario == "all"
            else SCENARIOS[args.scenario])

    print("=" * 68)
    print(f"  НАГРУЗКА ПО HTTP на {url}")
    print(f"  Параллельных пользователей: {args.users} · запросов каждый: {args.requests}")
    print(f"  Всего запросов: {args.users * args.requests}")
    print("=" * 68)

    conn = aiohttp.TCPConnector(limit=args.users * 2)
    async with aiohttp.ClientSession(connector=conn) as session:
        token = await login(session, url, args.login, args.password)
        results, errors = [], []
        t0 = time.perf_counter()
        await asyncio.gather(*[
            worker(session, url, token, plan, args.requests, results, errors)
            for _ in range(args.users)
        ])
        total = time.perf_counter() - t0

    if not results:
        print("  Все запросы упали — проверь адрес, логин и пароль")
        return
    s = sorted(results)
    p = lambda q: s[min(int(len(s) * q), len(s) - 1)]  # noqa: E731
    print(f"\n  Успешных запросов: {len(results)} · ошибок: {len(errors)}")
    print(f"  Общее время: {total:.1f} с · пропускная способность: {len(results)/total:.0f} запросов/с")
    print(f"\n  Время ответа:")
    print(f"    медиана (p50):  {statistics.median(s):7.0f} мс")
    print(f"    p95:            {p(0.95):7.0f} мс")
    print(f"    p99:            {p(0.99):7.0f} мс")
    print(f"    максимум:       {s[-1]:7.0f} мс")
    verdict = ("отлично" if p(0.95) < 500 else
               "нормально" if p(0.95) < 1500 else "медленно — стоит увеличить сервер")
    print(f"\n  Вывод: {verdict}")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
