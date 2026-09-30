#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
research_cards.py — 卡片式研究筆記 + 梯隊表產生器 + 發布前品管檢查

對應原始方法論的「二、累積」跟「三、產出(梯隊表部分)」跟「四、品管」三段。
「一、輸入」那段(DIGITIMES/SemiAnalysis/法說會/ECTC這些來源)不在這支處理,
那是不同規模的爬蟲/來源整合,跟目前機器人在抓的cnyes/udn台股新聞不是
同一件事,這裡不做。

每張卡片鎖定一個料件/主題,欄位固定:
  物理主軸: 為什麼「現在」變關鍵(一段文字)
  玩家與梯隊: [{tier, company, code, stars(1-5), reason}, ...]
  硬數字: [{metric, value, unit, source}, ...]  (單位一定要填,品管會檢查)
  反方: 為什麼不會被別的方案取代(一段文字)
  一手來源: [{title, url}, ...]

存放: research_cards.json (跟theme_candidate_log.csv一樣,靠GitHub Actions
commit回repo做持久化,如果你透過workflow跑;本機使用就是一般檔案)

CLI用法:
  python research_cards.py list                      # 列出所有卡片
  python research_cards.py show <卡片名稱>             # 看單一卡片完整內容+梯隊表+QA結果
  python research_cards.py qa <卡片名稱>                # 只跑品管檢查
  python research_cards.py tier-table <卡片名稱>        # 只輸出梯隊表(markdown)
  python research_cards.py delete <卡片名稱>            # 刪除卡片

新增/更新卡片請直接呼叫 upsert_card() (見下方函式),或是叫我幫你依訪談內容寫入。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CARDS_FILE = "research_cards.json"
TW_TZ = timezone(timedelta(hours=8))

REQUIRED_FIELDS = ["物理主軸", "玩家與梯隊", "硬數字", "反方", "一手來源"]


