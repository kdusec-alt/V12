TINO V12 v8.6.1｜MIS Debug Panel Renderer Hotfix Only

替換檔案：
- orchestrator.py
- ui_admin.py

目的：
- 將 v8.6 寫入 context['price_meta']['mis_debug'] 的 MIS 診斷資料帶到 Admin Console。
- Debug Mode 開啟後，在左側 Admin 顯示 MIS Price Debug。
- 不改價格採用邏輯，不改 V9 前台，不改法人/資券/基本面/Auto Learning/Google Sheet。

操作：
1. 先保留 v8.6 的 data_sources_tw.py / data_sources_tw_live_price.py。
2. 再替換本包的 orchestrator.py 與 ui_admin.py。
3. Deploy / Rerun。
4. 勾選 Debug Mode，重新分析 6770/5469，即可看到 MIS trace。
