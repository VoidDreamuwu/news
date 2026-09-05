# TW/US Tech News Digest Bot

每天兩次（07:30、14:00 台北時間）自動抓取台灣科技股與美股科技股個股新聞，發送到 Discord。

## 運作方式

- 台股新聞來源：鉅亨網（依股票代號精準比對）
- 美股新聞來源：Yahoo Finance（依 relatedTickers 比對 + 過濾內容農場網站）
- 排程：GitHub Actions（免費，不需要本機開機）

## 設定

在 repo 的 **Settings → Secrets and variables → Actions → New repository secret** 新增：

- Name: `DISCORD_WEBHOOK_URL`
- Value: 你的 Discord webhook 網址

## 手動測試

到 repo 的 **Actions** 分頁 → 選 "TW/US Tech News Digest" → **Run workflow** 按鈕，可以立即手動觸發一次（會跑早報版本）。

## 追蹤股票清單

在 `discord_news_bot.py` 的 `TW_STOCK_CODES` 和 `US_TICKERS` 裡增減。
