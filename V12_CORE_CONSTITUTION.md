# TINO V12 Core Constitution

## 不可修改核心

1. V12 是唯一正式版本，取代 V9，但不得低於 V9。
2. V9 的成熟戰術面板、操盤語言、閱讀流程、深度分析與功能，是 V12 最低能力基線。
3. V12 不重做漂亮畫面；V12 負責資料驗證、演算法仲裁、錯因追蹤與自我校準。
4. 所有模組只能輸出 SignalPacket：signal、score、confidence、risk、bias、reason、source、date、accepted。
5. 模組不得直接修改 T0/T1/High/Low；Final 只能由 Orchestrator 產生。
6. Prediction Trace 必須能重建 Final T1。
7. Dashboard Truth Guard 必須說明資料來源、日期、fallback、是否採納與原因。
8. Auto Learning 只能產生建議，不得自動改模型；必須 Tino Admin Approve 後才生效。
9. ETF Mode 獨立，不套 EPS、個股 BSI、個股財報、個股法人劇本。
10. GPT 不得私自刪除、合併、濃縮、重排或重新詮釋正式功能。

一句話：V9 負責實戰呈現，V12 負責可追蹤、可防錯、可學習的核心。
