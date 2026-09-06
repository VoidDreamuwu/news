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
INSTITUTIONAL_FLOW_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"     # 三大法人買賣金額統計表(官方,免key)
STOCK_FLOW_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"                # 個股三大法人買賣超(官方,免key)
STOCK_PRICE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"  # 上市個股當日收盤價(官方,免key)
TPEX_PRICE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"  # 上櫃個股當日收盤價(官方,免key)
TW_TIMEZONE = timezone(timedelta(hours=8))

SECTOR_TOP_N = 5
# TWSE官方產業別代碼對照(公司基本資料檔裡的「產業別」欄位是代碼,不是名稱)
INDUSTRY_CODE_NAMES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維",
    "05": "電機機械", "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業",
    "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
    "15": "航運業", "16": "觀光事業", "17": "金融保險", "18": "貿易百貨",
    "20": "其他", "21": "化學工業", "22": "生技醫療業", "23": "油電燃氣業",
    "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業", "27": "通信網路業",
    "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業", "31": "其他電子業",
    "35": "文化創意業", "36": "農業科技業", "37": "電子商務業", "38": "綠能環保業",
    "91": "存託憑證",
}

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

# 自訂概念股族群(手動整理,非官方/非付費資料源的分類,盡量參考業界常見的分法,
# 但不保證跟任何特定看盤軟體的名單一致)。一檔股票可以出現在多個族群裡。
# 每個代號都對照官方上市/上櫃公司名單核對過,避免記錯代碼。
THEME_GROUPS = {
    "LED": ["2393", "6854", "3339"],                          # 億光/錼創科技-KY/泰谷(上櫃)
    "被動元件": ["2327", "2492", "3026"],                      # 國巨/華新科/禾伸堂
    "太陽能": ["3576", "6443", "6244", "3691"],                # 聯合再生/元晶/茂迪(上櫃)/碩禾(上櫃)
    "半導體設備與材料": ["3583", "3131", "3680"],              # 辛耘/弘塑(上櫃)/家登(上櫃)
    "半導體通路": ["3036", "3702"],                            # 文曄/大聯大
    "電池": ["6121", "3211"],                                  # 新普(上櫃)/順達(上櫃)
    "軟體服務": ["6214", "2480", "3029"],                      # 精誠/敦陽科/零壹
}
THEME_TOP_N_DISPLAY = 6

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


