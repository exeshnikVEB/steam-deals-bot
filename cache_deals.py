#!/usr/bin/env python3
import asyncio
import json
from pathlib import Path
import aiohttp

async def main():
    url = "https://store.steampowered.com/api/featuredcategories/?cc=ru&l=russian&currency=5"
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json(content_type=None)

    deals = []
    for item in data.get("specials", {}).get("items", []):
        if item.get("discount_percent", 0) > 0:
            deals.append({
                "id": item["id"],
                "name": item.get("name", "?"),
                "discount": item.get("discount_percent", 0),
                "old_price": round(item.get("original_price", 0) / 100, 2),
                "new_price": round(item.get("final_price", 0) / 100, 2),
                "image": item.get("header_image", ""),
                "expires": item.get("discount_expiration", 0),
                "url": f"https://store.steampowered.com/app/{item['id']}",
            })
    deals.sort(key=lambda x: -x["discount"])

    Path("docs").mkdir(exist_ok=True)
    Path("docs/deals_cache.json").write_text(json.dumps(deals, ensure_ascii=False))
    print(f"Cached {len(deals)} deals")

asyncio.run(main())
