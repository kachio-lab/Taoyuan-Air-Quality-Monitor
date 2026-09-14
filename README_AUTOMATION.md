# 桃園測站 PM2.5 即時監控自動化

在既有教材（`src/` 與各 notebook）之上，補一條可以直接掛上 GitHub Actions 的自動化流程：
**每小時爬取即時資料 → 產生未來 1 / 3 小時的 PM2.5 預測 → 等實際值進來後自動評分 → 更新監控儀表板**。

儀表板是純靜態 HTML，靠 GitHub Pages 發佈，網址會長這樣：
`https://<你的帳號>.github.io/<repo 名稱>/`

---

## 這條管線在做什麼

| 檔案 | 角色 |
|---|---|
| `pipeline/settings.py` | 測站、API、路徑、警示門檻、AQI 分級 |
| `pipeline/build_history.py` | 把 2025 原始寬表 + 2026 合體版 CSV 整併成 `data/monitor/history.csv`（**只需跑一次**） |
| `pipeline/features.py` | 特徵工程，訓練與推論共用同一份程式碼 |
| `pipeline/train.py` | 比較 4 種演算法（RandomForest / HistGradientBoosting / LightGBM / Ridge），選 CV 最佳者存檔 |
| `pipeline/fetch.py` | 環境部 `aqx_p_432` + 中央氣象署 `O-A0001-001`，累加寫入 `observations.csv` |
| `pipeline/predict.py` | 用最新一小時觀測預測 +1h / +3h，寫入 `predictions.csv` |
| `pipeline/evaluate.py` | 預測對上實際值 → `scores.csv` 與 `docs/metrics.json` |
| `pipeline/backtest.py` | 用歷史保留期回測，提供上線第一天就看得到的成效基準 |
| `pipeline/dashboard.py` | 產生 `docs/index.html`（無外部相依，離線也打得開） |
| `pipeline/notify.py` | 選用：超過門檻時 LINE 推播 |
| `run_hourly.py` | 每小時排程的進入點（fetch → predict → evaluate → dashboard → notify） |

資料流全部走 git：Actions 每小時把新的觀測、預測、評分結果 commit 回 repo，
所以你隨時可以把 `data/monitor/scores.csv` 拉下來自己分析，也永遠有稽核軌跡。

---

## 一次性設定

### 1. 本機先跑一輪，把歷史資料和模型準備好

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 MOENV_API_KEY 與 CWA_API_KEY
python -m pipeline.build_history     # 產生 data/monitor/history.csv（約 5 MB，6.8 萬列）
python -m pipeline.train             # 訓練 +1h / +3h 模型（約 1~2 分鐘）
python -m pipeline.backtest          # 產生歷史回測成效
python run_hourly.py                 # 實跑一次完整流程，確認 API 金鑰沒問題
```

### 2. 推上 GitHub

```bash
git init
git add .
git commit -m "feat: 桃園測站 PM2.5 即時監控自動化"
git branch -M main
git remote add origin https://github.com/<你的帳號>/<repo 名稱>.git
git push -u origin main
```

`.env` 已在 `.gitignore` 內，金鑰不會被推上去。

### 3. 設定 repository secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`：

| Secret 名稱 | 必要性 | 說明 |
|---|---|---|
| `MOENV_API_KEY` | 必要 | 環境部開放資料平台金鑰 |
| `CWA_API_KEY` | 建議 | 中央氣象署授權碼；沒有的話氣溫/濕度會是缺值，模型仍能跑但會變準度較差 |
| `LINE_CHANNEL_ACCESS_TOKEN` | 選用 | 要 LINE 推播才需要 |
| `LINE_USER_ID` | 選用 | 同上 |

### 4. 開啟 GitHub Pages

`Settings` → `Pages` → Source 選 **Deploy from a branch**，Branch 選 `main` + `/docs`，按 Save。
一兩分鐘後儀表板就會上線。

### 5. 確認排程有跑

