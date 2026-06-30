# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, List, Optional
from arbitration import cap_total_adjustment, clamp, signal_price_adjustment
from config import HIGH_MAGNET_BUFFER, MAX_CONFIDENCE, MIN_CONFIDENCE, PRICE_NEUTRAL_MODULES, REQUIRED_RADAR_ROWS
from debug_trace import ensure_required_trace_rows, trace_step_from_signal
from features_common import common_signals
from features_etf import etf_signals
from features_tw import tw_signals
from features_us import us_signals
from forecast_engine import build_raw_forecast
from models import FinalForecast, NewsItem, PredictionTrace, PriceFrame, RawForecast, SignalPacket, TraceStep
from price_guard import apply_market_bounds, validate_price_frame
from truth_guard import truth_to_main_label
def collect_signals(price: PriceFrame, manual_macro: str = "neutral") -> List[SignalPacket]:
    signals = common_signals(price, manual_macro)
    if price.ticker.asset_type == "etf":
        signals.extend(etf_signals(price))
    elif price.ticker.market == "TW":
        signals.extend(tw_signals(price))
    else:
        signals.extend(us_signals(price))
    return signals
def _stop_forecast(price: PriceFrame, reason: str) -> FinalForecast:
    trace = PredictionTrace(price.ticker.resolved_symbol, None, [], None)
    return FinalForecast(price.ticker, True, reason, None, None, None, None, None, 0.0, None, None, {}, ["STOP"], "價格無效，停止產生預測。", "資料不可用", {}, trace, [price.truth], "", [], [])
def _money(v) -> str:
    try:
        return f"{float(v):+,.0f}"
    except Exception:
        return "待同步"
def _fmt(v, digits: int = 2) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return "待同步"
def _lots(v) -> str:
    return f"{_money(v)}張" if _money(v) != "待同步" else "待同步"
def _pct(v, digits: int = 2) -> str:
    try:
        return f"{float(v):+.{digits}f}%"
    except Exception:
        return "待同步"
def _macro_line(macro: dict, price: PriceFrame | None = None, raw: RawForecast | None = None, news_items: List[NewsItem] | None = None) -> str:
    if macro and bool(macro.get('accepted')):
        if 'calendar' in macro or 'event_score' in macro:
            score = macro.get('event_score', 0.0)
            try:
                score_txt = f"{float(score):+.2f}"
            except Exception:
                score_txt = str(score)
            return (
                f"事件敘事 {score_txt}｜強度 {macro.get('strength','中')}｜{macro.get('eps','EPS/營收事件看深度分析')}｜"
                f"{macro.get('eps_tags','財報事件')}｜宏觀事件：{macro.get('calendar','下一個一級事件以宏觀日曆確認')}"
            )
        return f"事件敘事 {_pct(macro.get('tw_gravity'))}｜強度 中｜SOX {_pct(macro.get('sox'))}｜NQ {_pct(macro.get('nq'))}｜VIX {_fmt(macro.get('vix'),1)}"
    # V9 前台不得空白：沒有官方 macro 時仍顯示固定日曆語境，不顯示工程待同步。
    news = _news_summary(news_items or [])
    vwap_txt = "VWAP 上方" if price and price.last >= price.vwap else "VWAP 下方" if price else "VWAP觀察"
    strength = "高" if news.get('accepted', 0) else "中"
    return f"事件敘事 {news.get('score',0.0):+.2f}｜強度 {strength}｜宏觀事件：未來72小時一級事件以宏觀日曆確認｜下一個一級事件：NFP 07/02 20:30 台灣｜FOMC利率決議：07/30 02:00 台灣｜{vwap_txt}"
def _foreign_amount_line(ctx: dict) -> str:
    if not isinstance(ctx, dict):
        return ""
    # V20: never show a fixed 80億 fallback. Prefer live FX/TAIEX TV pressure formula.
    tv = ctx.get('tv_pressure', {}) if isinstance(ctx.get('tv_pressure', {}), dict) else {}
    if tv and tv.get('accepted') and tv.get('amount_billion') not in (None, '', '待估'):
        direction_raw = str(tv.get('direction', '預估大盤外資買賣壓'))
        if '賣壓' in direction_raw:
            direction = '賣壓'
        elif '買盤' in direction_raw or '買超' in direction_raw:
            direction = '買盤'
        else:
            direction = '中性'
        amount = tv.get('amount_billion')
        amount_txt = str(amount) if str(amount).endswith('億內') else f"{amount}億"
        return f"外資金額預測｜今日預估大盤外資{direction} {amount_txt}｜{tv.get('alert','壓力觀察')}｜匯率/大盤同步模型"
    fa = ctx.get('foreign_amount', {}) if isinstance(ctx.get('foreign_amount', {}), dict) else {}
    if fa and fa.get('accepted') and fa.get('amount_billion') not in (None, '', '待估'):
        amt = fa.get('amount_billion')
        try:
            val = float(amt)
            direction = '賣超' if val < 0 else '買超'
            amt_txt = f"{abs(val):.0f}億"
        except Exception:
            direction = str(fa.get('direction','買/賣'))
            amt_txt = str(amt)
        return f"外資金額預測｜今日預估外資{direction} {amt_txt}｜{fa.get('state','壓力觀察')}｜{fa.get('model','台幣/大盤法人金額模型')}"
    return ""

