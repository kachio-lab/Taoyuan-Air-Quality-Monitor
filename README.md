# 桃園市空品資料爬蟲與整理

依據《桃園市空品即時預測模型訓練與建置》專案計畫書實作，範圍僅涵蓋**資料蒐集與整理階段**，不含模型訓練與推論。

> 想直接看能自動跑在 GitHub Actions 上、每小時預測 + 監控成效的版本，見 [README_AUTOMATION.md](README_AUTOMATION.md)（對應 `pipeline/` 與 `run_hourly.py`）。本檔說明的是課堂教材版（`src/`），兩者可以並存，互不影響。

## 安裝

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 MOENV_API_KEY（於 https://data.moenv.gov.tw/ 註冊取得）
```

## 目錄結構

```
data/
  raw_historical/   # 原始寬表 CSV（桃園_2025.csv、平鎮_2025.csv ... 5 站）
    長表格式/             # 課堂整理過的長表（taoyuan_air_qulity.csv 等），刻意和寬表分開資料夾，
                          # 避免 src/historical_preprocess.py 的單層 glob 誤把長表當寬表處理
    其他北部測站_未使用/  # 下載時一併附帶的其他 20 個北部測站，本專案用不到，留著備查
  realtime_raw/     # 每次 API 拉取的原始 JSON 存檔（稽核用）
  processed/        # 整理後輸出：historical_long.csv、realtime_long.csv
  monitor/          # 自動化管線（pipeline/）的資料庫：history.csv、observations.csv、predictions.csv...
src/
  config.py               # 測站清單、API 規格、欄位對照表
  imputation.py            # 最鄰近值補值、時間特徵（day_of_year, hour_of_day）衍生
  historical_preprocess.py # 歷史寬表 -> 長表（melt/pivot）
  realtime_crawler.py      # 環境部即時 API 爬蟲
  weather_crawler.py       # 中央氣象署 API 爬蟲，補齊氣象欄位
notebooks/          # 課堂 EDA / 建模 / 爬蟲示範 notebook（打開後如果找不到某個 CSV，通常在 data/raw_historical/）
reference/          # 專案計畫書 V4.docx、警示門檻筆記等靜態參考資料
pipeline/           # 自動化管線（見 README_AUTOMATION.md）
```

## 注意：即時氣溫從哪裡來？

環境部即時 API `aqx_p_432` 為精簡版，**不含**氣溫(AMB_TEMP)、濕度(RH)、雨量(RAINFALL)欄位，
只有 AQI、SO2、CO、O3、PM10、PM2.5、NO2、NOx、NO、風速、風向、經緯度。

因此 `realtime_crawler.py` 會額外呼叫中央氣象署（CWA）開放資料平台的氣象站觀測 API
（`O-A0001-001`，`weather_crawler.py`），依經緯度為每個空品測站找出最近、且**有實際氣溫觀測值**
的氣象站（超過 15 公里視為不合理匹配、不予填值；沒有溫度感測器的路況監測站會被排除，避免配到啞站），
取得即時氣溫/濕度/雨量後合併進資料集。測站對照結果會快取在 `data/processed/station_weather_mapping.json`，
可自行開啟核對匹配是否合理（2026-07-20 實測結果：桃園/龍潭直接對到同名氣象站，
大園、觀音、平鎮則對到 3~7 公里內最近的有效測站）。

需額外於 `.env` 設定 `CWA_API_KEY`（申請網址：https://opendata.cwa.gov.tw/）。若未設定或 CWA 呼叫失敗，
爬蟲仍會正常寫入 AQI 資料，氣象欄位維持缺值，不會中斷整條流程。

## API 金鑰

金鑰存放於專案根目錄的 `.env`（已加入 `.gitignore`，不會被提交進版本控制）。
`.env.example` 僅為範本，不含真實金鑰。

## 一、歷史資料整理

1. 將環境部下載的 5 個測站原始 CSV（桃園、平鎮、大園、觀音、龍潭）放入 `data/raw_historical/`
2. 執行：

```bash
python src/historical_preprocess.py
```

輸出 `data/processed/historical_long.csv`：每列為一個測站在某一時間戳記的觀測，欄位包含各測項數值、`day_of_year`、`hour_of_day`。已完成最鄰近值補值，時間序列不中斷。

## 二、即時資料爬蟲

呼叫環境部開放 API `aqx_p_432`，篩選桃園地區 5 站，欄位對照為與歷史資料一致的結構，累加寫入 `data/processed/realtime_long.csv`。

```bash
python src/realtime_crawler.py          # 執行一次
python src/realtime_crawler.py --loop   # 常駐執行，每小時 15 分自動拉取（需 pip install APScheduler）
```

若要改用系統 cron 排程（不使用常駐模式），可設定：

```
15 * * * * cd /path/to/project && python src/realtime_crawler.py >> logs/crawler.log 2>&1
```

## 範圍說明

本階段產出：清洗過的長表資料集（`historical_long.csv` / `realtime_long.csv`），欄位已對齊、缺值已補、時間特徵已衍生。

**未包含**（依需求，留待後續建模階段）：Min-Max 正規化、訓練/測試切分、10 折交叉驗證、隨機森林模型訓練與推論、MinMaxScaler 儲存與載入、預警與 Dashboard 視覺化。
