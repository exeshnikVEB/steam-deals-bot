#!/usr/bin/env python3
import asyncio
import json
import os
import time
from pathlib import Path

import aiohttp
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()
CACHE = {"deals": [], "ts": 0}
DATA_FILE = Path(__file__).parent / "data.json"


async def fetch_deals():
    if time.time() - CACHE["ts"] < 1800:
        return CACHE["deals"]
    url = "https://store.steampowered.com/api/featuredcategories/?cc=us&l=russian"
    try:
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
        CACHE["deals"] = sorted(deals, key=lambda x: -x["discount"])
        CACHE["ts"] = time.time()
    except Exception as e:
        print(f"Error: {e}")
    return CACHE["deals"]


def load_history():
    if DATA_FILE.exists():
        d = json.loads(DATA_FILE.read_text())
        return d.get("claimed_ids", [])
    return []


@app.get("/api/deals")
async def api_deals():
    deals = await fetch_deals()
    return JSONResponse(deals)


@app.get("/api/app/{app_id}")
async def api_app(app_id: int):
    url = f"https://store.steampowered.com/api/appdetails/?appids={app_id}&l=russian&cc=us"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json(content_type=None)
        info = data.get(str(app_id), {}).get("data", {})
        return JSONResponse({
            "short_description": info.get("short_description", ""),
            "genres": info.get("genres", []),
            "review_score": info.get("metacritic", {}).get("score", 0),
            "review_score_desc": info.get("review_score_desc", ""),
        })
    except Exception:
        return JSONResponse({})


@app.get("/api/history")
async def api_history():
    claimed = load_history()
    deals = await fetch_deals()
    history = [d for d in deals if str(d["id"]) in claimed]
    return JSONResponse(history)


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (Path(__file__).parent / "webapp" / "index.html").read_text()
    return HTMLResponse(html)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