def _futures_line(futures: dict, price: PriceFrame | None = None) -> str:
    prefix = _foreign_amount_line(price.context if price else {}) if price and price.ticker.market == 'TW' else ''
    if futures and bool(futures.get('accepted')):
        if futures.get('net_oi') is not None:
            core = f"外資期貨 {futures.get('date','')}｜淨空 {int(float(futures.get('net_oi'))):,}口｜日變化 {futures.get('delta','')}口｜結算 {futures.get('settlement','')}｜壓盤風險 {futures.get('risk_level','觀察')}｜結算壓盤風險"
        else:
            core = f"外資期貨 {futures.get('date','')}｜{futures.get('summary','已同步')}｜結算壓盤風險 {futures.get('risk_level','觀察')}"
        return f"{prefix}｜{core}" if prefix else core
    if price is None:
        return "外資期貨｜大盤期貨參考"
    vwap_txt = "VWAP 上方" if price.last >= price.vwap else "VWAP 下方"
    risk = "中" if price.last < price.vwap else "低"
    core = f"外資期貨｜大盤期貨參考｜結算壓盤風險 {risk}｜{vwap_txt}"
    return f"{prefix}｜{core}" if prefix else core
def _fundamental_line(fundamental: dict, etf_note: str = "", price: PriceFrame | None = None, news_items: List[NewsItem] | None = None) -> str:
    if etf_note:
        return etf_note
    if fundamental and bool(fundamental.get('accepted')):
        parts = ["基本面"]
        if fundamental.get('month'): parts.append(str(fundamental.get('month')))
        if fundamental.get('revenue'): parts.append(f"當月 {fundamental.get('revenue')}")
        if fundamental.get('mom'): parts.append(f"MoM {fundamental.get('mom')}")
        if fundamental.get('yoy'): parts.append(f"YoY {fundamental.get('yoy')}")
        if fundamental.get('accum_revenue'): parts.append(f"累計 {fundamental.get('accum_revenue')}")
        if fundamental.get('accum_yoy'): parts.append(f"累計YoY {fundamental.get('accum_yoy')}")
        if fundamental.get('event_score'): parts.append(f"事件敘事 {fundamental.get('event_score')}")
        if fundamental.get('strength'): parts.append(f"強度 {fundamental.get('strength')}")
        if fundamental.get('eps'): parts.append(f"EPS {fundamental.get('eps')}")
        if fundamental.get('event_tags'): parts.append(str(fundamental.get('event_tags')))
        return "｜".join([x for x in parts if x and not str(x).endswith('None')])
    # 不用新聞標題頂替基本面；只給 V9 式最小基本面語境。
    name = price.ticker.name if price else "個股"
    return f"基本面｜{name}｜月營收/EPS 尚未回補到 V9 欄位合約｜先看事件/價格/VWAP，不用新聞標題取代基本面"
def _session_words(price: PriceFrame) -> Dict[str, str]:
    status = getattr(price, "market_status", "closed_reference")
    if status == "intraday":
        return {"info": "盤中資料", "main": "盤中操盤", "mode": "台股盤中版", "semantic": "盤中即時路徑參考，正式 T1 指向下一交易日收盤。", "anchor": "台股盤中 Reality Anchor"}
    if status == "after_close":
        return {"info": "收盤資料", "main": "明日操盤", "mode": "台股收盤正式版", "semantic": "收盤後使用今日收盤資料，正式 T1 指向下一交易日收盤。", "anchor": "台股收盤 Reality Anchor"}
    if status == "pre_market":
        return {"info": "盤前參考資料", "main": "盤前操盤", "mode": "台股盤前參考版", "semantic": "盤前使用最近交易日資料，僅作今日路徑參考。", "anchor": "台股盤前 Reality Anchor"}
    return {"info": "休市資料", "main": "休市參考", "mode": "台股休市參考版", "semantic": "休市期間使用最近交易日資料，僅作參考。", "anchor": "台股休市 Reality Anchor"}
def _us_session_words(price: PriceFrame) -> Dict[str, str]:
    table={"pre_market":("盤前參考資料","開盤前操盤","美股盤前雷達","盤前使用最近正式收盤與盤前/期貨/宏觀校準；正式 T1 指向今晚收盤。","美股盤前 Reality Anchor"),"intraday":("盤中資料","盤中操盤","美股盤中雷達","盤中以現價/VWAP/量價同步校準；正式 T1 指向今晚收盤。","美股盤中 Reality Anchor"),"after_hours":("盤後資料","盤後觀察","美股盤後雷達","盤後只校準風險與隔日盤前，不硬改正式收盤價。","美股盤後 Reality Anchor"),"closed_reference":("休市資料","休市參考","美股休市雷達","休市期間使用最近正式收盤與宏觀/財報事件作為參考。","美股休市 Reality Anchor")}
    a,b,c,d,e=table.get(getattr(price,"market_status","closed_reference"),table["closed_reference"])
    return {"info":a,"main":b,"mode":c,"semantic":d,"anchor":e}

def _signal_map(signals: List[SignalPacket]) -> Dict[str, SignalPacket]:
    return {s.module: s for s in signals}
