# -*- coding: utf-8 -*-
"""機票價格掃描（資料來源：SerpAPI Google Flights）。

對設定檔中每條航線，掃描未來 scanWindowDays 天內、每隔 scanStepDays 天取樣的
出發日，查 5 天 4 夜來回最低總價，累積到 docs/data.json 供前端網頁呈現。

SerpAPI 一次呼叫只回傳「一組去回日期」的多筆航班，免費額度約 250 次/月，
因此採「每兩週完整掃一次（排程在每月 1 號、15 號）」把用量壓在額度內，
並以 monthlyCallBudget 當保險絲。

環境變數：
  SERPAPI_API_KEY   SerpAPI 金鑰（必填）
  FORCE_ROUTES      逗號分隔目的地代碼或 ALL，限定本次掃描的航線（手動觸發用）
  MAX_QUERIES       本次最多再花幾次查詢（測試用，省額度）；0 或未設 = 不額外限制
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DATA_PATH = os.path.join(ROOT, "docs", "data.json")
ENDPOINT = "https://serpapi.com/search.json"


def load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def serpapi_search(params, max_attempts=3):
    """呼叫 SerpAPI，必要時重試（429 或空結果）。

    回傳 (結果 dict, 實際呼叫次數)。每次 HTTP 請求都會消耗額度，
    所以把次數一起回傳給呼叫端，好精準計入每月用量。
    """
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    result = {}
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                result = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempts < max_attempts:  # 速率限制，退避後重試
                time.sleep(3 * attempts)
                continue
            body = e.read().decode("utf-8", "ignore")[:200]
            return {"error": f"HTTP {e.code}: {body}"}, attempts
        # Google Flights 偶爾回空結果，再試一次往往就有
        if "returned any results" in (result.get("error") or "") and attempts < max_attempts:
            time.sleep(1.5)
            continue
        break
    return result, attempts


def display_airline(name, name_map):
    """把 SerpAPI 的英文航空名稱正規化成中文（關鍵字比對），查無則原樣回傳。"""
    low = (name or "").lower()
    for key, zh in name_map.items():
        if key.lower() in low:
            return zh
    return name or "—"


def baggage_fee(name, fee_map, legs):
    low = (name or "").lower()
    for key, fee in fee_map.items():
        if key == "default":
            continue
        if key.lower() in low:
            return fee * legs
    return fee_map.get("default", 800) * legs


def includes_checked_bag(offer):
    """從 extensions 文字盡力判斷是否已含托運行李；判斷不出來時回 None。"""
    for ext in offer.get("extensions", []) or []:
        e = ext.lower()
        if "free checked" in e or "checked bag included" in e:
            return True
        if "checked baggage for a fee" in e or "no checked" in e:
            return False
    return None


def summarize(offer, cfg, legs):
    """把一筆 SerpAPI 航班報價整理成我們要存的欄位。"""
    price = round(float(offer["price"]))
    segments = offer.get("flights", []) or []
    carrier_raw = segments[0].get("airline") if segments else None
    carrier = display_airline(carrier_raw, cfg.get("airlineNames", {}))
    stops = len(offer.get("layovers", []) or [])
    has_bag = includes_checked_bag(offer)
    fee = 0 if has_bag else baggage_fee(carrier_raw, cfg.get("baggageFeePerLegTWD", {}), legs)
    return {
        "price": price,
        "airline": carrier,
        "stops": stops,
        "bagIncluded": bool(has_bag),
        "estWithBag": price + fee,
    }


def main():
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        sys.exit("讀不到 config.json")
    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        sys.exit("請設定 SERPAPI_API_KEY 環境變數")

    data = load_json(DATA_PATH, {"meta": {}, "routes": {}})
    today = date.today()
    today_iso = today.isoformat()
    month_key = today.strftime("%Y-%m")
    usage = (data.get("meta") or {}).get("apiUsage", {})
    used = usage.get(month_key, 0)
    budget = cfg.get("monthlyCallBudget", 245)
    # MAX_QUERIES：本次最多再花幾次（測試用，省額度）；0 或未設 = 不額外限制
    max_q = int(os.environ.get("MAX_QUERIES", "0") or 0)
    cap = budget if max_q <= 0 else min(budget, used + max_q)

    force = [s.strip().upper() for s in os.environ.get("FORCE_ROUTES", "").split(",") if s.strip()]
    targets = cfg["destinations"]
    if force and force != ["ALL"]:
        targets = [d for d in cfg["destinations"] if d in force]
    print(f"本月已用 {used}/{budget} 次，本次上限 {cap}，掃描航線：{targets}", flush=True)

    legs = 2  # 來回兩段，行李費以兩段估算
    step = max(1, cfg.get("scanStepDays", 3))

    for dest in targets:
        if used >= cap:
            print(f"已達上限 {cap}，停止", flush=True)
            break
        route_key = f'{cfg["origin"]}-{dest}'
        route = data["routes"].setdefault(
            route_key, {"latest": {}, "datePriceHistory": {}, "minHistory": []}
        )
        scanned = {}
        for offset in range(cfg.get("leadDays", 3), cfg["scanWindowDays"] + 1, step):
            if used >= cap:
                print(f"已達上限 {cap}，提前停止", flush=True)
                break
            depart = today + timedelta(days=offset)
            ret = depart + timedelta(days=cfg["stayNights"])
            params = {
                "engine": "google_flights",
                "departure_id": cfg["origin"],
                "arrival_id": dest,
                "outbound_date": depart.isoformat(),
                "return_date": ret.isoformat(),
                "type": "1",  # 1 = 來回
                "adults": cfg.get("adults", 1),
                "currency": cfg.get("currency", "TWD"),
                "hl": "zh-tw",
                "gl": "tw",
                "deep_search": "true",  # 等結果完整載入，避免回空結果
                "api_key": api_key,
            }
            resp, calls = serpapi_search(params)
            used += calls  # 連同重試一起計入額度
            time.sleep(0.3)  # 友善節流
            if resp.get("error"):
                print(f"  {route_key} {depart}: SerpAPI 回報 {resp['error']}，跳過", flush=True)
                continue
            offers = (resp.get("best_flights") or []) + (resp.get("other_flights") or [])
            offers = [o for o in offers if o.get("price")]
            if not offers:
                print(f"  {route_key} {depart}: 無報價", flush=True)
                continue
            best = min((summarize(o, cfg, legs) for o in offers), key=lambda s: s["estWithBag"])
            best["returnDate"] = ret.isoformat()
            print(f"  [{used}/{budget}] {route_key} {depart}: {best['price']} {best['airline']}"
                  f"{'' if best['bagIncluded'] else '＋行李'}", flush=True)
            scanned[depart.isoformat()] = best
            history = route["datePriceHistory"].setdefault(depart.isoformat(), [])
            history.append([today_iso, best["price"]])
            del history[:-60]

        if scanned:
            route["latest"].update(scanned)
            min_date, min_rec = min(scanned.items(), key=lambda kv: kv[1]["estWithBag"])
            route["minHistory"].append([today_iso, min_rec["estWithBag"], min_date])
            del route["minHistory"][:-180]
            route["lastScan"] = today_iso
            print(f"{route_key}: 掃到 {len(scanned)} 個出發日，最低 {min_rec['estWithBag']} ({min_date})")
        else:
            print(f"{route_key}: 沒掃到任何報價")

        # 清掉已過期的出發日
        route["latest"] = {k: v for k, v in sorted(route["latest"].items()) if k >= today_iso}
        route["datePriceHistory"] = {
            k: v for k, v in route["datePriceHistory"].items() if k >= today_iso
        }

    usage[month_key] = used
    data["meta"] = {
        "apiUsage": dict(sorted(usage.items())[-3:]),
        "source": "SerpAPI · Google Flights",
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": cfg["origin"],
        "stayNights": cfg["stayNights"],
        "currency": cfg.get("currency", "TWD"),
        "scanStepDays": step,
    }
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"完成。本月已用 {used}/{budget} 次，資料寫入 {DATA_PATH}")


if __name__ == "__main__":
    main()
