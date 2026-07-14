"""
MKT 그로스 분석 탭. (daily report 시트 · 열 문자 기준 정확 매핑)

시트 구조:
  0행 = 카테고리, 1행 = 지표. A열 = '11월' 같은 월 헤더 다음 '1','2'.. 일자.
  숫자는 ₩·쉼표·% 포함 → 파싱 시 제거. 시작 2024-11.
열 매핑(col 0-based):
  자사몰 O~AS(14~44) / 네이버 스스 AT~BD(45~55) / 쿠팡로켓 BE(56) / 그외 BG(58)
  전체광고 TTL BI~BK(60~62) / meta TTL BL~BV(63~73)
  google DE~DO(108~118) / GFA DP~DZ(119~129) / 네이버브검 EB~EL(131~141)
  CRM EM(142) / 인플루언서 EN~EP(143~145) / 네이버SA EQ~FA(146~156)
  쿠팡 FB~FS(157~174)
"""
import re
import datetime
from datetime import timedelta, date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DEFAULT_SHEET_URL = ("https://docs.google.com/spreadsheets/d/"
                     "1MHA572md96wxuos5x2EMuMiMmguWicea0nJTczjlLns/edit?gid=1873639498#gid=1873639498")

# 채널 매출 열
COL = {
    "dtc_sales": 14, "dtc_visit": 15, "dtc_new": 17, "dtc_return": 19,
    "dtc_conv": 21,
    "dtc_first_ord": 33, "dtc_first_amt": 34, "dtc_re_ord": 36, "dtc_re_amt": 37,
    "dtc_re_rate": 39, "dtc_refund_amt": 40, "dtc_refund_rate": 41, "dtc_cvr": 42, "dtc_aov": 43,
    "ss_sales": 45, "ss_visit": 46, "ss_pay": 48, "ss_refund_amt": 51,
    "ss_refund_rate": 52, "ss_cvr": 53, "ss_aov": 54,
    "coupang_ch_sales": 56, "etc_sales": 58,
}
# 광고 채널: (광고비col, 매출col, ROAS col) — 11지표 블록의 시작+0/+8/+10
AD = {
    "메타": 63, "구글": 108, "GFA": 119, "네이버 브랜드검색": 131, "네이버 SA": 146,
}
AD_META_SUB = {"메타 1계정": 74, "메타 2계정": 85, "메타 협력광고": 96}
CRM_COST = 142
INFLU = (143, 145)   # 인플루언서 광고비 범위 합
COUPANG_AD = (165, 171, 173)  # 쿠팡 광고비, 매출, ROAS
COUPANG_SALES = 157