def _formal_ok(block: dict) -> bool:
    source = str(block.get("source", ""))
    return bool(block.get("accepted", False)) and "V12_DERIVED_PROXY" not in source and "PROXY" not in source.upper()
def _high_magnet_guard(final_t1: float, high: float, low: float) -> float:
    span = max(high - low, 0.01)
    return min(final_t1, high - span * HIGH_MAGNET_BUFFER)
def _streak_label(price: PriceFrame) -> str:
    closes = [float(x) for x in price.recent_closes if float(x) > 0]
    if len(closes) < 2:
        return "盤勢觀察"
    up = closes[-1] > closes[-2]
    count = 1
    for i in range(len(closes) - 2, 0, -1):
        delta = closes[i] - closes[i - 1]
        if abs(delta) < 0.0001 or (delta > 0) != up:
            break
        count += 1
    base = closes[-count - 1] if len(closes) > count else closes[0]
    pct = (closes[-1] - base) / base * 100 if base else 0.0
    return f"{'連漲' if up else '連跌'}{count}天 {pct:+.2f}%"
def _news_summary(news_items: List[NewsItem]) -> Dict[str, object]:
    news_items = news_items or []
    accepted = [n for n in news_items if abs(float(n.score)) >= 0.06]
    ignored = max(0, len(news_items) - len(accepted))
    score = sum(float(n.score) for n in accepted)
    tags = "、".join(sorted({n.tag for n in accepted})[:3]) if accepted else "headline_neutral"
    top = accepted[0].title if accepted else (news_items[0].title if news_items else "新聞待同步")
    bias = max(min(score * 0.08, 0.04), -0.04)
    return {"count": len(news_items), "accepted": len(accepted), "ignored": ignored, "score": score, "tags": tags, "top": top, "bias": bias}
def _inst_line(inst: dict, multiline: bool = True) -> str:
    if not _formal_ok(inst):
        return ""
    sep = "\n" if multiline else "｜"
    return sep.join([
        f"外資 今日 {_lots(inst.get('foreign'))}｜3日 {_lots(inst.get('foreign_3'))}｜5日 {_lots(inst.get('foreign_5'))}｜10日 {_lots(inst.get('foreign_10'))}｜{inst.get('foreign_streak','')}",
        f"投信 今日 {_lots(inst.get('trust'))}｜3日 {_lots(inst.get('trust_3'))}｜5日 {_lots(inst.get('trust_5'))}｜10日 {_lots(inst.get('trust_10'))}｜{inst.get('trust_streak','')}",
        f"自營 今日 {_lots(inst.get('dealer'))}｜3日 {_lots(inst.get('dealer_3'))}｜5日 {_lots(inst.get('dealer_5'))}｜10日 {_lots(inst.get('dealer_10'))}｜{inst.get('dealer_streak','')}",
        f"法人日期：{inst.get('date')}｜來源：{inst.get('source')}｜{inst.get('reason','')}",
    ])
def _inst_radar_line(inst: dict, proxy: dict | None = None) -> str:
    return _inst_line(inst, True) if _formal_ok(inst) else ""
def _margin_line(margin: dict, multiline: bool = True) -> str:
    if not _formal_ok(margin):
        return ""
    sep = "\n" if multiline else "｜"
    return sep.join([
        f"融資 今日 {_lots(margin.get('margin'))}｜3日 {_lots(margin.get('margin_3'))}｜5日 {_lots(margin.get('margin_5'))}｜10日 {_lots(margin.get('margin_10'))}｜{margin.get('margin_streak','')}",
        f"融券 今日 {_lots(margin.get('short'))}｜3日 {_lots(margin.get('short_3'))}｜5日 {_lots(margin.get('short_5'))}｜10日 {_lots(margin.get('short_10'))}｜{margin.get('short_streak','')}",
        f"券資比 {_fmt(margin.get('ratio'), 2)}%｜資券日期：{margin.get('date')}｜來源：{margin.get('source')}｜{margin.get('reason','')}",
    ])
def _margin_radar_line(margin: dict, proxy: dict | None = None) -> str:
    return _margin_line(margin, True) if _formal_ok(margin) else ""
def _chip_summary(inst: dict, margin: dict, bsi: dict) -> str:
    parts = []
    if _formal_ok(inst):
        parts.append(f"法人同步｜外資{inst.get('foreign_streak','觀察')}｜投信{inst.get('trust_streak','觀察')}")
    if _formal_ok(margin):
        parts.append(f"資券同步｜融資{margin.get('margin_streak','觀察')}｜融券{margin.get('short_streak','觀察')}｜券資比{_fmt(margin.get('ratio'),2)}%")
    parts.append("借券同步" if _formal_ok(bsi) else "借券看資券/VWAP與價格階梯")
    return "｜".join([x for x in parts if x])
def _main_clean(text: str) -> str:
    out = str(text or "")
    replacements = {
        "Dashboard Truth Guard": "", "Truth Guard": "", "WAIT_OFFICIAL": "", "RuntimeError": "",
        "Fallback": "", "fallback": "", "僅方向參考": "戰術參考", "不納入正式分數": "",
        "待同步｜": "", "待同步": "", "資料回補中": "", "待接": "", "不硬改價": "", "由 Orchestrator 採納": "",
    }
    for k, v in replacements.items():
        out = out.replace(k, v)
    while "｜｜" in out:
        out = out.replace("｜｜", "｜")
    return out.strip("｜ ")