`Actions` 分頁 → `每小時空品監控` → 可以先按 `Run workflow` 手動觸發一次驗證。

---

## 怎麼看「即時監控的成效」

儀表板的成效表格有幾個欄位要一起看：

- **MAE / RMSE**：平均誤差（µg/m³）。桃園站夏季 PM2.5 本來就低，MAE 2~3 是正常量級。
- **偏差 (bias)**：正值代表系統性高估、負值代表低估。長期偏向同一邊就該重訓。
- **勝過基準**：和 persistence 基準線（假設「下一小時和現在一樣」）比較的 MAE 改善幅度。
  **這是最重要的一欄**——空品濃度本來就有高度自相關，一個「照抄現在值」的笨方法 MAE 就已經很低了，
  模型如果贏不過它，代表沒有真正的預測能力。正值才算及格。
- **等級命中率**：預測與實際落在同一個 PM2.5 空品等級（良好／普通／對敏感族群不健康…）的比例。
  對「要不要發警示」這個決策來說，這比 MAE 更貼近實際用途。

兩種成效並列顯示：

- **歷史回測成效**：上線前就算得出來，用最後 45 天完全沒參與訓練的資料驗證。
- **即時監控成效**：上線後累積的真實表現，會隨時間越來越有代表性（近 24 小時 / 7 天 / 30 天 / 全部）。

實測基準（2026-09-03 訓練，桃園站，保留期 2026-06-16 ~ 07-31）：

| 時距 | MAE | RMSE | R² | 勝過 persistence |
|---|---|---|---|---|
| +1 小時 | 2.25 | 2.88 | 0.58 | +7.9% |
| +3 小時 | 2.94 | 3.77 | 0.29 | +12.9% |

---

## 已知限制（重要）

1. **前 25 小時是暖機期**。最長的滯後特徵是 24 小時前的觀測，即時資料要累積滿 25 小時，
   特徵才完整。這段期間的預測會標記 `warmup=True`，儀表板會顯示暖機橫幅，
   而且**不列入成效統計**（避免把不完整的預測算進去，讓數字好看）。
2. **GitHub Actions 的排程不保證準時**，尖峰時段延遲 5~20 分鐘很常見，偶爾會整點跳過。
   管線設計成「補得回來」：漏掉一小時只是少一筆觀測，不會壞掉。
3. **repo 連續 60 天沒有 commit，GitHub 會自動停用排程**。本管線每小時都會 commit，
   所以正常運作下不會觸發，但如果你手動停用過再恢復，記得回 Actions 頁面確認。
4. **模型檔會進版控**（每個約 1.9 MB）。所以重訓設成每月一次而不是每週，
   避免 repo 體積長太快。要更頻繁的話改 `.github/workflows/monthly-retrain.yml` 的 cron。
5. **氣象是輔助欄位**。CWA API 掛掉時 `amb_temp` / `rh` 會是缺值，管線不會中斷，
   模型端有中位數補值，但那幾筆預測的準度會下降。
6. **即時 API 只給當下一小時**，所以歷史缺口補不回來。真的漏很多的話，
   等環境部把該月資料上架後重跑 `build_history` 補進 `history.csv`。

---

## 常用指令

```bash
python run_hourly.py                          # 完整跑一次（Actions 執行的就是這個）
python -m pipeline.fetch                      # 只抓資料
python -m pipeline.predict                    # 只做預測
python -m pipeline.evaluate                   # 只重算成效
python -m pipeline.dashboard                  # 只重產儀表板
python -m pipeline.train                      # 重新訓練
python -m pipeline.backtest --holdout-days 60 # 換保留期回測
```

換監控的測站（例如改成平鎮）：

```bash
TARGET_STATION=平鎮 python -m pipeline.train
TARGET_STATION=平鎮 python run_hourly.py
```

Actions 上要換站，就在 workflow 的 `env:` 區塊加一行 `TARGET_STATION: 平鎮`。
