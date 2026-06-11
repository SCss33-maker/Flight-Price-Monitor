# 越南機票比價平台 ✈️

每天自動掃描 **台北(TPE) → 胡志明(SGN) / 峴港(DAD)** 未來 60 天、5 天 4 夜來回的最低票價，
做成價格月曆與比價表，放在 GitHub Pages 上分享給朋友看。

## 運作方式

```
GitHub Actions（每天台北時間 09:10 自動執行）
  └─ scripts/fetch_prices.py 呼叫 Amadeus Flight Offers Search API
       └─ 兩條航線每天輪流掃描（控制在免費額度內）
            └─ 結果累積到 docs/data.json（git 就是歷史紀錄）
                 └─ docs/index.html（GitHub Pages）讀取並呈現
```

特色：

- **價格月曆熱力圖**：一眼看出哪天出發最便宜（綠＝便宜、紅＝貴、★＝期間最低）。
- **含行李估算**：廉航票面價未含托運行李時，依 `config.json` 的行李加購價目表
  自動估算「含 20kg 行李總價」，避免「票面便宜、加行李變貴」的陷阱。
- **額度保險絲**：每月 API 呼叫次數記錄在 data.json，達 `monthlyCallBudget`（預設 1,900）即自動停止。

## 部署步驟

### 1. 申請 Amadeus API 金鑰（免費）

1. 到 <https://developers.amadeus.com/> 註冊帳號。
2. My Self-Service Workspace → Create New App，取得 **API Key / API Secret**。
3. 預設拿到的是 **test 環境**金鑰：可以跑通流程，但價格是樣本資料、非真實票價。
   驗證沒問題後，在後台把 App 升級到 **production**（免費額度不變，每月約 2,000 次查價），
   換用 production 金鑰才是真實報價。

### 2. 推上 GitHub

```bash
git remote add origin https://github.com/<你的帳號>/<repo名>.git
git push -u origin main
```

### 3. 設定 Secrets 與變數

GitHub repo → Settings → Secrets and variables → Actions：

| 類型 | 名稱 | 值 |
|---|---|---|
| Secret | `AMADEUS_CLIENT_ID` | Amadeus API Key |
| Secret | `AMADEUS_CLIENT_SECRET` | Amadeus API Secret |
| Variable | `AMADEUS_ENV` | `test` 或 `production`（不設定預設 test） |

### 4. 開啟 GitHub Pages

Settings → Pages → Source 選 **Deploy from a branch**，Branch 選 `main`、資料夾選 `/docs`。
幾分鐘後網址會是 `https://<你的帳號>.github.io/<repo名>/`，把這個網址丟給朋友即可。

### 5. 手動跑第一次

Actions → 「每日機票價格掃描」→ Run workflow，
`routes` 填 `ALL` 可一次掃兩條航線（其後排程會自動每天輪流掃）。

## 調整設定（config.json）

| 欄位 | 說明 |
|---|---|
| `destinations` | 監控的目的地機場代碼，可增減（航線越多越吃額度） |
| `stayNights` | 來回固定停留晚數（預設 4 = 5 天 4 夜） |
| `scanWindowDays` | 往後掃描幾天（預設 60） |
| `leadDays` | 略過最近幾天內出發的票（預設 3） |
| `baggageFeePerLegTWD` | 各航空「單程一段 20kg 托運」加購估算價（台幣）。**目前是粗估值，請依航空公司官網價目表更新**，查無代碼時用 `default` |
| `monthlyCallBudget` | 每月 API 呼叫上限保險絲 |

額度試算：`航線數 ÷ 2 × (scanWindowDays - leadDays + 1) × 30` ≈ 每月呼叫次數，
請保持在 Amadeus 免費額度（約 2,000 次/月）以內。

## 本機測試

```powershell
$env:AMADEUS_CLIENT_ID = "你的Key"
$env:AMADEUS_CLIENT_SECRET = "你的Secret"
$env:AMADEUS_ENV = "test"
$env:FORCE_ROUTES = "ALL"
python scripts/fetch_prices.py
# 然後在 docs/ 目錄起個靜態伺服器看頁面
python -m http.server 8000 -d docs
```

## 已知限制

- Amadeus test 環境的價格是樣本資料，僅供驗證流程；真實價格需切換 production 金鑰。
- 廉航（如越捷）已加入 Amadeus 系統，但個別促銷價（官網限定）仍可能查不到，下訂前建議去官網再確認一次。
- 行李加購價是估算值（靜態價目表），非即時查價。
- 票價為查詢當下快照，實際購買價格以訂票網站／航空公司為準。