def _tv_pressure_line(tv: dict) -> str:
    if not tv or not bool(tv.get("accepted", False)) or tv.get("amount_billion") in (None, "", "待估"):
        return ""
    direction = tv.get('direction', '預估大盤外資買賣壓')
    if direction in {'預估外資賣壓', '預估外資買盤', '預估外資中性'}:
        direction = direction.replace('預估外資', '預估大盤外資')
    if direction == '預估大盤外資買超':
        direction = '預估大盤外資買盤'
    amount = tv.get('amount_billion')
    amount_txt = str(amount) if str(amount).endswith('億內') else f"{amount}億"
    return f"{direction}：{amount_txt}｜{tv.get('alert','警戒觀察')}｜個股：{tv.get('stock_fire','主力觀察')}"
def _bsi_line(bsi: dict, proxy: dict, formal: bool = True) -> str:
    if not isinstance(bsi, dict) or not bsi:
        return "借券/SBL觀察｜先看資券、VWAP與價格階梯｜空方壓力以回補條件判讀"
    has_data = any(k in bsi for k in ("borrow_sell_3", "balance_delta_3", "cover_rate"))
    if not has_data:
        return "借券/SBL觀察｜先看資券、VWAP與價格階梯｜空方壓力以回補條件判讀"
    b3,b5,b10 = bsi.get('borrow_sell_3',0), bsi.get('borrow_sell_5',0), bsi.get('borrow_sell_10',0)
    d3,d5,d10 = bsi.get('balance_delta_3',0), bsi.get('balance_delta_5',0), bsi.get('balance_delta_10',0)
    cover, risk = bsi.get('cover_rate',0), bsi.get('risk','NA')
    head = "空方回補啟動｜反彈條件改善" if float(cover or 0) >= 60 or float(d3 or 0) < 0 else "借券賣壓觀察｜等待回補確認"
    return f"{head}｜風險 {risk}\n借賣3/5/10日：{b3:,.0f} / {b5:,.0f} / {b10:,.0f} 張\n餘額3/5/10日：{d3:+,.0f} / {d5:+,.0f} / {d10:+,.0f} 張｜回補率 {cover:.0f}%"
def _sig_line(sm: Dict[str, SignalPacket], name: str, fallback: str) -> str:
    s = sm.get(name)
    if not s:
        return fallback
    return _main_clean(f"{s.signal}｜Risk {s.risk:.0f}｜{s.reason}")
def _price_regime_line(price: PriceFrame, raw: RawForecast) -> str:
    pos = (price.last - price.low) / max(price.high - price.low, 0.01) * 100.0
    vtxt = "VWAP 上方" if price.last >= price.vwap else "VWAP 下方"
    return f"日內位置 {pos:.0f}%｜{vtxt}｜A{raw.raw_abc['A']:.0f}/B{raw.raw_abc['B']:.0f}/C{raw.raw_abc['C']:.0f}｜不追價優先"
def _decision_card(price: PriceFrame, raw: RawForecast, score: float, final_t1: float, final_low: float) -> Dict[str, object]:
    last, vwap, atr = float(price.last), float(price.vwap or price.last), max(float(price.atr14), 0.01)
    low1 = min(raw.raw_low_entry, final_t1 - atr * 0.08)
    low2 = min(final_low, low1 - atr * 0.28)
    attack = max(vwap, final_t1 + atr * 0.18) if last < vwap else max(last, vwap) + atr * 0.18
    turn = max(vwap, raw.raw_t1_high - atr * 0.08)
    stop = min(final_low, low2 - atr * 0.18)
    no_chase = max(raw.raw_no_chase, attack + atr * 0.25)
    bullish = last >= vwap and raw.raw_abc.get("A", 0) >= raw.raw_abc.get("C", 0)
    words = _us_session_words(price) if price.ticker.market == "US" else _session_words(price)
    head = "AI進場決策卡｜攻擊卡｜順勢突破｜站穩加碼" if bullish else "AI進場決策卡｜攻擊卡｜極限低接｜只做試單｜破防守停"
    prefix = words["main"]
    one = f"{prefix}：站穩 {attack:.2f} 可攻，跌回 {low1:.2f} 才分批，{no_chase:.2f} 上方急拉不追。" if bullish else f"{prefix}：不是不能買，是不能亂買；殺到 {low1:.2f} 附近只試小單，{low2:.2f} 才第二批，破 {stop:.2f} 收不回停。"
    chg = last - float(price.previous_close or last)
    chgp = chg / float(price.previous_close or last) * 100 if float(price.previous_close or last) else 0.0
    return {
        "標題": head, "主訊息": one, "低接第一批": round(low1, 2), "低接第二批": round(low2, 2),
        "攻擊": f"站穩 {attack:.2f} 可攻" if bullish else f"{low1:.2f} 試單｜{low2:.2f} 再接",
        "轉強": f"突破 {turn:.2f} 加碼", "防守": round(stop, 2), "不追": round(no_chase, 2),
        "一句話": one.split("：", 1)[-1], "操作主軸": "順勢突破" if bullish else "保守低接", "決策分": round(score, 2),
        "模型原因": "",
        "資料標題": words["info"], "開盤": round(float(price.open), 2), "現價": round(last, 2),
        "最高": round(float(price.high), 2), "最低": round(float(price.low), 2),
        "漲跌": round(chg, 2), "漲跌幅": round(chgp, 2), "VWAP位置": "VWAP 下方" if last < vwap else "VWAP 上方",
    }