def fetch_stock_industry_lookup():
    """上市公司代號→產業別代碼(只有TWSE上市公司有這個欄位,上櫃沒有,產業輪動只涵蓋上市股)。"""
    lookup = {}
    try:
        r = requests.get(TWSE_COMPANY_URL, headers={"Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        for row in r.json():
            code, ind = row.get("公司代號"), row.get("產業別")
            if code and ind:
                lookup[code] = ind
    except Exception as e:
        print(f"  TWSE industry lookup fetch failed: {e}")
    return lookup


def _parse_twse_number(s):
    try:
        return int(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def fetch_institutional_flow():
    """三大法人(外資/投信/自營商)當日買賣金額統計,官方資料,單位新台幣億元。"""
    try:
        r = requests.get(INSTITUTIONAL_FLOW_URL, params={"response": "json", "dayDate": "", "type": "day"},
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        d = r.json()
        rows = d.get("data", [])
        as_of = d.get("date", "")
    except Exception as e:
        print(f"  Institutional flow fetch failed: {e}")
        return None

    net = {"自營商(自行)": 0, "自營商(避險)": 0, "投信": 0, "外資及陸資": 0, "外資自營商": 0}
    for row in rows:
        if len(row) < 4:
            continue
        name, buy, sell, diff = row[0], row[1], row[2], row[3]
        for key in net:
            if name.startswith(key.split("(")[0]) and (("(" not in key) or (key.split("(")[1][:-1] in name)):
                net[key] = _parse_twse_number(diff)
                break
    foreign = net["外資及陸資"] + net["外資自營商"]
    dealer = net["自營商(自行)"] + net["自營商(避險)"]
    trust = net["投信"]
    return {
        "as_of": as_of, "外資": foreign / 1e8, "投信": trust / 1e8,
        "自營商": dealer / 1e8, "合計": (foreign + trust + dealer) / 1e8,
    }


def fetch_stock_price_lookup():
    """全部上市證券當日收盤價,免key,用來把T86的買賣超股數換算成金額。"""
    lookup = {}
    try:
        r = requests.get(STOCK_PRICE_URL, headers={"Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        for row in r.json():
            code = row.get("Code", "").strip()
            price = row.get("ClosingPrice")
            if code and price:
                try:
                    lookup[code] = float(price)
                except ValueError:
                    pass
    except Exception as e:
        print(f"  Stock price lookup fetch failed: {e}")
    return lookup


def fetch_stock_change_lookup():
    """上市(TWSE)+上櫃(TPEx)全部證券當日漲跌幅(%),免key,用來算自訂概念股族群的
    平均漲跌幅。Change欄位是『今日收盤 - 昨日收盤』的點數(帶正負號),
    除以昨收(=收盤-漲跌)換算成百分比。"""
    lookup = {}
    try:
        r = requests.get(STOCK_PRICE_URL, headers={"Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        for row in r.json():
            code = row.get("Code", "").strip()
            try:
                close = float(row.get("ClosingPrice"))
                change = float(row.get("Change"))
                prev_close = close - change
                if prev_close > 0:
                    lookup[code] = change / prev_close * 100
            except (TypeError, ValueError):
                continue
    except Exception as e:
        print(f"  TWSE stock change lookup fetch failed: {e}")
    try:
        try:
            r = requests.get(TPEX_PRICE_URL, headers={"Accept": "application/json"}, timeout=20)
        except requests.exceptions.SSLError:
            r = requests.get(TPEX_PRICE_URL, headers={"Accept": "application/json"}, timeout=20, verify=False)
        r.raise_for_status()
        for row in r.json():
            code = row.get("SecuritiesCompanyCode", "").strip()
            try:
                close = float(row.get("Close"))
                change = float(row.get("Change"))
                prev_close = close - change
                if prev_close > 0:
                    lookup[code] = change / prev_close * 100
            except (TypeError, ValueError):
                continue
    except Exception as e:
        print(f"  TPEx stock change lookup fetch failed: {e}")
    return lookup


def fetch_theme_group_performance(change_lookup):
    """自訂概念股族群(手動整理,非官方分類,一檔股票可以同時屬於多個族群)的
    當日平均漲跌幅排行——等權重平均,不是市值加權,跟一般股市看盤軟體的
    族群漲跌幅可能有落差。"""
    results = []
    for theme, codes in THEME_GROUPS.items():
        pcts = [change_lookup[c] for c in codes if c in change_lookup]
        if not pcts:
            continue
        avg = sum(pcts) / len(pcts)
        results.append((theme, avg, len(pcts)))
    results.sort(key=lambda x: -x[1])
    return results


def fetch_sector_flow(industry_lookup, price_lookup):
    """個股三大法人買賣超股數 x 當日收盤價 = 買賣超金額,依官方產業分類加總,
    抓資金流入/流出最多的產業(單位:新台幣億元)。"""
    try:
        r = requests.get(STOCK_FLOW_URL, params={"response": "json", "date": "", "selectType": "ALLBUT0999"},
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        d = r.json()
        rows = d.get("data", [])
    except Exception as e:
        print(f"  Sector flow fetch failed: {e}")
        return [], []

    from collections import defaultdict
    sector_net = defaultdict(float)
    for row in rows:
        if len(row) < 19:
            continue
        code = row[0].strip()
        ind_code = industry_lookup.get(code)
        price = price_lookup.get(code)
        if not ind_code or not price:
            continue                                    # 沒有產業分類或當天沒收盤價(例如當日暫停交易),跳過
        ind_name = INDUSTRY_CODE_NAMES.get(ind_code, f"產業代碼{ind_code}")
        net_shares = _parse_twse_number(row[18])   # 三大法人買賣超股數(最後一欄)
        sector_net[ind_name] += net_shares * price / 1e8   # 換算成新台幣億元

    ranked = sorted(sector_net.items(), key=lambda x: x[1])
    outflow = [(name, v) for name, v in ranked if v < 0][:SECTOR_TOP_N]
    inflow = [(name, v) for name, v in reversed(ranked) if v > 0][:SECTOR_TOP_N]
    return inflow, outflow


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

    flow = fetch_institutional_flow()
    industry_lookup = fetch_stock_industry_lookup()
    price_lookup = fetch_stock_price_lookup()
    sector_inflow, sector_outflow = fetch_sector_flow(industry_lookup, price_lookup)
    change_lookup = fetch_stock_change_lookup()
    theme_performance = fetch_theme_group_performance(change_lookup)

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
    if flow:
        as_of_fmt = f"{flow['as_of'][:4]}/{flow['as_of'][4:6]}/{flow['as_of'][6:]}" if flow.get("as_of") else "?"
        parts.append(f"💰 **資金方向({as_of_fmt}收盤,三大法人買賣超,億元)**")
        parts.append(f"外資 {flow['外資']:+.1f}　投信 {flow['投信']:+.1f}　自營商 {flow['自營商']:+.1f}　"
                      f"合計 {flow['合計']:+.1f}")
        parts.append("")
    if sector_inflow or sector_outflow:
        parts.append("🔄 **產業輪動(依官方產業分類,三大法人買賣超金額,億元)**")
        if sector_inflow:
            parts.append("流入：" + "、".join(f"{n}(+{v:.1f}億)" for n, v in sector_inflow))
        if sector_outflow:
            parts.append("流出：" + "、".join(f"{n}({v:+.1f}億)" for n, v in sector_outflow))
        parts.append("")
    if theme_performance:
        parts.append("📊 **概念股族群漲跌幅(自訂分類,等權重平均,非官方/非市值加權)**")
        for theme, avg, n in theme_performance[:THEME_TOP_N_DISPLAY]:
            parts.append(f"{theme}({n}檔) {avg:+.2f}%")
        parts.append("")
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
