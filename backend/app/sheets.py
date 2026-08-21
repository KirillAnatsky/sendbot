"""Выгрузка в Google Таблицы через сервисный аккаунт.

Сервисный аккаунт — это «робот» Google с собственной почтой. Ему выдают
доступ к таблице как обычному человеку (кнопка «Поделиться»), и дальше он
пишет туда без всяких логинов и подтверждений. Ключ робота — JSON-файл,
который владелец вставляет в админку.
"""
import time

import aiohttp

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets"

# токен живёт час; держим его в памяти, чтобы не просить каждый раз
_token_cache: dict = {}


class SheetsError(Exception):
    """Ошибка, которую можно показать пользователю как есть."""


def _required_fields(info: dict):
    missing = [k for k in ("client_email", "private_key", "token_uri")
               if not info.get(k)]
    if missing:
        raise SheetsError(
            "Файл ключа не похож на ключ сервисного аккаунта — "
            f"не хватает полей: {', '.join(missing)}. "
            "Нужен JSON, скачанный в разделе Service Accounts → Keys.")


async def get_access_token(info: dict) -> str:
    """Меняет ключ сервисного аккаунта на временный токен доступа."""
    _required_fields(info)
    email = info["client_email"]
    cached = _token_cache.get(email)
    if cached and cached[1] > time.time() + 60:
        return cached[0]

    try:
        from google.auth import crypt, jwt
    except ImportError as e:  # noqa: BLE001
        raise SheetsError("На сервере не установлен google-auth — "
                          "пересоберите приложение") from e

    try:
        signer = crypt.RSASigner.from_service_account_info(info)
    except Exception as e:  # noqa: BLE001
        raise SheetsError(f"Не удалось прочитать ключ: {e}") from e

    now = int(time.time())
    assertion = jwt.encode(signer, {
        "iss": email,
        "scope": SCOPE,
        "aud": info.get("token_uri") or TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    })

    async with aiohttp.ClientSession() as s:
        async with s.post(info.get("token_uri") or TOKEN_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion.decode() if isinstance(assertion, bytes) else assertion,
        }) as r:
            data = await r.json()
            if r.status != 200:
                raise SheetsError(
                    "Google не принял ключ: "
                    f"{data.get('error_description') or data.get('error') or r.status}")

    token = data["access_token"]
    _token_cache[email] = (token, time.time() + int(data.get("expires_in", 3600)))
    return token


def _explain(status: int, body: dict, spreadsheet_id: str, email: str) -> str:
    msg = (body.get("error") or {}).get("message", "")
    if status == 403:
        return (f"Нет доступа к таблице. Откройте её и нажмите «Поделиться», "
                f"дайте права «Редактор» этой почте:\n{email}")
    if status == 404:
        return (f"Таблица не найдена (id: {spreadsheet_id}). "
                "Проверьте, что скопировали идентификатор из адреса таблицы.")
    return f"Google вернул ошибку {status}: {msg or 'без описания'}"


async def _request(session, method, url, token, **kw):
    headers = {"Authorization": f"Bearer {token}"}
    async with session.request(method, url, headers=headers, **kw) as r:
        body = await r.json(content_type=None)
        return r.status, (body or {})


async def write_tabs(info: dict, spreadsheet_id: str, tabs: dict) -> dict:
    """Пишет данные в таблицу: каждый ключ tabs — отдельный лист.

    tabs: {"Воронки": [[строка], [строка], ...], ...}
    Недостающие листы создаются, старое содержимое затирается.
    """
    if not spreadsheet_id:
        raise SheetsError("Не указан идентификатор таблицы")
    token = await get_access_token(info)
    email = info.get("client_email", "")

    async with aiohttp.ClientSession() as s:
        # 1. какие листы уже есть
        status, body = await _request(
            s, "GET", f"{API}/{spreadsheet_id}?fields=sheets.properties.title", token)
        if status != 200:
            raise SheetsError(_explain(status, body, spreadsheet_id, email))
        existing = {sh["properties"]["title"] for sh in body.get("sheets", [])}

        # 2. создаём недостающие
        to_create = [t for t in tabs if t not in existing]
        if to_create:
            status, body = await _request(
                s, "POST", f"{API}/{spreadsheet_id}:batchUpdate", token,
                json={"requests": [{"addSheet": {"properties": {"title": t}}}
                                   for t in to_create]})
            if status != 200:
                raise SheetsError(_explain(status, body, spreadsheet_id, email))

        # 3. чистим и пишем
        written = {}
        for title, rows in tabs.items():
            status, body = await _request(
                s, "POST", f"{API}/{spreadsheet_id}/values/{title}!A1:ZZ:clear", token)
            if status != 200:
                raise SheetsError(_explain(status, body, spreadsheet_id, email))
            status, body = await _request(
                s, "PUT",
                f"{API}/{spreadsheet_id}/values/{title}!A1?valueInputOption=RAW", token,
                json={"values": rows})
            if status != 200:
                raise SheetsError(_explain(status, body, spreadsheet_id, email))
            written[title] = max(0, len(rows) - 1)   # без строки заголовков

    return written
