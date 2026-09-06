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
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
CNYES_URL = "https://api.cnyes.com/media/api/v1/newslist/category/tw_stock_news"
UDN_SEARCH_URL = "https://udn.com/api/more"
TWSE_COMPANY_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"   # 上市公司名單(免key)
TPEX_COMPANY_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"  # 上櫃公司名單(免key)
MOPS_MATERIAL_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"  # 上市公司每日重大訊息(官方強制揭露)
TW_TIMEZONE = timezone(timedelta(hours=8))

# MOPS重大訊息的「主旨」裡,含這些字樣的算行政程序性公告,不是真正有意義的個股新聞,排除
MOPS_ROUTINE_PATTERNS = [
    "更名", "法人說明會", "股票面額", "變更登記", "召開股東", "股東會",
    "補辦", "更正公告", "取得或處分", "背書保證餘額", "資金貸與餘額",
]

# 熱門新題材偵測用:太generic的關鍵字濾掉,不然每天都是這幾個字洗版
KEYWORD_STOPWORDS = {
    "AI", "台股", "大盤", "美股", "台積電", "半導體", "財報", "股價", "投資", "外資",
    "台灣", "營收", "美國", "股市", "科技", "電子股", "上市", "上櫃", "台指期",
}
TRENDING_MIN_MENTIONS = 1
TRENDING_TOP_N = 5
KEYWORD_MIN_COUNT = 2
KEYWORD_TOP_N = 5
THEME_MIN_CODES = 2   # 一篇文章同時標記幾檔以上非清單股票代號,就算「產業題材新聞」
THEME_TOP_N = 4

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