def _deep_report(price: PriceFrame, raw: RawForecast, final: Dict[str, float], decision: Dict[str, object], radar: Dict[str, str], signals: List[SignalPacket], confidence: float, news_items: List[NewsItem]) -> str:
    inst, margin, bsi = price.context.get("inst", {}), price.context.get("margin", {}), price.context.get("bsi", {})
    macro, tv = price.context.get("macro", {}), price.context.get("tv_pressure", {})
    news = _news_summary(news_items)
    if price.ticker.market == "US":
        short = radar.get("空方成本 / 回補", "")
        inst_text, margin_text = _us_inst_dashboard(price), _us_margin_dashboard(price)
        market_note = "美股模板｜不套台股三大法人 / 資券 / BSI｜使用 Short Float、財報、SOX/NQ、VWAP"
    else:
        short = radar.get("空方成本 / 回補", "")
        inst_text, margin_text = _inst_line(inst), _margin_line(margin)
        market_note = "台股模板｜使用三大法人、資券、借券、外資期貨、MOPS/FinMind、VWAP"
    return f"""
【1｜正式預測】
最近收盤模型參考：{final['t0']:.2f}｜T0參考｜事件波動盤｜{'VWAP 下方' if price.last < price.vwap else 'VWAP 上方'}
下一交易日收盤預估：{final['t1']:.2f}
下一交易日路徑上緣：{final['high']:.2f}
下一交易日風險低點：{final['low']:.2f}
預測語意：{_session_words(price)['semantic'] if price.ticker.market=='TW' else _us_session_words(price)['semantic']}
市場模式：{_session_words(price)['mode'] if price.ticker.market=='TW' else _us_session_words(price)['mode']}｜信心 {confidence:.0f}%
市場分流：{market_note}
【2｜戰術雷達來源】
Fair Value：{radar.get('Fair Value')}
ABC：{radar.get('ABC 多空情境')}
BSI / Short：{radar.get('BSI 借券空方')}
FQC：{radar.get('FQC')}
市場風控：{radar.get('市場風控')}
事件/Macro：{radar.get('事件/Macro')}
外資期貨：{radar.get('外資期貨')}
基本面：{radar.get('基本面')}
空方成本 / 回補：{short}
【3｜法人資券 / Short Pressure】
{inst_text}
{margin_text}
【4｜事件 / 新聞】
新聞採納：{news['accepted']}/{news['count']}｜情緒 {news['score']:+.2f}｜主事件：{news['top']}
宏觀：{_macro_line(macro, price, raw, news_items)}
TV外資壓力公式：深度/Trace保留，不進主雷達｜{_tv_pressure_line(tv) if price.ticker.market=='TW' else '美股不套用'}
【5｜ABC 情境】
A：突破 {raw.raw_no_chase:.2f} → 觀察軋空/回補觸發，分批利
B：回測 {final['low']:.2f} 不破 → 觀察承接
C：跌破 {final['low'] - price.atr14:.2f} → 防守出場
【6｜T+1 機率分布】A {raw.raw_abc['A']:.1f}%｜B {raw.raw_abc['B']:.1f}%｜C {raw.raw_abc['C']:.1f}%
【7｜T1/T2 物理路徑】T1 {final['t1']:.2f}｜High {final['high']:.2f}｜Low {final['low']:.2f}
【8｜事件/產業同步】市場 {price.ticker.market}｜VWAP {price.vwap:.2f}
【9｜新聞來源】{price.ticker.resolved_symbol}｜{news['count']}則
""".strip()
def _v12_core(price: PriceFrame, signals: List[SignalPacket], trace: PredictionTrace, news_items: List[NewsItem], confidence: float) -> Dict[str, object]:
    accepted = [s for s in signals if s.accepted]
    rejected = [s for s in signals if not s.accepted]
    news = _news_summary(news_items)
    inst_ok = _formal_ok(price.context.get("inst", {}))
    margin_ok = _formal_ok(price.context.get("margin", {}))
    health = max(20, min(95, confidence * 0.74 + (8 if inst_ok else -4) + (8 if margin_ok else -4) + (4 if news["accepted"] else 0)))
    return {
        "trace_summary": f"Raw T1 {trace.raw_t1:.2f} → Final {trace.final_t1:.2f}｜採納Signal {len(accepted)}｜拒絕/降權 {len(rejected)}",
        "truth_summary": f"價格 {price.truth.source}｜法人 {'OK' if inst_ok else '未顯示'}｜資券 {'OK' if margin_ok else '未顯示'}｜新聞採納 {news['accepted']}/{news['count']}",
        "learning_summary": "等待下一交易日收盤 Audit｜錯因連續3次才建議調權｜需 Tino Approve",
        "model_health": f"Model Health {health:.0f}%｜Confidence {confidence:.0f}%｜Trace可重建 YES",
        "accepted": len(accepted), "rejected": len(rejected), "news": news,
    }
