# TINO V12 v8｜V9 Official Pipeline + One Page Shell

本版只整合目前已確認的 V9 前台 / 資料管線修正，不進入 AI 自動權重層。

## v8 重點
- Fair Value 從右側雷達移到左側標題旁，右側從 ABC 開始，讓一頁資訊更完整。
- 黑色底 / input safe zone / button z-index 加強，避免 Share / Deploy toolbar 造成白底或點擊卡住。
- 台股右側保留 V9 資料順序：ABC / BSI / FQC / 市場風控 / 事件 Macro / 外資期貨+外資金額預測 / 基本面 / 空方成本 / 三大法人 / 資券。
- 美股右側保留 V9 美股語境：BSI 美股無台股借券 / 美股盤前雷達 / Short Float / 空方成本回補 / 財報營收 / 三大法人 US/NA / 空單補空天數。
- 左側不再顯示「最近收盤模型參考 / 模型根因」。
- 保留 V12 拆檔與速度，不新增專案檔。

## 驗證
- python -m py_compile *.py
- TINO_OFFLINE_TEST=1 python smoke_tests.py


## V22 Google Sheet Memory
Secrets required:
ADMIN_PASSWORD, GSPREAD_SHEET_ID, [gcp_service_account].
Worksheets: prediction_log, audit_log, ticker_profiles, system_status.
Local memory remains the fast cache; Google Sheet is the durable memory across redeploy/reboot.