def fetch_cnyes_articles(since_ts):
    """鉅亨網台股新聞原始清單(時間窗口內),給watchlist比對/熱門股偵測/題材偵測共用。"""
    try:
        r = requests.get(CNYES_URL, params={"limit": 100}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        data = r.json().get("items", {}).get("data", [])
    except Exception as e:
        print(f"  cnyes fetch failed: {e}")
        return []
    return [n for n in data if n.get("publishAt", 0) >= since_ts]


def match_watchlist_tw_news(articles):
    """從鉅亨網原始清單裡,篩出跟固定watchlist(TW_STOCK_CODES)相關的新聞。"""
    per_stock = {code: [] for code in TW_STOCK_CODES}
    for n in articles:
        stocks = n.get("stock", []) or []
        matched = [s for s in stocks if s in TW_STOCK_CODES]
        if not matched:
            continue
        title = n.get("title", "").strip()
        for code in matched:
            if len(per_stock[code]) < MAX_ITEMS_PER_TICKER:
                per_stock[code].append({"title": title, "publisher": "鉅亨網", "ts": n.get("publishAt", 0)})
    return per_stock


def _parse_roc_datetime(date_str, time_str):
    """民國年日期(YYYMMDD)+時間(H...HMMSS,可能缺前導0)轉成有時區的datetime。"""
    date_str = (date_str or "").strip()
    time_str = (time_str or "0").strip().zfill(6)
    if len(date_str) < 7:
        return None
    year = int(date_str[:-4]) + 1911
    month, day = int(date_str[-4:-2]), int(date_str[-2:])
    hour, minute, sec = int(time_str[:-4] or 0), int(time_str[-4:-2]), int(time_str[-2:])
    return datetime(year, month, day, hour, minute, sec, tzinfo=TW_TIMEZONE)


def fetch_mops_material_news(since_ts):
    """上市公司每日重大訊息(官方強制揭露,MOPS),只比對固定watchlist,當作『官方認證版』新聞。
    排除掉行政程序性公告(更名、股東會通知等),只留真正跟營運/財務相關的重大訊息。"""
    try:
        r = requests.get(MOPS_MATERIAL_URL, headers={"Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        print(f"  MOPS material fetch failed: {e}")
        return {}

    per_stock = {}
    for row in rows:
        code = row.get("公司代號", "")
        if code not in TW_STOCK_CODES:
            continue
        subject = (row.get("主旨 ") or row.get("主旨") or "").replace("\r", "").replace("\n", " ").strip()
        if any(p in subject for p in MOPS_ROUTINE_PATTERNS):
            continue
        dt = _parse_roc_datetime(row.get("發言日期"), row.get("發言時間"))
        if dt is None or int(dt.timestamp()) < since_ts:
            continue
        per_stock.setdefault(code, []).append({"title": subject, "publisher": "MOPS官方公告", "ts": int(dt.timestamp())})
    return per_stock


def fetch_company_name_lookup():
    """上市(TWSE)+上櫃(TPEx)公司代號→簡稱,用來幫清單外的熱門股標名字。"""
    lookup = {}
    try:
        r = requests.get(TWSE_COMPANY_URL, headers={"Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        for row in r.json():
            code, name = row.get("公司代號"), row.get("公司簡稱")
            if code and name:
                lookup[code] = name
    except Exception as e:
        print(f"  TWSE company list fetch failed: {e}")
    try:
        try:
            r = requests.get(TPEX_COMPANY_URL, headers={"Accept": "application/json"}, timeout=20)
        except requests.exceptions.SSLError:
            # tpex.org.tw的憑證有已知技術性瑕疵(缺Subject Key Identifier),在部分環境下驗證失敗;
            # 這裡只是拿公開公司名單(非敏感資料),容錯改用不驗證憑證的方式重試一次。
            print("  TPEx SSL verify failed, retrying without verification ...")
            r = requests.get(TPEX_COMPANY_URL, headers={"Accept": "application/json"}, timeout=20, verify=False)
        r.raise_for_status()
        for row in r.json():
            code, name = row.get("SecuritiesCompanyCode"), row.get("CompanyAbbreviation")
            if code and name:
                lookup[code] = name
    except Exception as e:
        print(f"  TPEx company list fetch failed: {e}")
    return lookup


def detect_theme_articles(articles, name_lookup):
    """一篇文章『同時』點名多檔(非清單)股票代號 = 供應鏈/產業題材新聞。
    這種新聞常常只出現一次(不會像單一熱股那樣被多篇文章重複報導),
    如果只看『單一代號被提及次數』會被同一天其他代號的提及次數排擠掉,
    所以獨立判斷:不看次數,看『一篇文章裡有沒有一次點名一整組概念股』。"""
    lines, used_titles = [], set()
    for n in articles:
        stocks = n.get("stock", []) or []
        codes = [s for s in stocks if s.isdigit() and len(s) == 4 and s not in TW_STOCK_CODES]
        codes = list(dict.fromkeys(codes))
        if len(codes) < THEME_MIN_CODES:
            continue
        title = n.get("title", "").strip()
        if title in used_titles:
            continue
        used_titles.add(title)
        names = "、".join(f"{name_lookup.get(c, c)}({c})" for c in codes[:4])
        lines.append(f"• [{names}]：{title}")
        if len(lines) >= THEME_TOP_N:
            break
    return lines, used_titles


def detect_trending_stocks(articles, name_lookup, exclude_titles=None):
    """清單外、短時間內被多篇新聞提及的股票代號——用『提及次數暴增』當『新興熱門』的代理指標。
    exclude_titles:已經被detect_theme_articles抓走的文章標題,這裡跳過避免同一則新聞重複出現。"""
    from collections import Counter
    exclude_titles = exclude_titles or set()
    counts = Counter()
    sample_title = {}
    for n in articles:
        title = n.get("title", "").strip()
        if title in exclude_titles:
            continue
        stocks = n.get("stock", []) or []
        for code in stocks:
            if not code.isdigit() or len(code) != 4:
                continue                                # 非台股數字代號(例如美股的US-NVDA),跳過
            if code in TW_STOCK_CODES:
                continue                                # 已經在固定清單裡的不算「新發現」
            counts[code] += 1
            sample_title.setdefault(code, title)

    trending = [(code, cnt) for code, cnt in counts.items() if cnt >= TRENDING_MIN_MENTIONS]
    trending.sort(key=lambda x: -x[1])
    lines = []
    for code, cnt in trending[:TRENDING_TOP_N]:
        name = name_lookup.get(code, code)
        lines.append(f"• **{name} {code}**（{cnt}則新聞）：{sample_title[code]}")
    return lines


def detect_trending_keywords(articles):
    """新聞關鍵字標籤的出現頻率,抓短時間內突然變熱的題材(排除太generic的詞)。"""
    from collections import Counter
    counts = Counter()
    for n in articles:
        for kw in (n.get("keyword") or []):
            kw = kw.strip()
            if not kw or kw in KEYWORD_STOPWORDS or len(kw) < 2:
                continue
            counts[kw] += 1
    top = [(kw, c) for kw, c in counts.items() if c >= KEYWORD_MIN_COUNT]
    top.sort(key=lambda x: -x[1])
    return top[:KEYWORD_TOP_N]


def fetch_udn_news(code, name, since_ts):
    """經濟日報/udn.com,用『公司名稱是否出現在標題』二次過濾(搜尋結果本身不夠精準)。"""
    try:
        r = requests.get(UDN_SEARCH_URL, params={"page": 1, "id": f"search:{code}",
                                                     "channelId": 2, "type": "searchword"},
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        items = r.json().get("lists", []) or []
    except Exception as e:
        print(f"  udn {code}: fetch failed: {e}")
        return []
    results = []
    for it in items:
        title = it.get("title", "").strip()
        if name not in title:
            continue                                   # 公司名稱沒出現在標題,搜尋結果不夠相關,跳過
        dt_str = it.get("time", {}).get("dateTime", "")
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TW_TIMEZONE)
        except ValueError:
            continue
        pub_ts = int(dt.timestamp())
        if pub_ts < since_ts:
            continue
        results.append({"title": title, "publisher": "經濟日報", "ts": pub_ts})
    return results[:MAX_ITEMS_PER_TICKER]


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

    cnyes_articles = fetch_cnyes_articles(since_ts)
    tw_news = match_watchlist_tw_news(cnyes_articles)
    for code, name in TW_STOCK_CODES.items():
        combined = list(tw_news.get(code, []))
        combined.extend(fetch_udn_news(code, name, since_ts))
        time.sleep(0.2)
        for item in combined[:MAX_ITEMS_PER_TICKER]:
            key = item["title"][:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            tw_lines.append(f"• **{name} {code}**：{item['title']}")

    mops_news = fetch_mops_material_news(since_ts)
    mops_lines = []
    for code, name in TW_STOCK_CODES.items():
        for item in mops_news.get(code, [])[:MAX_ITEMS_PER_TICKER]:
            mops_lines.append(f"• **{name} {code}**：{item['title']}")

    name_lookup = fetch_company_name_lookup()
    theme_lines, theme_titles = detect_theme_articles(cnyes_articles, name_lookup)
    trending_lines = detect_trending_stocks(cnyes_articles, name_lookup, exclude_titles=theme_titles)
    trending_keywords = detect_trending_keywords(cnyes_articles)

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
        parts.append("")
    if mops_lines:
        parts.append("📋 **官方重大訊息(MOPS強制揭露,跟媒體報導交叉對照)**")
        parts.extend(mops_lines[:6])
        parts.append("")
    if theme_lines:
        parts.append("🏭 **產業題材新聞(單篇同時點名多檔概念股)**")
        parts.extend(theme_lines)
        parts.append("")
    if trending_lines:
        parts.append("🔥 **新興熱門股(不在固定清單,短時間內新聞暴增)**")
        parts.extend(trending_lines)
        parts.append("")
    if trending_keywords:
        kw_str = "、".join(f"{kw}({c}則)" for kw, c in trending_keywords)
        parts.append(f"💡 **今日熱門題材關鍵字**：{kw_str}")
    if not tw_lines and not us_lines and not mops_lines and not trending_lines and not theme_lines:
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
