# TINO V12 RC25.1 Render Crash Guard

## Purpose
Fix the fatal segmentation fault that occurs after analysis completes and the forecast render begins.

## Evidence from production logs
- price/news/orchestrator/prediction log all complete
- crash occurs after `render_forecast_start`
- memory remains below 200 MB
- reproduced on Python 3.12 and 3.14

## Changes
1. `ui_v9_deep_report.py`
   - removes `pandas.DataFrame` and `st.dataframe`
   - uses native Streamlit text/Markdown for news rows
   - avoids PyArrow serialization while the expander is collapsed
2. `app.py`
   - adds render checkpoints:
     - `render_battle_start/done`
     - `render_radar_start/done`
     - `render_deep_start/done`

## Not changed
- orchestrator.py
- data_sources_tw.py
- Price Guard
- Auto Audit
- Market Heat cache architecture
- V9 battle/radar layout

## Deployment
Upload and overwrite only:
- app.py
- ui_v9_deep_report.py

Then reboot the Streamlit app and test one TW stock and one US stock.
