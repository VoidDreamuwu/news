#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discord_news_bot.py — 台美科技股新聞機器人,發送到Discord
================================================================
用 Yahoo Finance 搜尋端點 + 鉅亨網(免費、不需要API key)抓指定個股的
最新新聞,過濾時間窗口、去重後,整理成條列式訊息發到Discord webhook。

這支不用LLM做新聞判讀,用「鎖定特定個股 + 時間窗口 + 關聯代號比對」取代
語意判斷——不是每則新聞都翻譯成中文,標題原文呈現(大部分是英文財經
媒體),但公司/代號用中文標註方便辨識。

Webhook網址從環境變數 DISCORD_WEBHOOK_URL 讀取,不寫死在程式碼裡
(GitHub Actions裡用 repository secret 設定)。

執行:
  DISCORD_WEBHOOK_URL=... python discord_news_bot.py --report morning
  DISCORD_WEBHOOK_URL=... python discord_news_bot.py --report afternoon
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
CNYES_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock_news"

# 目標台股代號(鉅亨網用純數字代號比對)——大型權值股 + 中小型科技股
TW_STOCK_CODES = {
    # 大型權值股
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電",
    "3711": "日月光投控", "2303": "聯電", "2382": "廣達", "3231": "緯創",
    "2357": "華碩", "3008": "大立光", "6669": "緯穎",
    # 中小型科技股(台灣中型100/富時羅素最新審核名單,IC設計/PCB/半導體設備為主)
    "2379": "瑞昱", "3034": "聯詠", "2337": "旺宏", "2344": "華邦電",
    "3665": "貿聯-KY", "3189": "景碩", "7750": "新代", "2492": "華新科",
    "3023": "信邦", "6409": "旭隼", "3481": "群創", "6770": "力積電",
    "5347": "世界先進", "3443": "創意", "4958": "臻鼎-KY", "8046": "南電",
    "3037": "欣興", "2368": "金像電",
}
US_TICKERS = [
    # 大型科技股
    ("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Google"),
    ("META", "Meta"), ("AMZN", "Amazon"), ("AMD", "AMD"), ("TSM", "TSMC ADR"), ("AVGO", "Broadcom"),
    # 中小型科技股(半導體/AI供應鏈為主)
    ("MRVL", "Marvell"), ("ON", "ON Semiconductor"), ("ARM", "Arm Holdings"),
    ("SMCI", "Super Micro"), ("ALAB", "Astera Labs"), ("CRDO", "Credo"),
    ("MPWR", "Monolithic Power"), ("LSCC", "Lattice Semi"),
]

MAX_ITEMS_PER_TICKER = 2
MAX_TOTAL_ITEMS = 20
MAX_RELATED_TICKERS = 3   # 關聯代號太多 = 廣泛市場回顧,不是個股新聞,排除
CLICKBAIT_PATTERNS = [
    "stocks to buy", "stock to buy", "should you buy", "better buy", "is it a buy",
    "buy and hold forever", "reasons to", "prediction:", "dividend stocks",
    "stocks to sell", "if you invested", "here's how much", "millionaire",
    "buy now", "top stocks", "hot stocks", "cramer", "does that make",
    "quietly concentrating", "sent a signal", "has grown", "forever hold",
    "high-conviction", "worth holding", "flagged one stock",
]
# 內容農場/評論網站,不是硬新聞來源,即使標題看起來正常也排除
LOW_QUALITY_PUBLISHERS = {
    "motley fool", "insider monkey", "simply wall st.", "simply wall st",
    "thestreet", "thestreet pro", "zacks", "gurufocus", "benzinga",
    "24/7 wall st.", "24/7 wall st", "investorplace", "moneywise", "stockstory",
}


def is_clickbait(title: str, publisher: str = "") -> bool:
    t = title.lower()
    p_lower = publisher.strip().lower()
    if any(bad in p_lower for bad in LOW_QUALITY_PUBLISHERS):
        return True
    return any(p in t for p in CLICKBAIT_PATTERNS)