def _to_csv_url(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        return None
    sid = m.group(1)
    gid = "0"
    g = re.search(r"[#&?]gid=([0-9]+)", url)
    if g:
        gid = g.group(1)
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv&gid={gid}"


def _num(v):
    if v is None:
        return 0.0
    s = str(v).replace("₩", "").replace(",", "").replace("%", "").strip()
    if s in ("", "nan", "#DIV/0!", "#VALUE!", "-"):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


@st.cache_data(ttl=300, show_spinner="시트를 불러오는 중...")
def _load(csv_url):
    raw = pd.read_csv(csv_url, header=None, dtype=str)
    rows = []
    year, month = 2024, None
    for r in range(2, raw.shape[0]):
        a = str(raw.iloc[r, 0]).strip()
        mm = re.match(r"(\d+)\s*월", a)
        if mm:
            newm = int(mm.group(1))
            if month is not None and newm < month:
                year += 1
            month = newm
            continue
        if a.isdigit() and month:
            try:
                d = datetime.date(year, month, int(a))
            except ValueError:
                continue
            rec = {"date": d}
            for k, c in COL.items():
                rec[k] = _num(raw.iloc[r, c]) if c < raw.shape[1] else 0.0
            # 광고 채널
            for name, base in AD.items():
                rec[f"ad_{name}_cost"] = _num(raw.iloc[r, base]) if base < raw.shape[1] else 0.0
                rec[f"ad_{name}_sales"] = _num(raw.iloc[r, base + 8]) if base + 8 < raw.shape[1] else 0.0
            rec["crm_cost"] = _num(raw.iloc[r, CRM_COST]) if CRM_COST < raw.shape[1] else 0.0
            rec["influ_cost"] = sum(_num(raw.iloc[r, c]) for c in range(INFLU[0], INFLU[1] + 1)
                                    if c < raw.shape[1])
            rec["influ_yt"] = _num(raw.iloc[r, 144]) if 144 < raw.shape[1] else 0.0
            rec["influ_pa"] = _num(raw.iloc[r, 145]) if 145 < raw.shape[1] else 0.0
            rec["coupang_sales"] = _num(raw.iloc[r, 56]) if 56 < raw.shape[1] else 0.0
            rec["coupang_ad_cost"] = _num(raw.iloc[r, COUPANG_AD[0]]) if COUPANG_AD[0] < raw.shape[1] else 0.0
            rec["coupang_ad_sales"] = _num(raw.iloc[r, COUPANG_AD[1]]) if COUPANG_AD[1] < raw.shape[1] else 0.0
            rows.append(rec)
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def _load_page_daily():
    """월별 파일의 page_daily 를 {날짜: {페이지: {q, a}}} 로."""
    import os
    import glob
    import json
    out = {}
    for path in sorted(glob.glob(os.path.join("data", "monthly", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                md = json.load(f)
        except Exception:
            continue
        for dt, pages in md.get("page_daily", {}).items():
            dst = out.setdefault(dt, {})
            for pno, v in pages.items():
                cell = dst.setdefault(pno, {"orders": 0, "q": 0, "a": 0, "cancel": 0})
                cell["orders"] += v.get("orders", 0)
                cell["q"] += v.get("q", 0)
                cell["a"] += v.get("a", 0)
                cell["cancel"] += v.get("cancel", 0)
    return out


@st.cache_data(ttl=300)
def _load_odit_daily():
    """월별 파일의 odit_daily 를 {날짜: {인치그룹·색상: 수량}} 로."""
    import os
    import glob
    import json
    out = {}
    for path in sorted(glob.glob(os.path.join("data", "monthly", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                md = json.load(f)
        except Exception:
            continue
        for key, daymap in md.get("odit_daily", {}).items():
            for dt, v in daymap.items():
                q = v["q"] if isinstance(v, dict) else v
                out.setdefault(dt, {})[key] = out.setdefault(dt, {}).get(key, 0) + q
    return out


def _won_short(n):
    try:
        n = float(n)
    except Exception:
        return "-"
    if abs(n) >= 1e8:
        return f"{n/1e8:.1f}억"
    if abs(n) >= 1e4:
        return f"{n/1e4:.0f}만"
    return f"{n:,.0f}"


def _won_aov(n):
    """객단가용: 만원 단위 소수 첫째자리(=천원 단위)까지. 예: 439,000 → 43.9만"""
    try:
        n = float(n)
    except Exception:
        return "-"
    if abs(n) >= 1e4:
        return f"{n/1e4:.1f}만"
    return f"{n:,.0f}"


def _delta_str(cur, prev):
    if prev == 0:
        return ("+∞" if cur > 0 else "0%")
    p = (cur - prev) / prev * 100
    return f"{'+' if p >= 0 else ''}{p:.0f}%"


def _pie(values, names, colors=None):
    fig = go.Figure(go.Pie(values=values, labels=names, hole=0.5,
                           marker=dict(colors=colors) if colors else None,
                           sort=False))
    fig.update_traces(textinfo="percent", textfont_size=11)
    fig.update_layout(height=260, margin=dict(t=30, b=10, l=10, r=10),
                      showlegend=True, legend=dict(orientation="h", y=-0.1, font=dict(size=10)))
    return fig


def _render_growth(df):
    st.header("🚀 그로스")

    # ---- 날짜 필터 (현재 기간 vs 직전 동일 기간) ----
    valid = df[df["dtc_sales"] > 0]
    if valid.empty:
        st.info("실적 데이터가 없어요.")
        return
    dmin = df["date"].min()
    dmax = valid["date"].max()
    fc1, fc2 = st.columns([2, 1])
    with fc1:
        rng = st.date_input("기간", (dmax - timedelta(days=6), dmax),
                            min_value=dmin, max_value=dmax, key="growth_range")
    if isinstance(rng, tuple) and len(rng) == 2:
        c_from, c_to = rng
    else:
        c_from = c_to = rng if not isinstance(rng, tuple) else dmax
    span = (c_to - c_from).days + 1
    p_to = c_from - timedelta(days=1)
    p_from = p_to - timedelta(days=span - 1)
    with fc2:
        st.caption(f"현재: {c_from} ~ {c_to} ({span}일)\n\n비교: {p_from} ~ {p_to}")

    cur = df[(df["date"] >= c_from) & (df["date"] <= c_to)]
    prev = df[(df["date"] >= p_from) & (df["date"] <= p_to)]

    def s(frame, col):
        return frame[col].sum() if col in frame else 0

    # 공통: 파이 2개 + 비교 테이블 렌더
    def block(title, ch_map, colors):
        st.markdown(f"#### {title}")
        cur_tot = sum(s(cur, c) for c in ch_map.values()) or 1
        prev_tot = sum(s(prev, c) for c in ch_map.values()) or 1
        names = list(ch_map.keys())
        cur_vals = [s(cur, ch_map[n]) for n in names]
        prev_vals = [s(prev, ch_map[n]) for n in names]
        col_colors = [colors[n] for n in names]
        p1, p2, p3 = st.columns([1, 1, 1.4])
        with p1:
            st.caption("현재 기간")
            shown = [(n, v, c) for n, v, c in zip(names, cur_vals, col_colors) if v > 0]
            if shown:
                st.plotly_chart(_pie([x[1] for x in shown], [x[0] for x in shown],
                                     [x[2] for x in shown]),
                                use_container_width=True, config={"displayModeBar": False})
        with p2:
            st.caption("비교 기간")
            shownp = [(n, v, c) for n, v, c in zip(names, prev_vals, col_colors) if v > 0]
            if shownp:
                st.plotly_chart(_pie([x[1] for x in shownp], [x[0] for x in shownp],
                                     [x[2] for x in shownp]),
                                use_container_width=True, config={"displayModeBar": False})
        with p3:
            st.caption("비교 표")
            rows = []
            for n in names:
                cv, pv = s(cur, ch_map[n]), s(prev, ch_map[n])
                if cv == 0 and pv == 0:
                    continue
                rows.append({"채널": n, "현재": cv, "비중": cv / cur_tot * 100,
                             "이전": pv, "증감": _delta_str(cv, pv)})
            # 합계 행
            ct = sum(cur_vals); pt = sum(prev_vals)
            rows.append({"채널": "합계", "현재": ct, "비중": 100.0, "이전": pt,
                         "증감": _delta_str(ct, pt)})
            dfr = pd.DataFrame(rows)

            def ud(v):
                if isinstance(v, str) and v.startswith("+"): return "color:#1D9E75;font-weight:600;"
                if isinstance(v, str) and v.startswith("-"): return "color:#E5484D;font-weight:600;"
                return ""
            st.dataframe(dfr.style.map(ud, subset=["증감"]).format(
                {"현재": "₩{:,.0f}", "비중": "{:.0f}%", "이전": "₩{:,.0f}"}),
                hide_index=True, use_container_width=True)

    # ---- 1. 매출 ----
    sale_ch = {"자사몰": "dtc_sales", "네이버": "ss_sales", "쿠팡": "coupang_sales", "기타": "etc_sales"}
    sale_hex = {"자사몰": "#378ADD", "네이버": "#3FA972", "쿠팡": "#E0A800", "기타": "#B8BCC2"}
    block("1. 매출", sale_ch, sale_hex)

    # ---- 2. 광고비 ----
    ad_ch = {"메타": "ad_메타_cost", "구글": "ad_구글_cost", "GFA": "ad_GFA_cost",
             "브랜드검색": "ad_네이버 브랜드검색_cost", "네이버SA": "ad_네이버 SA_cost",
             "CRM": "crm_cost", "인플루언서 YT": "influ_yt", "인플루언서 PA": "influ_pa"}
    ad_hex = {"메타": "#4267B2", "구글": "#EA4335", "GFA": "#03C75A", "브랜드검색": "#1EC800",
              "네이버SA": "#00B843", "CRM": "#E0A800", "인플루언서 YT": "#B060D0",
              "인플루언서 PA": "#8E44AD"}
    block("2. 광고비", ad_ch, ad_hex)

    # ---- 3. ROAS (카드) ----
    st.markdown("#### 3. ROAS")
    def roas(sales, cost):
        return sales / cost if cost else 0
    cur_sales = sum(s(cur, c) for c in sale_ch.values())
    prev_sales = sum(s(prev, c) for c in sale_ch.values())
    cur_ad = sum(s(cur, c) for c in ad_ch.values())
    prev_ad = sum(s(prev, c) for c in ad_ch.values())
    dtc_ad_c = s(cur, "ad_메타_cost") + s(cur, "ad_구글_cost") + s(cur, "ad_네이버 브랜드검색_cost")
    dtc_ad_p = s(prev, "ad_메타_cost") + s(prev, "ad_구글_cost") + s(prev, "ad_네이버 브랜드검색_cost")
    nv_ad_c = s(cur, "ad_GFA_cost") + s(cur, "ad_네이버 SA_cost")
    nv_ad_p = s(prev, "ad_GFA_cost") + s(prev, "ad_네이버 SA_cost")
    cur_all_roas = roas(cur_sales, cur_ad); prev_all_roas = roas(prev_sales, prev_ad)
    dtc_c = roas(s(cur, "dtc_sales"), dtc_ad_c); dtc_p = roas(s(prev, "dtc_sales"), dtc_ad_p)
    nv_c = roas(s(cur, "ss_sales"), nv_ad_c); nv_p = roas(s(prev, "ss_sales"), nv_ad_p)
    r = st.columns(3)
    r[0].metric("전체 ROAS", f"{cur_all_roas*100:.0f}%", _delta_str(cur_all_roas, prev_all_roas))
    r[1].metric("자사몰 ROAS", f"{dtc_c*100:.0f}%", _delta_str(dtc_c, dtc_p),
                help="자사몰 매출 / (메타+구글+브랜드검색)")
    r[2].metric("네이버 ROAS", f"{nv_c*100:.0f}%", _delta_str(nv_c, nv_p),
                help="네이버 매출 / (GFA+네이버SA)")

    # ---- 인플루언서 · CRM 집행 타임라인 (현재+비교 기간, x축=날짜) ----
    st.markdown("#### 인플루언서 · CRM 집행 타임라인")
    tl_from, tl_to = p_from, c_to   # 비교 시작 ~ 현재 끝
    tl = df[(df["date"] >= tl_from) & (df["date"] <= tl_to)].copy().sort_values("date")
    xs = [d.strftime("%m/%d") for d in tl["date"]]
    yt = [tl.iloc[i].get("influ_yt", 0) or 0 for i in range(len(tl))]
    pa = [tl.iloc[i].get("influ_pa", 0) or 0 for i in range(len(tl))]
    crm = [tl.iloc[i].get("crm_cost", 0) or 0 for i in range(len(tl))]
    if not xs:
        st.caption("선택 기간에 집행 데이터가 없어요.")
    else:
        figc = go.Figure()
        figc.add_trace(go.Bar(x=xs, y=yt, name="인플루언서 YT", marker_color="#B060D0"))
        figc.add_trace(go.Bar(x=xs, y=pa, name="인플루언서 PA", marker_color="#8E44AD"))
        figc.add_trace(go.Scatter(x=xs, y=crm, name="CRM", yaxis="y2",
                                  mode="lines+markers", line=dict(color="#E0A800", width=2),
                                  marker=dict(size=6)))
        figc.update_layout(barmode="stack", height=300, plot_bgcolor="white",
                           margin=dict(t=10, b=10, l=10, r=10),
                           yaxis=dict(title="인플루언서 광고비", gridcolor="#EEF1F5", tickformat=","),
                           yaxis2=dict(title="CRM 광고비", overlaying="y", side="right",
                                       showgrid=False, tickformat=","),
                           xaxis=dict(title=None),
                           legend=dict(orientation="h", y=1.15))
        st.plotly_chart(figc, use_container_width=True, config={"displayModeBar": False})
        # 집행 합계 요약
        st.caption(f"기간 집행 합계 · 인플루언서 YT {_won_short(sum(yt))} · "
                   f"인플루언서 PA {_won_short(sum(pa))} · CRM {_won_short(sum(crm))}")
    st.divider()


def _render_daily_checkin(df):
    st.header("📋 자사몰 데일리 체크인")
    valid = df[df["dtc_sales"] > 0]
    if valid.empty:
        st.info("실적 데이터가 없어요.")
        return
    dmin, dmax = df["date"].min(), valid["date"].max()
    sel = st.date_input("확인할 일자", dmax, min_value=dmin, max_value=dmax, key="checkin_date")
    if isinstance(sel, tuple):
        sel = sel[0]

    row_by_date = {r["date"]: r for _, r in df.iterrows()}

    def val(d, col):
        r = row_by_date.get(d)
        if r is None:
            return None
        # 전환율은 시트 값 대신 전환수/유입수로 직접 계산
        if col == "dtc_cvr":
            conv = r.get("dtc_conv") or 0
            visit = r.get("dtc_visit") or 0
            return (conv / visit) if visit else 0
        v = r.get(col)
        return v if (v is not None and v != 0) else (0 if r is not None else None)

    import datetime as _dt
    d_prev = sel - _dt.timedelta(days=1)
    d_wow = sel - _dt.timedelta(days=7)
    d_mom = sel - _dt.timedelta(days=28)          # 4주 전(요일 정렬 유지)
    try:
        d_yoy = sel.replace(year=sel.year - 1)
    except ValueError:
        d_yoy = sel - _dt.timedelta(days=365)

    def cmp_line(cur, comp):
        if cur is None:
            return "-"
        if comp is None:
            return "비교없음"
        if comp == 0:
            return "+∞" if cur > 0 else "0%"
        p = (cur - comp) / comp * 100
        return f"{'+' if p >= 0 else ''}{p:.0f}%"

    # ---- 지표 5개 ----
    metrics = [
        ("매출", "dtc_sales", "won"), ("유입수", "dtc_visit", "num"),
        ("전환", "dtc_conv", "num"), ("전환율", "dtc_cvr", "pct"),
        ("객단가", "dtc_aov", "aov"),
    ]
    st.caption(f"선택일: {sel}　·　전일 {d_prev}　·　WoW {d_wow}　·　MoM {d_mom}　·　YoY {d_yoy}")
    for label, col, fmt in metrics:
        cur = val(sel, col)

        def show(v):
            if v is None:
                return "-"
            if fmt == "won":
                return _won_short(v)
            if fmt == "aov":
                return _won_aov(v)
            if fmt == "pct":
                return f"{v*100:.2f}%"
            return f"{v:,.0f}"
        cols = st.columns([1.4, 1, 1, 1, 1])
        cols[0].markdown(f"**{label}**<br><span style='font-size:1.3rem;font-weight:700;'>{show(cur)}</span>",
                         unsafe_allow_html=True)
        for i, (name, d) in enumerate([("전일", d_prev), ("WoW", d_wow),
                                       ("MoM", d_mom), ("YoY", d_yoy)]):
            comp = val(d, col)
            delta = cmp_line(cur, comp)
            color = "#1D9E75" if delta.startswith("+") else ("#E5484D" if delta.startswith("-") else "#8A8F98")
            cols[i + 1].markdown(
                f"<div style='font-size:0.75rem;color:#8A8F98;'>{name}</div>"
                f"<div style='font-size:0.8rem;'>{show(comp)}</div>"
                f"<div style='color:{color};font-weight:600;'>{delta}</div>",
                unsafe_allow_html=True)
        st.markdown("<hr style='margin:6px 0;border:none;border-top:1px solid #F0F2F5;'>",
                    unsafe_allow_html=True)

    # ---- 오딧 SKU 표 ----
    st.markdown("#### 오딧 캐리어 SKU 판매 (선택일)")
    odit = _load_odit_daily()
    INCH = ["20인치 플랩", "29인치", "26인치", "24인치", "20인치"]
    COLORS = ["화이트", "실버", "다크그레이", "블랙", "솔티블루", "펄스레드", "아이시핑크", "웻그린"]

    def qty(d, inch, color):
        m = odit.get(d.isoformat() if hasattr(d, "isoformat") else d, {})
        return m.get(f"{inch}·{color}", 0)

    sel_s = sel.isoformat()
    prev_s = d_prev.isoformat()
    wow_s = d_wow.isoformat()
    mom_s = d_mom.isoformat()
    yoy_s = d_yoy.isoformat()
    if sel_s not in odit:
        st.caption("선택일에 오딧 SKU 판매 데이터가 없어요. (data/monthly 갱신 필요할 수 있어요)")

    # HTML 표 (셀 마우스오버로 WoW/MoM/YoY)
    html = ['<table style="border-collapse:collapse;font-size:12px;text-align:center;width:100%;">']
    html.append('<tr><th style="padding:5px;border:1px solid #E7EBF0;background:#F7F8FA;"></th>'
                + "".join(f'<th style="padding:5px;border:1px solid #E7EBF0;background:#F7F8FA;">{c}</th>'
                          for c in COLORS) + "</tr>")
    label_map = {"20인치 플랩": "플랩", "29인치": "29", "26인치": "26", "24인치": "24", "20인치": "20"}
    for inch in INCH:
        html.append(f'<tr><td style="padding:5px;border:1px solid #E7EBF0;background:#F7F8FA;'
                    f'font-weight:600;">{label_map[inch]}</td>')
        for color in COLORS:
            cur_q = odit.get(sel_s, {}).get(f"{inch}·{color}", 0)
            prev_q = odit.get(prev_s, {}).get(f"{inch}·{color}", 0)
            wow_q = odit.get(wow_s, {}).get(f"{inch}·{color}", 0)
            mom_q = odit.get(mom_s, {}).get(f"{inch}·{color}", 0)
            yoy_q = odit.get(yoy_s, {}).get(f"{inch}·{color}", 0)
            diff = cur_q - prev_q
            arrow = ""
            if diff > 0:
                arrow = f'<span style="color:#1D9E75;">▲{diff}</span>'
            elif diff < 0:
                arrow = f'<span style="color:#E5484D;">▼{abs(diff)}</span>'
            tip = f"전일 {prev_q} / WoW {wow_q} / MoM {mom_q} / YoY {yoy_q}"
            cell = f'{cur_q}' if cur_q else '<span style="color:#C8CDD3;">·</span>'
            if cur_q:
                cell += f'<br>{arrow}' if arrow else ''
            bg = "#FFFFFF" if cur_q else "#FBFBFC"
            html.append(f'<td title="{tip}" style="padding:5px;border:1px solid #E7EBF0;'
                        f'background:{bg};">{cell}</td>')
        html.append("</tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)
    st.caption("셀에 마우스를 올리면 전일·WoW·MoM·YoY 판매량이 보입니다. (숫자 아래 ▲▼는 전일 대비)")
    st.divider()


def _render_page_cards():
    st.header("🛍️ 오딧 상품 페이지별 판매")
    page = _load_page_daily()
    if not page:
        st.info("페이지별 판매 데이터가 없어요. (backfill 재실행 필요할 수 있어요)")
        st.divider()
        return
    PAGES = [("248", "오딧 캐리어 (일반)", "#378ADD"),
             ("270", "오딧 플랩 (20인치)", "#B060D0"),
             ("184", "세트할인", "#1D9E75")]
    days = sorted(page.keys())
    dmin = date.fromisoformat(days[0])
    dmax = date.fromisoformat(days[-1])
    sel = st.date_input("확인할 일자", dmax, min_value=dmin, max_value=dmax, key="page_date")
    if isinstance(sel, tuple):
        sel = sel[0]

    import datetime as _dt
    d_prev = sel - _dt.timedelta(days=1)
    d_wow = sel - _dt.timedelta(days=7)
    d_mom = sel - _dt.timedelta(days=28)
    try:
        d_yoy = sel.replace(year=sel.year - 1)
    except ValueError:
        d_yoy = sel - _dt.timedelta(days=365)

    def get(d, pno):
        return page.get(d.isoformat(), {}).get(pno, {"orders": 0, "q": 0, "a": 0, "cancel": 0})

    def cmp(cur, comp):
        if comp == 0:
            return ("+∞" if cur > 0 else "0%")
        p = (cur - comp) / comp * 100
        return f"{'+' if p >= 0 else ''}{p:.0f}%"

    st.caption(f"선택일 {sel} · 전일 {d_prev} · WoW {d_wow} · MoM {d_mom} · YoY {d_yoy}")
    cols = st.columns(len(PAGES))
    for i, (pno, name, color) in enumerate(PAGES):
        cur = get(sel, pno)
        orders = cur["orders"]; q = cur["q"]; a = cur["a"]; cancel = cur["cancel"]
        aov = round(a / orders) if orders else 0          # 객단가 = 매출 / 주문수
        # 취소율 = 취소수량 / (순수량 + 취소수량)
        total_q = q + cancel
        cancel_rate = (cancel / total_q * 100) if total_q else 0
        # 비교 (주문수 기준 증감)
        comps = []
        for label, d in [("전일", d_prev), ("WoW", d_wow), ("MoM", d_mom), ("YoY", d_yoy)]:
            co = get(d, pno)["orders"]
            comps.append((label, co, cmp(orders, co)))
        rows_html = ""
        for label, co, delta in comps:
            dc = "#1D9E75" if delta.startswith("+") else ("#E5484D" if delta.startswith("-") else "#8A8F98")
            rows_html += (f'<tr><td style="color:#8A8F98;padding:2px 6px;">{label}</td>'
                          f'<td style="text-align:right;padding:2px 6px;">{co}건</td>'
                          f'<td style="text-align:right;color:{dc};font-weight:600;padding:2px 6px;">{delta}</td></tr>')
        card = f'''<div style="border:1px solid #E7EBF0;border-top:4px solid {color};
            border-radius:12px;padding:16px;background:#fff;">
            <div style="font-size:0.8rem;color:#8A8F98;">#{pno}</div>
            <div style="font-size:1.05rem;font-weight:700;color:{color};margin-bottom:10px;">{name}</div>
            <div style="display:flex;flex-wrap:wrap;gap:12px 18px;margin-bottom:10px;">
              <div><div style="font-size:0.72rem;color:#8A8F98;">주문수</div>
                   <div style="font-size:1.25rem;font-weight:700;">{orders}건</div></div>
              <div><div style="font-size:0.72rem;color:#8A8F98;">구매 상품수</div>
                   <div style="font-size:1.25rem;font-weight:700;">{q}개</div></div>
              <div><div style="font-size:0.72rem;color:#8A8F98;">매출</div>
                   <div style="font-size:1.25rem;font-weight:700;">{_won_short(a)}</div></div>
              <div><div style="font-size:0.72rem;color:#8A8F98;">객단가</div>
                   <div style="font-size:1.25rem;font-weight:700;">{_won_aov(aov)}</div></div>
              <div><div style="font-size:0.72rem;color:#8A8F98;">취소율</div>
                   <div style="font-size:1.25rem;font-weight:700;">{cancel_rate:.0f}%
                   <span style="font-size:0.7rem;color:#8A8F98;font-weight:400;">({cancel}개)</span></div></div>
            </div>
            <table style="width:100%;font-size:0.78rem;border-collapse:collapse;">{rows_html}</table>
            <div style="font-size:0.68rem;color:#B8BCC2;margin-top:6px;">※ 객단가=매출÷주문수 · 증감은 주문수 기준</div>
            </div>'''
        cols[i].markdown(card, unsafe_allow_html=True)
    st.caption("조회수·전환율은 접속 통계 API 확보 후 추가 예정이에요.")
    st.divider()


def _render_sales_ad_trend(df):
    st.header("📈 채널별 매출 · 광고비 추이")
    valid = df[df["dtc_sales"] > 0]
    if valid.empty:
        st.info("데이터가 없어요.")
        return
    dmin, dmax = df["date"].min(), valid["date"].max()

    SALES = [
        ("자사몰", "dtc_sales", "#378ADD"), ("네이버", "ss_sales", "#3FA972"),
        ("쿠팡", "coupang_sales", "#E0A800"), ("기타", "etc_sales", "#B8BCC2"),
    ]
    ADS = [
        ("메타", "ad_메타_cost", "#4267B2"), ("구글", "ad_구글_cost", "#EA4335"),
        ("GFA", "ad_GFA_cost", "#03C75A"), ("인플 YT", "influ_yt", "#B060D0"),
        ("인플 PA", "influ_pa", "#8E44AD"),
    ]
    sales_names = [s[0] for s in SALES]
    ad_names = [a[0] for a in ADS]

    rng = st.date_input("기간", (dmax - timedelta(days=29), dmax),
                        min_value=dmin, max_value=dmax, key="trend_range")
    fc1, fc2 = st.columns(2)
    with fc1:
        sel_sales = st.multiselect("매출처", sales_names, default=sales_names, key="trend_sales")
    with fc2:
        sel_ads = st.multiselect("광고 매체", ad_names, default=ad_names, key="trend_ads")

    if isinstance(rng, tuple) and len(rng) == 2:
        d_from, d_to = rng
    else:
        d_from = d_to = rng if not isinstance(rng, tuple) else dmax
    view = df[(df["date"] >= d_from) & (df["date"] <= d_to)].copy().sort_values("date")
    if view.empty:
        st.caption("선택 기간에 데이터가 없어요.")
        return
    xs = [d.strftime("%m/%d") for d in view["date"]]

    def col(c):
        return list(view[c]) if c in view else [0] * len(view)

    fig = go.Figure()
    # ---- 매출 채널 (실선, 왼쪽 축 y) — 선택된 것만 ----
    for name, c, color in SALES:
        if name not in sel_sales:
            continue
        fig.add_trace(go.Scatter(x=xs, y=col(c), name=f"매출·{name}", yaxis="y",
                                 mode="lines", line=dict(color=color, width=2)))
    # ---- 광고비 채널 (점선, 오른쪽 축 y2) — 선택된 것만 ----
    for name, c, color in ADS:
        if name not in sel_ads:
            continue
        fig.add_trace(go.Scatter(x=xs, y=col(c), name=f"광고비·{name}", yaxis="y2",
                                 mode="lines", line=dict(color=color, width=1.5, dash="dot")))
    # ---- CRM 발송일 점 (오른쪽 축) ----
    crm_x = [x for x, v in zip(xs, col("crm_cost")) if v and v > 0]
    crm_y = [v for v in col("crm_cost") if v and v > 0]
    if crm_x:
        fig.add_trace(go.Scatter(x=crm_x, y=crm_y, name="CRM 발송", yaxis="y2",
                                 mode="markers", marker=dict(color="#F0883E", size=11,
                                                             symbol="diamond")))
    fig.update_layout(
        height=420, plot_bgcolor="white", margin=dict(t=10, b=10, l=10, r=10),
        yaxis=dict(title="매출", gridcolor="#EEF1F5", tickformat=","),
        yaxis2=dict(title="광고비", overlaying="y", side="right", showgrid=False, tickformat=","),
        legend=dict(orientation="h", y=-0.18, font=dict(size=10)),
        hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption("실선=매출(왼쪽 축) · 점선=광고비(오른쪽 축) · 주황 다이아=CRM 발송일. "
               "위 필터로 매출처·광고 매체를 선택하거나, 범례 클릭으로도 켜고 끌 수 있어요.")
    st.divider()


def render_mkt_tab():
    st.title("MKT 그로스 분석")
    st.caption("일별 마케팅 리포트(daily report) 기반 채널 매출·광고 효율 분석.")

    sheet_url = DEFAULT_SHEET_URL
    try:
        sheet_url = st.secrets.get("MKT_SHEET_URL", DEFAULT_SHEET_URL) or DEFAULT_SHEET_URL
    except Exception:
        pass
    with st.expander("데이터 소스 (구글 시트)"):
        st.caption("기본 daily report 시트가 자동 연동됩니다.")
        custom = st.text_input("다른 시트 URL", value="",
                               placeholder="비워두면 기본 시트 사용")
        if custom.strip():
            sheet_url = custom.strip()

    csv_url = _to_csv_url(sheet_url)
    if not csv_url:
        st.error("구글 시트 URL 형식이 아니에요.")
        return
    try:
        df = _load(csv_url)
    except Exception as e:
        st.error(f"시트를 불러오지 못했어요: {e}\n\n"
                 "시트가 '링크가 있는 모든 사용자에게 공개(보기)'인지 확인하세요.")
        return
    if df.empty:
        st.warning("데이터 행이 없어요.")
        return

    # =========================================================
    # 0. 그로스 섹션 (최근 7일 vs 이전 7일) — 자체 계산, 필터 독립
    # =========================================================
    _render_growth(df)

    _render_daily_checkin(df)

    _render_page_cards()

    _render_sales_ad_trend(df)

    dmin, dmax = df["date"].min(), df["date"].max()
    # 실제 매출이 있는 마지막 날 기준으로 기본 기간
    valid = df[df["dtc_sales"] > 0]
    end_default = valid["date"].max() if not valid.empty else dmax
    c1, c2 = st.columns([2, 1])
    with c1:
        rng = st.date_input("기간", (end_default - timedelta(days=29), end_default),
                            min_value=dmin, max_value=dmax, key="mkt_range")
    with c2:
        unit = st.radio("단위", ["일", "주", "월"], horizontal=True, index=0, key="mkt_unit")
    if isinstance(rng, tuple) and len(rng) == 2:
        d_from, d_to = rng
    else:
        d_from = d_to = rng if not isinstance(rng, tuple) else dmin
    view = df[(df["date"] >= d_from) & (df["date"] <= d_to)].copy()
    if view.empty:
        st.caption("선택 기간에 데이터가 없어요.")
        return

    def bucket(d):
        if unit == "일":
            return d.isoformat()
        if unit == "월":
            return d.strftime("%Y-%m")
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    view["bucket"] = view["date"].map(bucket)

    # =========================================================
    # 1. 채널별 매출 종합
    # =========================================================
    st.header("1. 채널별 매출 종합")
    ch = {"자사몰": "dtc_sales", "네이버(스스)": "ss_sales",
          "쿠팡": "coupang_sales", "그외": "etc_sales"}
    tot = {n: view[c].sum() for n, c in ch.items()}
    grand = sum(tot.values()) or 1
    cols = st.columns(len(ch))
    for i, (n, v) in enumerate(tot.items()):
        cols[i].metric(n, _won_short(v), f"{v/grand*100:.0f}%")
    g1, g2 = st.columns([1, 1.4])
    with g1:
        st.markdown("**채널 매출 비중**")
        names = [n for n in ch if tot[n] > 0]
        if names:
            fig = go.Figure(go.Pie(values=[tot[n] for n in names], labels=names, hole=0.5))
            fig.update_traces(textinfo="percent+label")
            fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with g2:
        st.markdown("**채널별 매출 추이**")
        gg = view.groupby("bucket").agg(
            자사몰=("dtc_sales", "sum"), 네이버=("ss_sales", "sum"),
            쿠팡=("coupang_sales", "sum"), 그외=("etc_sales", "sum")).reset_index().sort_values("bucket")
        fig = go.Figure()
        for n, cc in [("자사몰", "#378ADD"), ("네이버", "#3FA972"), ("쿠팡", "#E0A800"), ("그외", "#B8BCC2")]:
            fig.add_trace(go.Bar(x=gg["bucket"], y=gg[n], name=n, marker_color=cc))
        fig.update_layout(barmode="stack", height=300, plot_bgcolor="white",
                          margin=dict(t=10, b=10, l=10, r=10),
                          yaxis=dict(gridcolor="#EEF1F5", tickformat=","),
                          legend=dict(orientation="h", y=1.15))
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # =========================================================
    # 2. 광고 채널별 효율
    # =========================================================
    st.divider()
    st.header("2. 광고 채널별 효율")
    rows = []
    for name in AD:
        cost = view[f"ad_{name}_cost"].sum()
        sales = view[f"ad_{name}_sales"].sum()
        if cost == 0 and sales == 0:
            continue
        rows.append({"채널": name, "광고비": cost, "광고매출": sales, "ROAS": sales / cost if cost else 0})
    cp_cost = view["coupang_ad_cost"].sum(); cp_sales = view["coupang_ad_sales"].sum()
    if cp_cost or cp_sales:
        rows.append({"채널": "쿠팡", "광고비": cp_cost, "광고매출": cp_sales,
                     "ROAS": cp_sales / cp_cost if cp_cost else 0})
    crm = view["crm_cost"].sum(); influ = view["influ_cost"].sum()
    if crm:
        rows.append({"채널": "CRM", "광고비": crm, "광고매출": 0, "ROAS": 0})
    if influ:
        rows.append({"채널": "인플루언서", "광고비": influ, "광고매출": 0, "ROAS": 0})
    if rows:
        addf = pd.DataFrame(rows)
        a1, a2 = st.columns([1.2, 1])
        with a1:
            st.markdown("**채널별 광고비 & ROAS**")
            fig = go.Figure()
            fig.add_trace(go.Bar(x=addf["채널"], y=addf["광고비"], name="광고비", marker_color="#C9D6E5"))
            fig.add_trace(go.Scatter(x=addf["채널"], y=addf["ROAS"], name="ROAS", yaxis="y2",
                                     mode="markers+text", marker=dict(size=12, color="#E0A800"),
                                     text=[f"{r*100:.0f}%" for r in addf["ROAS"]], textposition="top center"))
            fig.update_layout(height=320, plot_bgcolor="white", margin=dict(t=10, b=10, l=10, r=10),
                              yaxis=dict(title="광고비", gridcolor="#EEF1F5", tickformat=","),
                              yaxis2=dict(title="ROAS", overlaying="y", side="right", showgrid=False),
                              legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        with a2:
            st.markdown("**요약 표**")
            addf_disp = addf.copy()
            addf_disp["ROAS"] = addf_disp["ROAS"] * 100
            st.dataframe(addf_disp.style.format({"광고비": "₩{:,.0f}", "광고매출": "₩{:,.0f}", "ROAS": "{:.0f}%"}),
                         hide_index=True, use_container_width=True)
        tc = addf["광고비"].sum(); ts = addf["광고매출"].sum()
        if tc:
            st.caption(f"총 광고비 {_won_short(tc)} · 총 광고매출 {_won_short(ts)} · 통합 ROAS {ts/tc*100:.0f}%")

    # =========================================================
    # 3. 자사몰 상세
    # =========================================================
    st.divider()
    st.header("3. 자사몰 상세")
    fa = view["dtc_first_amt"].sum(); ra = view["dtc_re_amt"].sum()
    refund = view["dtc_refund_amt"].sum(); sales = view["dtc_sales"].sum()
    visit = view["dtc_visit"].sum(); nv = view["dtc_new"].sum(); rv = view["dtc_return"].sum()
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("자사몰 매출", _won_short(sales))
    d2.metric("첫구매 매출", _won_short(fa), f"{fa/(fa+ra)*100:.0f}%" if (fa+ra) else "")
    d3.metric("재구매 매출", _won_short(ra), f"{ra/(fa+ra)*100:.0f}%" if (fa+ra) else "")
    d4.metric("환불액", _won_short(refund), f"환불율 {refund/sales*100:.1f}%" if sales else "")
    e1, e2, e3 = st.columns(3)
    e1.metric("총 유입", f"{visit:,.0f}")
    e2.metric("신규 방문", f"{nv:,.0f}", f"{nv/visit*100:.0f}%" if visit else "")
    e3.metric("재방문", f"{rv:,.0f}", f"{rv/visit*100:.0f}%" if visit else "")
    st.markdown("**첫구매 vs 재구매 매출 추이**")
    gg = view.groupby("bucket").agg(
        첫구매=("dtc_first_amt", "sum"), 재구매=("dtc_re_amt", "sum")).reset_index().sort_values("bucket")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=gg["bucket"], y=gg["첫구매"], name="첫구매", marker_color="#378ADD"))
    fig.add_trace(go.Bar(x=gg["bucket"], y=gg["재구매"], name="재구매", marker_color="#3FA972"))
    fig.update_layout(barmode="stack", height=300, plot_bgcolor="white",
                      margin=dict(t=10, b=10, l=10, r=10),
                      yaxis=dict(gridcolor="#EEF1F5", tickformat=","),
                      legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