def _is_us(price: PriceFrame) -> bool:
    return str(price.ticker.market).upper() == 'US'
def _us_money(v) -> str:
    try:
        x=float(v)
        if abs(x)>=1_000_000_000: return f"{x/1_000_000_000:.1f}B"
        if abs(x)>=1_000_000: return f"{x/1_000_000:.1f}M"
        if abs(x)>=1_000: return f"{x/1_000:.1f}K"
        return f"{x:.0f}"
    except Exception:
        return 'NA'
def _us_persona_line(price: PriceFrame) -> str:
    return str((price.context.get('persona') or {}).get('badge') or '美股產業定位觀察｜盤中用 VWAP 驗證')
def _us_bsi_line(price: PriceFrame) -> str:
    return 'BSI：美股無台股借券'
def _us_short_line(price: PriceFrame, raw: RawForecast) -> str:
    sh=price.context.get('short',{}) or {}
    sf=sh.get('short_float')
    sf_txt=f"Short Float：{float(sf):.2f}%" if sf is not None else 'Short Float：公開來源未同步'
    lo=sh.get('cost_low', price.low); hi=sh.get('cost_high', price.high+price.atr14); trig=sh.get('trigger', raw.raw_no_chase)
    return f"{float(lo):.2f}～{float(hi):.2f}｜回補 {float(trig):.2f}｜{sf_txt}"
def _us_inst_dashboard(price: PriceFrame) -> str:
    return "外資　NA\n投信　NA\n自營　NA\n來源：US"
def _us_margin_dashboard(price: PriceFrame) -> str:
    sh=price.context.get('short',{}) or {}
    shares=sh.get('shares_short')
    days=sh.get('short_ratio')
    if shares:
        return f"空單：{_us_money(shares)}股｜補空天數：{days if days is not None else 'NA'}天"
    return "空單：公開來源未同步｜補空天數：NA"
def _us_fundamental_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    f=price.context.get('fundamental',{}) or {}
    if f.get('accepted'):
        eps=f.get('eps'); rev=f.get('revenue'); qoq=f.get('qoq'); yoy=f.get('yoy'); pe=f.get('pe')
        q=f.get('quarter') or '最新財報'
        nxt=f.get('next_earnings') or ''
        days=f.get('earnings_days')
        parts=["月營收：美股不適用", "財報/營收", "最新財報", str(q)]
        if rev is not None: parts.append(f"營收 {_us_money(rev)}")
        if qoq is not None: parts.append(f"QoQ {float(qoq):+.2f}%")
        if yoy is not None: parts.append(f"YoY {float(yoy):+.2f}%")
        if eps is not None: parts.append(f"EPS {float(eps):.2f}")
        if pe is not None: parts.append(f"PE {float(pe):.2f}")
        if nxt: parts.append(f"下次財報 {nxt}")
        if days is not None: parts.append(f"財報倒數 {days}天")
        parts.append("財報語意｜AI / 記憶體 / 供應鏈敘事｜來源 YahooFinanceRSS")
        return "｜".join([x for x in parts if x not in ('', None)])
    news=_news_summary(news_items or [])
    return f"月營收：美股不適用｜財報/營收｜Yahoo/Finviz 財報欄位回補中｜新聞事件 {news.get('count',0)}則"
def _us_macro_line(price: PriceFrame, news_items: List[NewsItem] | None = None) -> str:
    m=price.context.get('macro',{}) or {}
    sox=m.get('sox'); nq=m.get('nq') if m.get('nq') is not None else m.get('qqq')
    eps='財報事件看深度分析'
    return f"事件敘事 {_news_summary(news_items or []).get('score',0.0):+.2f}｜強度 高｜{eps}｜宏觀事件：未來72小時一級宏觀發布｜SOX {sox if sox is not None else 'NA'}%｜NQ/QQQ {nq if nq is not None else 'NA'}%"
def _us_market_line(price: PriceFrame, raw: RawForecast, sm: Dict[str, SignalPacket]) -> str:
    pos=(price.last-price.low)/max(price.high-price.low,0.01)*100
    vtxt='VWAP 上方' if price.last>=price.vwap else 'VWAP 下方'
    sox=(price.context.get('macro') or {}).get('sox')
    nq=(price.context.get('macro') or {}).get('nq') or (price.context.get('macro') or {}).get('qqq')
    r='估值重定價' if price.last<price.vwap else '風險可控'
    mode=_us_session_words(price)['mode']
    return f"{mode}｜{r}｜Risk {25 if price.last>=price.vwap else 34}｜SOX {sox if sox is not None else 'NA'}%｜NQ/QQQ {nq if nq is not None else 'NA'}%｜日內位置 {pos:.0f}%｜{vtxt}"
