# -*- coding: utf-8 -*-
"""每日機票價格掃描。

呼叫 Amadeus Flight Offers Search API，掃描設定檔中各航線
「未來 N 天每個出發日」的來回最低價，結果累積到 docs/data.json，
由 GitHub Pages 上的靜態網頁讀取呈現。

環境變數：
  AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET  Amadeus API 金鑰（必填）
  AMADEUS_ENV    test（預設，假價格驗證流程用）或 production（真實價格）
  FORCE_ROUTES   逗號分隔的目的地代碼或 ALL，強制掃描指定航線（手動觸發用）

額度控制：免費額度有限，預設兩條航線「每天輪流掃一條」，
並在 data.json 記錄每月已用次數，達到 monthlyCallBudget 即停止。
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

HOSTS = {
    "test": "https://test.api.amadeus.com",
    "production": "https://api.amadeus.com",
}


def load_json(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def get_token(host, client_id, client_secret):
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    req = urllib.request.Request(
        host + "/v1/security/oauth2/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def search_offers(host, token, params, retries=2):
    url = host + "/v2/shopping/flight-offers?" + urllib.parse.urlencode(params)
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp).get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:  # rate limit，退避後重試
                time.sleep(3 * (attempt + 1))
                continue
            if e.code in (400, 404):  # 該日期查無航班之類的，當作沒資料
                return []
            raise
    return []


def included_checked_bags(offer):
    """這筆報價是否已含托運行李（依件數或重量判斷）。"""
    try:
        fare = offer["travelerPricings"][0]["fareDetailsBySegment"][0]
        bags = fare.get("includedCheckedBags") or {}
        if bags.get("quantity"):
            return True
        if bags.get("weight") and float(bags["weight"]) >= 15:
            return True
    except (KeyError, IndexError, TypeError, ValueError):
        pass
    return False


def summarize(offer, cfg):
    """取出一筆報價的關鍵欄位，並估算含行李總價。"""
    price = float(offer["price"]["grandTotal"])
    itineraries = offer["itineraries"]
    first_seg = itineraries[0]["segments"][0]
    carriers = offer.get("validatingAirlineCodes") or [first_seg["carrierCode"]]
    carrier = carriers[0]
    stops = max(len(it["segments"]) - 1 for it in itineraries)
    has_bag = included_checked_bags(offer)
    fees = cfg.get("baggageFeePerLegTWD", {})
    bag_fee = 0 if has_bag else fees.get(carrier, fees.get("default", 800)) * len(itineraries)
    return {
        "price": round(price),
        "airline": carrier,
        "stops": stops,
        "bagIncluded": has_bag,
        "estWithBag": round(price + bag_fee),
    }


def routes_for_today(destinations, today, force):
    if force == ["ALL"]:
        return list(destinations)
    if force:
        return [d for d in destinations if d in force]
    if len(destinations) <= 1:
        return list(destinations)
    # 多條航線每天輪流掃，分攤 API 額度（兩條航線時各自每兩天更新一次）
    ordered = sorted(destinations)
    return [d for i, d in enumerate(ordered) if i % 2 == today.toordinal() % 2]


def main():
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        sys.exit("讀不到 config.json")
    client_id = os.environ.get("AMADEUS_CLIENT_ID")
    client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("請設定 AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET 環境變數")
    env = os.environ.get("AMADEUS_ENV", "test")
    host = HOSTS.get(env, HOSTS["test"])

    data = load_json(DATA_PATH, {"meta": {}, "routes": {}})
    today = date.today()
    today_iso = today.isoformat()
    month_key = today.strftime("%Y-%m")
    usage = (data.get("meta") or {}).get("apiUsage", {})
    used = usage.get(month_key, 0)
    budget = cfg.get("monthlyCallBudget", 1900)

    force = [s.strip().upper() for s in os.environ.get("FORCE_ROUTES", "").split(",") if s.strip()]
    targets = routes_for_today(cfg["destinations"], today, force)
    print(f"環境={env} 本月已用 {used}/{budget} 次，今日掃描：{targets}")

    token = get_token(host, client_id, client_secret)

    for dest in targets:
        if used >= budget:
            break
        route_key = f'{cfg["origin"]}-{dest}'
        route = data["routes"].setdefault(
            route_key, {"latest": {}, "datePriceHistory": {}, "minHistory": []}
        )
        scanned = {}
        for offset in range(cfg.get("leadDays", 3), cfg["scanWindowDays"] + 1):
            if used >= budget:
                print(f"已達本月額度上限 {budget}，提前停止")
                break
            depart = today + timedelta(days=offset)
            ret = depart + timedelta(days=cfg["stayNights"])
            params = {
                "originLocationCode": cfg["origin"],
                "destinationLocationCode": dest,
                "departureDate": depart.isoformat(),
                "returnDate": ret.isoformat(),
                "adults": cfg.get("adults", 1),
                "currencyCode": cfg.get("currency", "TWD"),
                "max": cfg.get("maxOffersPerQuery", 5),
            }
            used += 1
            try:
                offers = search_offers(host, token, params)
            except urllib.error.HTTPError as e:
                print(f"{route_key} {depart}: HTTP {e.code}，跳過")
                continue
            finally:
                time.sleep(0.2)  # 控制 TPS，避免 429
            if not offers:
                continue
            best = min((summarize(o, cfg) for o in offers), key=lambda s: s["estWithBag"])
            best["returnDate"] = ret.isoformat()
            scanned[depart.isoformat()] = best
            history = route["datePriceHistory"].setdefault(depart.isoformat(), [])
            history.append([today_iso, best["price"]])
            del history[:-120]

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
        "env": env,
        "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": cfg["origin"],
        "stayNights": cfg["stayNights"],
        "currency": cfg.get("currency", "TWD"),
        "airlineNames": cfg.get("airlineNames", {}),
        "baggageFeePerLegTWD": cfg.get("baggageFeePerLegTWD", {}),
    }
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"完成。本月已用 {used}/{budget} 次，資料寫入 {DATA_PATH}")


if __name__ == "__main__":
    main()
