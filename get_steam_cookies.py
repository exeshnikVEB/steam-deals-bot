#!/usr/bin/env python3
"""
Открывает браузер — войди в Steam, потом нажми Enter в терминале.
Куки сохранятся автоматически.
"""
import json
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

STEAM_FILE = Path(__file__).parent / "steam_config.json"


async def main():
    print("Открываю браузер Steam...")
    print("Войди в свой аккаунт, потом вернись сюда и нажми Enter.\n")

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=False, slow_mo=100)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto("https://store.steampowered.com/login/")

        input(">>> Войди в Steam в браузере, затем нажми Enter здесь...")

        cookies = await ctx.cookies("https://store.steampowered.com")
        await browser.close()

    session_id = next((c["value"] for c in cookies if c["name"] == "sessionid"), None)
    login_secure = next((c["value"] for c in cookies if c["name"] == "steamLoginSecure"), None)

    if not session_id or not login_secure:
        print("❌ Куки не найдены. Убедись что ты вошёл в аккаунт.")
        return

    STEAM_FILE.write_text(json.dumps({
        "session_id": session_id,
        "login_secure": login_secure
    }, indent=2))

    print(f"\n✅ Готово! Куки сохранены в {STEAM_FILE}")
    print("Бот теперь будет автоматически добавлять бесплатные игры.")


asyncio.run(main())