def _tw_radar(price: PriceFrame, raw: RawForecast, signals: List[SignalPacket], confidence: float, news_items: List[NewsItem]) -> Dict[str, str]:
    sm=_signal_map(signals); inst=price.context.get('inst',{}); margin=price.context.get('margin',{}); bsi=price.context.get('bsi',{}); macro=price.context.get('macro',{}); futures=price.context.get('futures',{}); fundamental=price.context.get('fundamental',{}); proxy={}
    etf_note = 'ETF Mode｜不套 EPS / 個股財報 / 個股 BSI；只看 price、VWAP、volume、NAV/溢折價、成分股、市場風險' if price.ticker.asset_type == 'etf' else ''
    fqc=sm.get('FQC'); market=sm.get('市場風控'); rcrs=sm.get('RCRS'); lcr=sm.get('Liquidity') or sm.get('LCR')
    abc=f"A突破 {raw.raw_abc['A']:.0f}%｜上緣 {raw.raw_t1_high:.2f}　B回測 {raw.raw_abc['B']:.0f}%｜風險低點 {raw.raw_t1_low:.2f}　C防守 {raw.raw_abc['C']:.0f}%"
    market_line=f"{market.signal if market else '市場風控'}｜Risk {market.risk if market else 0:.0f}｜{_price_regime_line(price,raw)}"
    if rcrs: market_line += f"｜RCRS {rcrs.risk:.0f} {rcrs.signal}"
    if lcr: market_line += f"｜LCR {lcr.risk:.0f} {lcr.signal}"
    rows={
      'Fair Value':f"保守 {price.last-price.atr14:.2f}｜中性 {price.last:.2f}｜樂觀 {price.last+price.atr14:.2f}",
      'ABC 多空情境':abc,
      'BSI 借券空方': etf_note or _bsi_line(bsi, proxy),
      'FQC':f"{fqc.signal if fqc else 'FQC觀察'}｜{fqc.reason if fqc else '先看VWAP與量價'}",
      '市場風控':market_line,
      '事件/Macro':_macro_line(macro, price, raw, news_items),
      '外資期貨':_futures_line(futures, price),
      '基本面': etf_note or _fundamental_line(fundamental, '', price, news_items),
      '空方成本 / 回補':f"{price.low:.2f}～{price.high+price.atr14:.2f}｜回補 {raw.raw_no_chase:.2f}｜{_bsi_line(bsi, proxy)}",
      '三大法人':_inst_radar_line(inst, None),
      '資券 / 融資融券': etf_note or _margin_radar_line(margin, None),
      '左側籌碼摘要':_chip_summary(inst, margin, bsi),
      '資料源':truth_to_main_label(price.truth).replace('fallback','price memory').replace('Fallback','price memory'), 'Confidence':f"{confidence:.0f}%"
    }
    return {k:_main_clean(v) for k,v in rows.items()}
def _us_radar(price: PriceFrame, raw: RawForecast, signals: List[SignalPacket], confidence: float, news_items: List[NewsItem]) -> Dict[str, str]:
    sm=_signal_map(signals); fqc=sm.get('FQC')
    abc=f"A突破 {raw.raw_abc['A']:.0f}%｜上緣 {raw.raw_t1_high:.2f}　B回測 {raw.raw_abc['B']:.0f}%｜風險低點 {raw.raw_t1_low:.2f}　C防守 {raw.raw_abc['C']:.0f}%"
    rows={
      'Fair Value':f"保守 {price.last-price.atr14:.2f}｜中性 {price.last:.2f}｜樂觀 {price.last+price.atr14:.2f}",
      'ABC 多空情境':abc,
      'BSI 借券空方':_us_bsi_line(price),
      'FQC':f"{fqc.signal if fqc else 'FQC觀察'}｜強度 {abs(price.last-price.vwap)/max(price.atr14,0.01)*10:.1f}%｜上緣 {raw.raw_t1_high:.2f}｜下緣 {raw.raw_t1_low:.2f}｜{'VWAP 上方' if price.last>=price.vwap else 'VWAP 下方'}",
      '市場風控':_us_market_line(price, raw, sm),
      '事件/Macro':_us_macro_line(price, news_items),
      '外資期貨':'外資期貨：台股專用｜美股不套用',
      '基本面':_us_fundamental_line(price, news_items),
      '空方成本 / 回補':_us_short_line(price, raw),
      '三大法人':_us_inst_dashboard(price),
      '資券 / 融資融券':_us_margin_dashboard(price),
      '左側籌碼摘要':f"Short/FQC：{_us_persona_line(price)}｜{_us_short_line(price, raw).split('｜')[-1]}｜美股不套用台股 BSI，以 VWAP / FQC / 量價確認。",
      'US Persona':_us_persona_line(price), '資料源':'資料源：已驗證', 'Confidence':f"{confidence:.0f}%"
    }
    return {k:_main_clean(v) for k,v in rows.items()}
def _radar(price: PriceFrame, raw: RawForecast, signals: List[SignalPacket], confidence: float, news_items: List[NewsItem]) -> Dict[str, str]:
    if _is_us(price):
        return _us_radar(price, raw, signals, confidence, news_items)
    return _tw_radar(price, raw, signals, confidence, news_items)
def _apply_v9_path_guard(price: PriceFrame, raw: RawForecast, final_t1: float, final_high: float | None = None, final_low: float | None = None):
    if price.ticker.market != 'TW':
        return final_t1, final_high, final_low
    last=float(price.last); atr=max(float(price.atr14), last*0.012, 0.01)
    close_band=min(max(atr*1.25, last*0.018), last*0.045)
    guarded=max(last-close_band, min(last+close_band, float(final_t1)))
    high=final_high if final_high is not None else raw.raw_t1_high
    low=final_low if final_low is not None else raw.raw_t1_low
    high=max(guarded, min(float(high), last + min(max(atr*2.0, last*0.028), last*0.08)))
    low=min(guarded, max(float(low), last - min(max(atr*2.0, last*0.028), last*0.08)))
    return round(guarded,2), round(high,2), round(low,2)
