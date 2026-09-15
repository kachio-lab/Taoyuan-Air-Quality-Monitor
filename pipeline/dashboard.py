"""產生靜態監控儀表板 docs/index.html（給 GitHub Pages 直接發佈）。

刻意不依賴任何 CDN 或 JS 圖表套件：圖是 Python 產生的 inline SVG，
所以離線打開、被公司防火牆擋掉外部資源時都還是看得到。

用法：python -m pipeline.dashboard
"""
from __future__ import annotations

import html
import json
import logging
import os
from datetime import datetime

import pandas as pd

from pipeline.settings import (
    AQI_BANDS,
    DASHBOARD_HTML,
    DOCS_DIR,
    METRICS_JSON,
    OBSERVATIONS_CSV,
    SCORES_CSV,
    TARGET_STATION,
    aqi_band,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BACKTEST_JSON = DOCS_DIR / "backtest.json"
CHART_HOURS = 72
CHART_W, CHART_H = 960, 300
PAD_L, PAD_R, PAD_T, PAD_B = 46, 16, 16, 34


# ----------------------------------------------------------------- SVG 折線圖
def line_chart(series: dict[str, tuple[list[datetime], list[float], str]], title: str) -> str:
    """series: {圖例名稱: (x 時間陣列, y 值陣列, 色碼)}"""
    points = [(x, y) for xs, ys, _ in series.values() for x, y in zip(xs, ys) if y == y]
    if len(points) < 2:
        return f'<div class="empty">{html.escape(title)}：資料還不夠，累積幾小時後就會出現</div>'

    xs_all = [p[0] for p in points]
    ys_all = [p[1] for p in points]
    x_min, x_max = min(xs_all), max(xs_all)
    y_min, y_max = min(ys_all), max(ys_all)
    span = (x_max - x_min).total_seconds() or 1
    y_pad = max((y_max - y_min) * 0.15, 1.0)
    y_lo, y_hi = y_min - y_pad, y_max + y_pad
    if y_min >= 0:      # 濃度不會是負的，軸從 0 起算比較好讀
        y_lo = max(0.0, y_lo)

    def px(t: datetime) -> float:
        return PAD_L + (t - x_min).total_seconds() / span * (CHART_W - PAD_L - PAD_R)

    def py(v: float) -> float:
        return CHART_H - PAD_B - (v - y_lo) / (y_hi - y_lo) * (CHART_H - PAD_T - PAD_B)

    parts = [f'<svg viewBox="0 0 {CHART_W} {CHART_H}" class="chart" role="img" aria-label="{html.escape(title)}">']

    # 水平格線 + y 軸標籤
    for i in range(5):
        v = y_lo + (y_hi - y_lo) * i / 4
        y = py(v)
        parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{CHART_W - PAD_R}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" class="axis" text-anchor="end">{v:.0f}</text>')

    # x 軸時間標籤
    for i in range(5):
        t = x_min + (x_max - x_min) * i / 4
        x = px(t)
        parts.append(f'<text x="{x:.1f}" y="{CHART_H - 10}" class="axis" text-anchor="middle">{t:%m/%d %H:%M}</text>')

    if y_lo < 0 < y_hi:  # 誤差圖：畫出 0 基準線
        zero = py(0.0)
        parts.append(
            f'<line x1="{PAD_L}" y1="{zero:.1f}" x2="{CHART_W - PAD_R}" y2="{zero:.1f}" '
            'class="grid" stroke-width="1.5" stroke-dasharray="4 3"/>'
        )

    for name, (xs, ys, color) in series.items():
        pts = [(px(x), py(y)) for x, y in zip(xs, ys) if y == y]
        if len(pts) < 2:
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
        dashed = ' stroke-dasharray="5 4"' if "預測" in name else ""
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.2"{dashed}/>')
        for x, y in pts[-1:]:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}"/>')

    parts.append("</svg>")

    legend = " ".join(
        f'<span class="legend-item"><i style="background:{c}"></i>{html.escape(n)}</span>'
        for n, (_, _, c) in series.items()
    )
    return f'<div class="chart-title">{html.escape(title)}</div>{"".join(parts)}<div class="legend">{legend}</div>'


