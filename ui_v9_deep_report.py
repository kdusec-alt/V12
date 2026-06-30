# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd


def render_deep_report(st, forecast):
    title = f"開啟完整量子分析｜{forecast.ticker.resolved_symbol}"
    with st.expander(title, expanded=False):
        st.text(forecast.deep_report)
        news = forecast.news_items or []
        st.markdown(f"**新聞來源｜{forecast.ticker.resolved_symbol}｜{len(news)}則**")
        if news:
            df = pd.DataFrame([x.__dict__ for x in news])
            st.dataframe(df, hide_index=True, use_container_width=True, height=min(260, 76 + len(news) * 38))
            st.caption("來源：GoogleNewsTW｜V12 保留 V9 新聞、支撐共振與外部事件觀察，僅作輕量修正，等待後續 Audit 驗證。")
        else:
            st.caption("本次沒有抓到可用外部新聞，或新聞資料源暫時未回傳。")
        st.markdown(f"↑ 回到 {forecast.ticker.resolved_symbol} 卡片上方")