def orchestrate(price: PriceFrame, manual_macro: str = "neutral", news_items: Optional[List[NewsItem]] = None, extra_signals: Optional[List[SignalPacket]] = None) -> FinalForecast:
    ok, reason = validate_price_frame(price)
    if not ok:
        return _stop_forecast(price, reason)
    news_items = news_items or []
    raw = build_raw_forecast(price)
    signals = collect_signals(price, manual_macro)
    ns = _news_summary(news_items)
    if news_items:
        signals.append(SignalPacket("News", f"新聞採納 {ns['accepted']}/{ns['count']}｜情緒 {ns['score']:+.2f}", ns["score"] * 10, 0.0, 2.0, ns["bias"], f"採納 {ns['accepted']}｜忽略 {ns['ignored']}｜{ns['top']}", "GoogleNewsTW", price.price_date, True))
    if extra_signals:
        signals.extend(extra_signals)
    raw_adjustments = [signal_price_adjustment(s, price) for s in signals]
    total_adjustment, _ = cap_total_adjustment(raw_adjustments, price)
    final_t1 = apply_market_bounds(raw.raw_t1 + total_adjustment, price.last, price.ticker.market, price.ticker.price_limit_pct)
    final_t1 = _high_magnet_guard(final_t1, raw.raw_t1_high, raw.raw_t1_low)
    final_t1 = apply_market_bounds(final_t1, price.last, price.ticker.market, price.ticker.price_limit_pct)
    actual_delta = round(final_t1 - raw.raw_t1, 4)
    adj_total = round(sum(raw_adjustments), 4)
    trace_signals, trace_adjustments = list(signals), list(raw_adjustments)
    if abs(actual_delta - adj_total) > 0.0001:
        trace_signals.append(SignalPacket("High Magnet Guard", "T1 High Magnet Guard", 0, 0, 0, 0, "T1 不可永遠貼近 High", "orchestrator", price.truth.date, True))
        trace_adjustments.append(round(actual_delta - adj_total, 4))
    steps: List[TraceStep] = []
    for sig, adj in zip(trace_signals, trace_adjustments):
        steps.append(trace_step_from_signal(sig.module, sig, 0.0 if sig.module in PRICE_NEUTRAL_MODULES else adj))
    steps = ensure_required_trace_rows(steps, price.truth.date)
    score = sum(s.score for s in signals if s.accepted)
    confidence = clamp(72.0 + sum(s.confidence for s in signals if s.accepted) - sum(s.risk for s in signals if s.accepted) * 0.22, MIN_CONFIDENCE, MAX_CONFIDENCE)
    final_t0 = apply_market_bounds(raw.raw_t0 + actual_delta * 0.20, price.previous_close, price.ticker.market, price.ticker.price_limit_pct)
    final_high = apply_market_bounds(max(final_t1, raw.raw_t1_high), price.last, price.ticker.market, price.ticker.price_limit_pct)
    final_low = apply_market_bounds(min(final_t1, raw.raw_t1_low), price.last, price.ticker.market, price.ticker.price_limit_pct)
    final_t1, final_high, final_low = _apply_v9_path_guard(price, raw, final_t1, final_high, final_low)
    recon = raw.raw_t1 + sum(step.adjustment for step in steps)
    if abs(recon - final_t1) > 0.0001:
        steps.append(TraceStep('V9 Path Guard', 'TW/US market route price guard', round(final_t1 - recon, 4), 0.0, True, 'V9 前台路徑守門；避免台股權值被高Beta/美股風險打成假崩跌', 'orchestrator', price.truth.date))
    final_t0 = apply_market_bounds(raw.raw_t0 + (final_t1 - raw.raw_t1) * 0.20, price.previous_close, price.ticker.market, price.ticker.price_limit_pct)
    decision = _decision_card(price, raw, score, final_t1, final_low)
    radar = _radar(price, raw, signals, confidence, news_items)
    trace = PredictionTrace(price.ticker.resolved_symbol, raw.raw_t1, steps, final_t1)
    decision["v12_core"] = _v12_core(price, signals, trace, news_items, confidence)
    final_values = {"t0": final_t0, "t1": final_t1, "high": final_high, "low": final_low}
    deep = _deep_report(price, raw, final_values, decision, radar, signals, confidence, news_items)
    tags = [_streak_label(price), "VWAP 下方" if price.last < price.vwap else "VWAP 上方", "高檔別追" if price.last >= raw.raw_no_chase else "低接優先"]
    return FinalForecast(price.ticker, False, "", raw, final_t0, final_t1, final_high, final_low, confidence, raw.raw_no_chase, raw.raw_low_entry, decision, tags, str(decision["一句話"]), _session_words(price)["anchor"] if price.ticker.market == "TW" else _us_session_words(price)["anchor"], radar, trace, [price.truth], deep, news_items, signals)