def load_cards():
    if not os.path.exists(CARDS_FILE):
        return {}
    with open(CARDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_cards(cards):
    with open(CARDS_FILE, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)


def upsert_card(name, 物理主軸=None, 玩家與梯隊=None, 硬數字=None, 反方=None, 一手來源=None):
    """新增或更新一張卡片。已存在的欄位不傳就沿用舊值,方便分次補齊。
    玩家與梯隊: [{"tier": "第一梯隊", "company": "村田", "code": None,
                 "stars": 5, "reason": "..."}, ...]
    硬數字: [{"metric": "電容密度", "value": "12", "unit": "μF/mm²", "source": "..."}, ...]
    一手來源: [{"title": "...", "url": "..."}, ...]
    """
    cards = load_cards()
    now = datetime.now(TW_TZ).strftime("%Y-%m-%d")
    existing = cards.get(name, {})
    card = {
        "物理主軸": 物理主軸 if 物理主軸 is not None else existing.get("物理主軸", ""),
        "玩家與梯隊": 玩家與梯隊 if 玩家與梯隊 is not None else existing.get("玩家與梯隊", []),
        "硬數字": 硬數字 if 硬數字 is not None else existing.get("硬數字", []),
        "反方": 反方 if 反方 is not None else existing.get("反方", ""),
        "一手來源": 一手來源 if 一手來源 is not None else existing.get("一手來源", []),
        "建立日期": existing.get("建立日期", now),
        "更新日期": now,
    }
    cards[name] = card
    save_cards(cards)
    return card


def delete_card(name):
    cards = load_cards()
    if name in cards:
        del cards[name]
        save_cards(cards)
        return True
    return False


def render_tier_table(card):
    """把card["玩家與梯隊"]排成方法論指定的markdown表格。"""
    rows = card.get("玩家與梯隊", [])
    if not rows:
        return "(尚未填寫玩家與梯隊)"
    lines = ["| 梯隊 | 公司 | 題材純度 × 技術力 | 一句話理由 |",
             "|---|---|---|---|"]
    tier_order = {}
    for r in rows:
        tier_order.setdefault(r.get("tier", "未分類"), len(tier_order))
    sorted_rows = sorted(rows, key=lambda r: (tier_order.get(r.get("tier", "未分類"), 999),
                                               -r.get("stars", 0)))
    for r in sorted_rows:
        stars = "⭐" * int(r.get("stars", 0))
        code = f"({r['code']})" if r.get("code") else ""
        lines.append(f"| {r.get('tier','')} | {r.get('company','')}{code} | {stars} | {r.get('reason','')} |")
    return "\n".join(lines)


def qa_check(card, name=""):
    """發布前品管檢查——只做結構性、機械式能檢查的部分:
      - 5個固定欄位都有填內容(不是空字串/空list)
      - 硬數字每一筆都有標單位、有來源
      - 玩家與梯隊每一筆都有一句話理由
      - 一手來源至少一筆、且是網址格式
    語意層的檢查(例如"電阻/電感有沒有搞混""數字合不合理")機器沒辦法自動判斷,
    這裡只能提醒去對照原始checklist手動確認,不能取代人工看過一遍。"""
    issues = []
    warnings = []

    for field in REQUIRED_FIELDS:
        val = card.get(field)
        if not val:
            issues.append(f"「{field}」是空的")

    for i, hn in enumerate(card.get("硬數字", [])):
        if not hn.get("unit"):
            issues.append(f"硬數字第{i+1}筆「{hn.get('metric','?')}」沒有標單位")
        if not hn.get("source"):
            warnings.append(f"硬數字第{i+1}筆「{hn.get('metric','?')}」沒有標來源")

    for i, r in enumerate(card.get("玩家與梯隊", [])):
        if not r.get("reason"):
            issues.append(f"梯隊表第{i+1}列「{r.get('company','?')}」沒有一句話理由")
        if not r.get("stars"):
            warnings.append(f"梯隊表第{i+1}列「{r.get('company','?')}」沒有給星等")

    sources = card.get("一手來源", [])
    if not sources:
        issues.append("完全沒有一手來源")
    else:
        for i, s in enumerate(sources):
            url = s.get("url", "")
            if not (url.startswith("http://") or url.startswith("https://")):
                issues.append(f"一手來源第{i+1}筆「{s.get('title','?')}」網址格式看起來不對: {url}")

    print(f"===== 品管檢查: {name} =====")
    if not issues and not warnings:
        print("✅ 結構性檢查全部通過")
    else:
        for msg in issues:
            print(f"❌ {msg}")
        for msg in warnings:
            print(f"⚠️  {msg}")
    print("\n以下仍需人工對照原始checklist手動確認(機器判斷不了):")
    print("  - 單位對不對(例如電容密度是μF/mm²不是μF/mm)")
    print("  - 電阻vs電感有沒有用對(瞬態去耦看ESL寄生電感,直流壓降才看電阻)")
    print("  - 反方論證站不站得住腳(不是只是形式上有寫)")
    return issues, warnings


def summary_line(name, card):
    n_tiers = len(card.get("玩家與梯隊", []))
    n_sources = len(card.get("一手來源", []))
    return f"「{name}」更新於{card.get('更新日期','?')}｜{n_tiers}家廠商入梯隊表｜{n_sources}筆一手來源"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["list", "show", "qa", "tier-table", "delete"])
    ap.add_argument("name", nargs="?")
    args = ap.parse_args()

    cards = load_cards()

    if args.action == "list":
        if not cards:
            print("目前沒有任何卡片")
            return
        for name, card in cards.items():
            print(summary_line(name, card))
        return

    if not args.name:
        print("這個指令需要指定卡片名稱")
        sys.exit(1)

    if args.name not in cards:
        print(f"找不到卡片「{args.name}」")
        sys.exit(1)
    card = cards[args.name]

    if args.action == "show":
        print(f"===== {args.name} =====")
        print(f"物理主軸: {card['物理主軸']}")
        print(f"反方: {card['反方']}")
        print("\n硬數字:")
        for hn in card["硬數字"]:
            print(f"  {hn.get('metric')}: {hn.get('value')} {hn.get('unit')}（來源: {hn.get('source')}）")
        print("\n梯隊表:")
        print(render_tier_table(card))
        print("\n一手來源:")
        for s in card["一手來源"]:
            print(f"  {s.get('title')}: {s.get('url')}")
        print()
        qa_check(card, args.name)
    elif args.action == "qa":
        qa_check(card, args.name)
    elif args.action == "tier-table":
        print(render_tier_table(card))
    elif args.action == "delete":
        delete_card(args.name)
        print(f"已刪除「{args.name}」")


if __name__ == "__main__":
    main()