def fetch_tw_news(since_ts):
    """鉅亨網台股新聞,用『stock』欄位精準比對代號,不是關鍵字搜尋。"""
    try:
        r = requests.get(CNYES_URL, params={"limit": 100}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        data = r.json().get("items", {}).get("data", [])
    except Exception as e:
        print(f"  cnyes fetch failed: {e}")
        return {}

    per_stock = {code: [] for code in TW_STOCK_CODES}
    for n in data:
        pub_ts = n.get("publishAt", 0)
        if pub_ts < since_ts:
            continue
        stocks = n.get("stock", []) or []
        matched = [s for s in stocks if s in TW_STOCK_CODES]
        if not matched:
            continue
        title = n.get("title", "").strip()
        for code in matched:
            if len(per_stock[code]) < MAX_ITEMS_PER_TICKER:
                per_stock[code].append({"title": title, "publisher": "鉅亨網", "ts": pub_ts})
    return per_stock


def fetch_news_for(symbol, since_ts):
    try:
        r = requests.get(SEARCH_URL, params={"q": symbol, "newsCount": 8},
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        news = r.json().get("news", [])
    except Exception as e:
        print(f"  {symbol}: fetch failed: {e}")
        return []
    items = []
    for n in news:
        pub_ts = n.get("providerPublishTime", 0)
        if pub_ts < since_ts:
            continue
        related = n.get("relatedTickers", []) or []
        if symbol not in related:
            continue                                   # 目標代號沒真的被標記,跳過
        if len(related) > MAX_RELATED_TICKERS:
            continue                                   # 關聯代號太多,是廣泛市場新聞不是個股新聞
        title = n.get("title", "").strip()
        publisher = n.get("publisher", "")
        if is_clickbait(title, publisher):
            continue
        items.append({
            "title": title, "publisher": n.get("publisher", ""),
            "link": n.get("link", ""), "ts": pub_ts,
        })
    return items[:MAX_ITEMS_PER_TICKER]


def build_digest(report_type):
    now = datetime.now(timezone.utc)
    tw_now = now.astimezone(timezone(timedelta(hours=8)))  # 台北時間,不依賴runner的本地時區設定
    if report_type == "morning":
        since = now - timedelta(hours=20)
        header = f"📈 台美科技股新聞 | 早報 {tw_now.strftime('%Y-%m-%d')}"
    else:
        since = now - timedelta(hours=7)
        header = f"📊 台美科技股新聞 | 盤後報 {tw_now.strftime('%Y-%m-%d')}"
    since_ts = int(since.timestamp())

    seen_titles = set()
    tw_lines, us_lines = [], []

    tw_news = fetch_tw_news(since_ts)
    for code, name in TW_STOCK_CODES.items():
        for item in tw_news.get(code, []):
            key = item["title"][:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            tw_lines.append(f"• **{name} {code}**：{item['title']}")

    for symbol, label in US_TICKERS:
        for item in fetch_news_for(symbol, since_ts):
            key = item["title"][:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            us_lines.append(f"• **{label}**：{item['title']}（{item['publisher']}）")
        time.sleep(0.3)

    tw_lines = tw_lines[:MAX_TOTAL_ITEMS // 2]
    us_lines = us_lines[:MAX_TOTAL_ITEMS // 2]

    parts = [header, ""]
    if tw_lines:
        parts.append("🇹🇼 **台灣科技股**")
        parts.extend(tw_lines)
        parts.append("")
    if us_lines:
        parts.append("🇺🇸 **美股科技股**")
        parts.extend(us_lines)
    if not tw_lines and not us_lines:
        parts.append("（這個時段沒有偵測到符合條件的重大個股新聞）")

    content = "\n".join(parts)
    if len(content) > 1900:
        content = content[:1900] + "\n...(截斷)"
    return content


def post_to_discord(content):
    if not WEBHOOK_URL:
        raise SystemExit("DISCORD_WEBHOOK_URL is not set (check the repository secret).")
    r = requests.post(WEBHOOK_URL, json={"content": content},
                       headers={"Content-Type": "application/json; charset=utf-8"}, timeout=20)
    print(f"Discord response: {r.status_code}")
    if r.status_code != 204:
        print(r.text)
        r.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", choices=["morning", "afternoon"], required=True)
    args = ap.parse_args()

    print(f"===== {datetime.now().isoformat()} | {args.report} =====")
    content = build_digest(args.report)
    print(content)
    print("Posting to Discord ...")
    post_to_discord(content)


if __name__ == "__main__":
    main()