# --------------------------------------------------------------------- 元件
def metric_card(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return (
        f'<div class="metric {tone}"><div class="metric-label">{html.escape(label)}</div>'
        f'<div class="metric-value">{value}</div>'
        f'<div class="metric-sub">{sub}</div></div>'
    )


def fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and value != value):
        return "—"
    return f"{value:.{digits}f}{suffix}"


def performance_table(horizons: dict, warmup_only: bool = False, target: str = "pm25") -> str:
    rows = []
    for h in sorted(horizons, key=int):
        h_data = horizons[h]
        # 支援新格式 {"pm25": {...}, "ox": {...}} 和舊格式 {"24h": {...}, ...}
        windows = h_data.get(target, h_data) if target in h_data else h_data
        for window, label in (("24h", "近 24 小時"), ("7d", "近 7 天"), ("30d", "近 30 天"), ("all", "全部")):
            m = windows.get(window)
            if not m:
                continue
            skill = m.get("skill_vs_baseline_pct")
            tone = "good" if skill is not None and skill > 0 else ("bad" if skill is not None else "")
            band_hit = fmt(m.get("band_hit_rate"), 1, "%") if target == "pm25" else "—"
            rows.append(
                f"<tr><td>+{h}h</td><td>{label}</td><td>{m['n']}</td>"
                f"<td>{fmt(m['mae'])}</td><td>{fmt(m['rmse'])}</td>"
                f"<td>{fmt(m['r2'])}</td><td>{fmt(m['bias'])}</td>"
                f"<td>{fmt(m['baseline_mae'])}</td>"
                f"<td class='{tone}'>{fmt(skill, 1, '%')}</td>"
                f"<td>{band_hit}</td></tr>"
            )
    if not rows:
        msg = (
            "暖機中：目前只有暖機期的預測，滯後特徵不完整，依規則不列入成效統計。"
            "累積滿 25 小時觀測後這張表就會開始有數字；在那之前請先看下方的歷史回測成效。"
            if warmup_only
            else "還沒有可評分的預測（要等預測的目標時間到了、實際觀測進來才算得出來）"
        )
        return f'<div class="empty">{msg}</div>'
    return (
        '<table><thead><tr><th>時距</th><th>統計區間</th><th>筆數</th><th>MAE</th><th>RMSE</th>'
        '<th>R²</th><th>偏差</th><th>基準線 MAE</th><th>勝過基準</th><th>等級命中率</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def backtest_section() -> str:
    if not BACKTEST_JSON.exists():
        return ""
    bt = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
    cards = []
    for h in sorted(bt.get("horizons", {}), key=int):
        m = bt["horizons"][h]
        cards.append(metric_card(
            f"+{h} 小時（回測）",
            fmt(m["mae"]),
            f"MAE µg/m³ · RMSE {fmt(m['rmse'])} · R² {fmt(m['r2'])}<br>"
            f"勝過 persistence {fmt(m['skill_vs_baseline_pct'], 1, '%')} · 誤差 ≤5 佔 {fmt(m['coverage_pct'], 1, '%')}",
            "good" if (m.get("skill_vs_baseline_pct") or 0) > 0 else "",
        ))
    period = next(iter(bt["horizons"].values()), {})
    return (
        '<section><h2>歷史回測成效</h2>'
        f'<p class="note">用 {bt["holdout_days"]} 天的保留期驗證（{period.get("test_start", "")} ~ '
        f'{period.get("test_end", "")}，共 {period.get("test_rows", 0)} 筆）。'
        '這段期間的資料完全沒有參與訓練，可以當作上線前的成效預期值。</p>'
        f'<div class="metrics">{"".join(cards)}</div></section>'
    )


def band_legend() -> str:
    items = "".join(
        f'<span class="legend-item"><i style="background:{c}"></i>{n}（{lo:g}–{hi:g}）</span>'
        for lo, hi, n, c in AQI_BANDS[:4]
    )
    return f'<div class="legend">{items}</div>'


# --------------------------------------------------------------------- 主體
def build_html(station: str) -> str:
    metrics = json.loads(METRICS_JSON.read_text(encoding="utf-8")) if METRICS_JSON.exists() else {}
    status = metrics.get("status", {})

    scores = (
        pd.read_csv(SCORES_CSV, encoding="utf-8-sig", parse_dates=["run_at", "base_time", "target_time"])
        if SCORES_CSV.exists() else pd.DataFrame()
    )
    obs = (
        pd.read_csv(OBSERVATIONS_CSV, encoding="utf-8-sig", parse_dates=["publishtime"])
        if OBSERVATIONS_CSV.exists() else pd.DataFrame()
    )
    if not obs.empty:
        obs = obs[obs["sitename"] == station].sort_values("publishtime")

    # --- 現況卡片 ---
    latest_pm = status.get("latest_pm25")
    cards = [metric_card(
        "目前實測 PM2.5",
        f'<span style="color:{status.get("latest_color", "#94A3B8")}">{fmt(latest_pm, 1)}</span>',
        f'µg/m³ · {html.escape(str(status.get("latest_band", "無資料")))}<br>觀測時間 {html.escape(str(status.get("latest_obs_time", "—")))}',
    )]
    for f in status.get("forecasts", [])[:3]:
        cards.append(metric_card(
            f'+{f["horizon_h"]} 小時預測',
            f'<span style="color:{f["color"]}">{f["y_pred"]:.1f}</span>',
            f'µg/m³ · {html.escape(f["band"])}<br>目標時間 {html.escape(f["target_time"])}',
        ))

    # --- 圖 1：實際 vs 預測 PM2.5 ---
    chart_html = '<div class="empty">還沒有足夠的觀測可以畫圖</div>'
    if not obs.empty:
        cutoff = obs["publishtime"].max() - pd.Timedelta(hours=CHART_HOURS)
        recent_obs = obs[obs["publishtime"] >= cutoff].dropna(subset=["pm2.5"])
        series = {
            "實際觀測": (
                recent_obs["publishtime"].tolist(),
                recent_obs["pm2.5"].astype(float).tolist(),
                "#2563EB",
            )
        }
        if not scores.empty:
            for horizon, color in ((1, "#F97316"), (3, "#7C3AED")):
                sel = scores[(scores["horizon_h"] == horizon) & (scores["target_time"] >= cutoff)]
                if len(sel) >= 2:
                    series[f"+{horizon}h 預測"] = (
                        sel["target_time"].tolist(),
                        sel["pm25_pred"].astype(float).tolist(),
                        color,
                    )
        chart_html = line_chart(series, f"{station}測站 PM2.5：近 {CHART_HOURS} 小時實際值 vs 預測值")

    # --- 圖 1b：實際 vs 預測 OX ---
    ox_chart_html = '<div class="empty">還沒有足夠的 OX 觀測可以畫圖</div>'
    if not obs.empty and "o3" in obs.columns and "no2" in obs.columns:
        cutoff = obs["publishtime"].max() - pd.Timedelta(hours=CHART_HOURS)
        obs_ox = obs[obs["publishtime"] >= cutoff].copy()
        obs_ox["ox_actual"] = pd.to_numeric(obs_ox["o3"], errors="coerce") + pd.to_numeric(obs_ox["no2"], errors="coerce")
        obs_ox = obs_ox.dropna(subset=["ox_actual"])
        ox_series: dict = {
            "實際 OX": (obs_ox["publishtime"].tolist(), obs_ox["ox_actual"].astype(float).tolist(), "#0891B2"),
        }
        if not scores.empty and "ox_pred" in scores.columns:
            for horizon, color in ((1, "#F97316"), (3, "#7C3AED")):
                sel = scores[(scores["horizon_h"] == horizon) & (scores["target_time"] >= cutoff)].dropna(subset=["ox_pred"])
                if len(sel) >= 2:
                    ox_series[f"+{horizon}h OX 預測"] = (
                        sel["target_time"].tolist(),
                        sel["ox_pred"].astype(float).tolist(),
                        color,
                    )
        if len(obs_ox) >= 2:
            ox_chart_html = line_chart(ox_series, f"{station}測站 OX：近 {CHART_HOURS} 小時實際值 vs 預測值")

    # --- 圖 2：預測誤差 PM2.5 ---
    err_chart = ""
    graded = scores.dropna(subset=["y_true_pm25"]) if not scores.empty and "y_true_pm25" in scores.columns else (
        scores.dropna(subset=["y_true"]) if not scores.empty and "y_true" in scores.columns else pd.DataFrame()
    )
    y_true_col = "y_true_pm25" if "y_true_pm25" in scores.columns else "y_true"
    if len(graded) >= 2:
        h1 = graded[graded["horizon_h"] == 1]
        if len(h1) >= 2:
            err_chart = line_chart(
                {
                    "+1h 模型誤差": (h1["target_time"].tolist(), h1["error"].astype(float).tolist(), "#F97316"),
                    "基準線誤差": (h1["target_time"].tolist(), h1["baseline_error"].astype(float).tolist(), "#94A3B8"),
                },
                "PM2.5 逐筆預測誤差（預測值 − 實際值，越接近 0 越好）",
            )

    # --- 最近 20 筆 PM2.5 對照表 ---
    recent_rows = ""
    if not graded.empty:
        for _, r in graded.sort_values("target_time", ascending=False).head(20).iterrows():
            better = abs(r["error"]) <= abs(r["baseline_error"])
            recent_rows += (
                f'<tr><td>{r["target_time"]:%m/%d %H:%M}</td><td>+{int(r["horizon_h"])}h</td>'
                f'<td>{r[y_true_col]:.1f}</td><td>{r["pm25_pred"]:.1f}</td>'
                f'<td class="{"good" if better else "bad"}">{r["error"]:+.1f}</td>'
                f'<td>{r["baseline_error"]:+.1f}</td>'
                f'<td>{html.escape(str(r.get("true_band", "—")))}</td></tr>'
            )
    recent_table = (
        '<table><thead><tr><th>目標時間</th><th>時距</th><th>實際 PM2.5</th><th>預測</th>'
        '<th>誤差</th><th>基準線誤差</th><th>實際等級</th></tr></thead>'
        f'<tbody>{recent_rows}</tbody></table>'
        if recent_rows else '<div class="empty">尚無已驗證的 PM2.5 預測</div>'
    )

    # --- 最近 20 筆 OX 對照表 ---
    ox_recent_rows = ""
    if not scores.empty and "ox_pred" in scores.columns and "y_true_ox" in scores.columns:
        ox_graded = scores.dropna(subset=["y_true_ox"])
        for _, r in ox_graded.sort_values("target_time", ascending=False).head(20).iterrows():
            if pd.isna(r.get("ox_pred")) or pd.isna(r.get("y_true_ox")):
                continue
            ox_err = r["ox_pred"] - r["y_true_ox"]
            ox_base_err = (r.get("ox_persistence") or float("nan")) - r["y_true_ox"]
            better = abs(ox_err) <= abs(ox_base_err) if not pd.isna(ox_base_err) else False
            ox_recent_rows += (
                f'<tr><td>{r["target_time"]:%m/%d %H:%M}</td><td>+{int(r["horizon_h"])}h</td>'
                f'<td>{r["y_true_ox"]:.1f}</td><td>{r["ox_pred"]:.1f}</td>'
                f'<td class="{"good" if better else "bad"}">{ox_err:+.1f}</td>'
                f'<td>{f"{ox_base_err:+.1f}" if not pd.isna(ox_base_err) else "—"}</td></tr>'
            )
    ox_recent_table = (
        '<table><thead><tr><th>目標時間</th><th>時距</th><th>實際 OX</th><th>預測</th>'
        '<th>誤差</th><th>基準線誤差</th></tr></thead>'
        f'<tbody>{ox_recent_rows}</tbody></table>'
        if ox_recent_rows else '<div class="empty">尚無已驗證的 OX 預測</div>'
    )

    # --- 系統狀態 ---
    model_meta = status.get("model", {}).get("models", {})
    model_rows = "".join(
        f'<tr><td>{html.escape(m.get("target","—"))}</td><td>+{m.get("horizon","?")}h</td><td>{html.escape(m["best_algorithm"])}</td>'
        f'<td>{m["train_rows"]}</td><td>{fmt(m["cv"]["cv_rmse"])}</td>'
        f'<td>{fmt(m["cv"]["cv_r2"])}</td><td>{html.escape(m["trained_at"])}</td></tr>'
        for h, m in sorted(model_meta.items(), key=lambda kv: (kv[1].get("target",""), kv[1].get("horizon", 0)))
    )
    warm = status.get("warmup", True)
    obs_hours = status.get("observation_hours", 0)
    warm_note = (
        f'<div class="banner warn">暖機中：目前累積 {obs_hours} 小時觀測，滿 25 小時後滯後特徵才完整，'
        "在那之前的預測僅供參考、也不列入成效統計。</div>"
        if warm else ""
    )

    generated = metrics.get("generated_at", datetime.now().isoformat(timespec="seconds"))

    # GitHub Pages 的網站根目錄是 docs/，連不到 repo 內的其他資料夾，
    # 所以逐筆評分檔改連到 GitHub 上的檔案（在 Actions 內才知道 repo 名稱）。
    repo = os.getenv("GITHUB_REPOSITORY")
    branch = os.getenv("GITHUB_REF_NAME", "main")
    scores_link = (
        f'<a href="https://github.com/{repo}/blob/{branch}/data/monitor/scores.csv">scores.csv</a>、'
        if repo else "data/monitor/scores.csv、"
    )

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(station)}測站 PM2.5 即時監控</title>
<style>
  :root {{
    --bg:#F8FAFC; --card:#FFFFFF; --ink:#0F172A; --muted:#64748B;
    --line:#E2E8F0; --good:#16A34A; --bad:#DC2626; --accent:#2563EB;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0B1220; --card:#151E2E; --ink:#E6EDF7; --muted:#94A3B8; --line:#25324A; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.6 "Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:28px 18px 64px; }}
  header h1 {{ margin:0 0 4px; font-size:26px; letter-spacing:.5px; }}
  header p {{ margin:0; color:var(--muted); font-size:13px; }}
  section {{ margin-top:32px; }}
  h2 {{ font-size:17px; margin:0 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }}
  .note {{ color:var(--muted); font-size:13px; margin:0 0 14px; }}
  .metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:14px; }}
  .metric {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
  .metric.good {{ border-left:4px solid var(--good); }}
  .metric.bad {{ border-left:4px solid var(--bad); }}
  .metric-label {{ color:var(--muted); font-size:12px; letter-spacing:.5px; }}
  .metric-value {{ font-size:32px; font-weight:600; line-height:1.2; margin:4px 0; }}
  .metric-sub {{ color:var(--muted); font-size:12px; }}
  table {{ width:100%; border-collapse:collapse; background:var(--card);
    border:1px solid var(--line); border-radius:12px; overflow:hidden; font-size:13px; }}
  th,td {{ padding:9px 12px; text-align:right; border-bottom:1px solid var(--line); }}
  th:first-child,td:first-child,th:nth-child(2),td:nth-child(2) {{ text-align:left; }}
  thead th {{ background:rgba(37,99,235,.08); font-weight:600; color:var(--muted); }}
  tbody tr:last-child td {{ border-bottom:none; }}
  td.good {{ color:var(--good); font-weight:600; }}
  td.bad {{ color:var(--bad); font-weight:600; }}
  .table-scroll {{ overflow-x:auto; }}
  .chart {{ width:100%; height:auto; background:var(--card);
    border:1px solid var(--line); border-radius:12px; padding:6px; }}
  .chart-title {{ font-size:13px; color:var(--muted); margin-bottom:6px; }}
  .grid {{ stroke:var(--line); stroke-width:1; }}
  .axis {{ fill:var(--muted); font-size:11px; }}
  .legend {{ margin:8px 0 0; font-size:12px; color:var(--muted); }}
  .legend-item {{ margin-right:16px; white-space:nowrap; }}
  .legend-item i {{ display:inline-block; width:10px; height:10px; border-radius:2px;
    margin-right:5px; vertical-align:middle; }}
  .empty {{ background:var(--card); border:1px dashed var(--line); border-radius:12px;
    padding:22px; color:var(--muted); text-align:center; font-size:13px; }}
  .banner {{ border-radius:10px; padding:12px 16px; font-size:13px; margin-top:16px; }}
  .banner.warn {{ background:rgba(249,115,22,.12); border:1px solid rgba(249,115,22,.35); }}
  footer {{ margin-top:40px; color:var(--muted); font-size:12px; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{html.escape(station)}測站 PM2.5 即時監控</h1>
    <p>資料來源：環境部 aqx_p_432 ・ 中央氣象署 O-A0001-001｜每小時由 GitHub Actions 自動更新｜
       最後更新 {html.escape(str(generated))}</p>
  </header>

  {warm_note}

  <section>
    <h2>目前狀況與預報</h2>
    <div class="metrics">{"".join(cards)}</div>
    {band_legend()}
  </section>

  <section>
    <h2>實際值 vs 預測值（PM2.5）</h2>
    {chart_html}
  </section>

  <section>
    <h2>實際值 vs 預測值（OX）</h2>
    {ox_chart_html}
  </section>

  <section>
    <h2>即時監控成效（PM2.5）</h2>
    <p class="note">「勝過基準」是和 persistence 基準線（假設濃度維持不變）比較的 MAE 改善幅度，
       正值代表模型真的有預測能力；「等級命中率」是預測與實際落在同一個 PM2.5 空品等級的比例。
       待驗證 {metrics.get("pending", 0)} 筆（目標時間還沒到）。</p>
    <div class="table-scroll">{performance_table(metrics.get("horizons", {}), metrics.get("warmup_only", False), "pm25")}</div>
    {err_chart}
  </section>

  <section>
    <h2>即時監控成效（OX）</h2>
    <p class="note">OX = O₃ + NO₂（總氧化劑），單位 ppb。</p>
    <div class="table-scroll">{performance_table(metrics.get("horizons", {}), metrics.get("warmup_only", False), "ox")}</div>
  </section>

  {backtest_section()}

  <section>
    <h2>最近 20 筆預測對照（PM2.5）</h2>
    <div class="table-scroll">{recent_table}</div>
  </section>

  <section>
    <h2>最近 20 筆預測對照（OX）</h2>
    <div class="table-scroll">{ox_recent_table}</div>
  </section>

  <section>
    <h2>模型與系統狀態</h2>
    <div class="table-scroll"><table>
      <thead><tr><th>目標</th><th>時距</th><th>演算法</th><th>訓練筆數</th><th>CV RMSE</th><th>CV R²</th><th>訓練時間</th></tr></thead>
      <tbody>{model_rows or '<tr><td colspan="7">尚未訓練</td></tr>'}</tbody>
    </table></div>
    <p class="note">累積觀測 {status.get("observation_rows", 0)} 列
       ・ 已評分 {int(len(graded))} 筆 ・ 暖機排除 {metrics.get("warmup_excluded", 0)} 筆。
       原始資料：{scores_link}<a href="metrics.json">metrics.json</a>。</p>
  </section>

  <footer>桃園市空品即時預測模型 ・ 本頁由 pipeline/dashboard.py 自動產生</footer>
</div>
</body>
</html>"""


def main(station: str = TARGET_STATION) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    DASHBOARD_HTML.write_text(build_html(station), encoding="utf-8")
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")  # 讓 GitHub Pages 原樣提供靜態檔
    logger.info("儀表板已產生 -> %s", DASHBOARD_HTML)


if __name__ == "__main__":
    main()
