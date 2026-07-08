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
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

DEFAULT_SHEET_URL = ("https://docs.google.com/spreadsheets/d/"
                     "1MHA572md96wxuos5x2EMuMiMmguWicea0nJTczjlLns/edit?gid=1873639498#gid=1873639498")

# 채널 매출 열
COL = {
    "dtc_sales": 14, "dtc_visit": 15, "dtc_new": 17, "dtc_return": 19,
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
            rec["coupang_sales"] = _num(raw.iloc[r, COUPANG_SALES]) if COUPANG_SALES < raw.shape[1] else 0.0
            rec["coupang_ad_cost"] = _num(raw.iloc[r, COUPANG_AD[0]]) if COUPANG_AD[0] < raw.shape[1] else 0.0
            rec["coupang_ad_sales"] = _num(raw.iloc[r, COUPANG_AD[1]]) if COUPANG_AD[1] < raw.shape[1] else 0.0
            rows.append(rec)
    return pd.DataFrame(rows)


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


def _delta_str(cur, prev):
    if prev == 0:
        return ("+∞" if cur > 0 else "0%")
    p = (cur - prev) / prev * 100
    return f"{'+' if p >= 0 else ''}{p:.0f}%"


def _render_growth(df):
    st.header("🚀 그로스 (최근 7일 vs 이전 7일)")
    # 실적 있는 마지막 날 기준 최근 7일 / 직전 7일
    valid = df[df["dtc_sales"] > 0]
    if valid.empty:
        st.info("실적 데이터가 없어요.")
        return
    last = valid["date"].max()
    cur = df[(df["date"] > last - timedelta(days=7)) & (df["date"] <= last)]
    prev = df[(df["date"] > last - timedelta(days=14)) & (df["date"] <= last - timedelta(days=7))]
    st.caption(f"최근 7일: {last - timedelta(days=6)} ~ {last}  ·  "
               f"이전 7일: {last - timedelta(days=13)} ~ {last - timedelta(days=7)}")

    def s(frame, col):
        return frame[col].sum() if col in frame else 0

    # ---------- 1. 매출 ----------
    st.markdown("#### 1. 매출")
    sale_ch = {"자사몰": "dtc_sales", "네이버": "ss_sales", "쿠팡": "coupang_sales", "기타": "etc_sales"}
    cur_total = sum(s(cur, c) for c in sale_ch.values())
    prev_total = sum(s(prev, c) for c in sale_ch.values())
    m0 = st.columns(len(sale_ch) + 1)
    m0[0].metric("전체 매출", _won_short(cur_total), _delta_str(cur_total, prev_total))
    for i, (name, col) in enumerate(sale_ch.items()):
        cv, pv = s(cur, col), s(prev, col)
        share = cv / cur_total * 100 if cur_total else 0
        m0[i + 1].metric(name, _won_short(cv), f"{share:.0f}% · {_delta_str(cv, pv)}")

    # ---------- 2. 광고비 ----------
    st.markdown("#### 2. 광고비")
    ad_ch = {"메타": "ad_메타_cost", "구글": "ad_구글_cost", "GFA": "ad_GFA_cost",
             "브랜드검색": "ad_네이버 브랜드검색_cost", "네이버SA": "ad_네이버 SA_cost",
             "CRM": "crm_cost", "인플루언서": "influ_cost"}
    cur_ad = sum(s(cur, c) for c in ad_ch.values())
    prev_ad = sum(s(prev, c) for c in ad_ch.values())
    st.metric("전체 광고비", _won_short(cur_ad), _delta_str(cur_ad, prev_ad))
    present = [(n, c) for n, c in ad_ch.items() if s(cur, c) > 0 or s(prev, c) > 0]
    per_row = 4
    for start in range(0, len(present), per_row):
        chunk = present[start:start + per_row]
        cols = st.columns(per_row)
        for j, (name, col) in enumerate(chunk):
            cv, pv = s(cur, col), s(prev, col)
            share = cv / cur_ad * 100 if cur_ad else 0
            cols[j].metric(name, _won_short(cv), f"{share:.0f}% · {_delta_str(cv, pv)}")

    # ---------- 3. ROAS ----------
    st.markdown("#### 3. ROAS")
    def roas(sales, cost):
        return sales / cost if cost else 0
    # 전체
    cur_roas = roas(cur_total, cur_ad)
    prev_roas = roas(prev_total, prev_ad)
    # 자사몰 = 자사몰매출 / (메타+구글+브검)
    dtc_ad_c = s(cur, "ad_메타_cost") + s(cur, "ad_구글_cost") + s(cur, "ad_네이버 브랜드검색_cost")
    dtc_ad_p = s(prev, "ad_메타_cost") + s(prev, "ad_구글_cost") + s(prev, "ad_네이버 브랜드검색_cost")
    dtc_roas_c = roas(s(cur, "dtc_sales"), dtc_ad_c)
    dtc_roas_p = roas(s(prev, "dtc_sales"), dtc_ad_p)
    # 네이버 = 네이버매출 / (GFA+SA)
    nv_ad_c = s(cur, "ad_GFA_cost") + s(cur, "ad_네이버 SA_cost")
    nv_ad_p = s(prev, "ad_GFA_cost") + s(prev, "ad_네이버 SA_cost")
    nv_roas_c = roas(s(cur, "ss_sales"), nv_ad_c)
    nv_roas_p = roas(s(prev, "ss_sales"), nv_ad_p)
    r = st.columns(3)
    r[0].metric("전체 ROAS", f"{cur_roas:.2f}", _delta_str(cur_roas, prev_roas))
    r[1].metric("자사몰 ROAS", f"{dtc_roas_c:.2f}", _delta_str(dtc_roas_c, dtc_roas_p),
                help="자사몰 매출 / (메타+구글+브랜드검색)")
    r[2].metric("네이버 ROAS", f"{nv_roas_c:.2f}", _delta_str(nv_roas_c, nv_roas_p),
                help="네이버 매출 / (GFA+네이버SA)")

    # 인플루언서·CRM: 집행 날짜 표시
    def spend_dates(frame, col):
        if col not in frame:
            return []
        return [d.strftime("%m/%d") for d, v in zip(frame["date"], frame[col]) if v and v > 0]
    influ_days = spend_dates(cur, "influ_cost")
    crm_days = spend_dates(cur, "crm_cost")
    notes = []
    if influ_days:
        notes.append(f"인플루언서 집행일: {', '.join(influ_days)} (합계 {_won_short(s(cur,'influ_cost'))})")
    if crm_days:
        notes.append(f"CRM 집행일: {', '.join(crm_days)} (합계 {_won_short(s(cur,'crm_cost'))})")
    if notes:
        st.caption("　|　".join(notes))
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
                                     text=[f"{r:.1f}" for r in addf["ROAS"]], textposition="top center"))
            fig.update_layout(height=320, plot_bgcolor="white", margin=dict(t=10, b=10, l=10, r=10),
                              yaxis=dict(title="광고비", gridcolor="#EEF1F5", tickformat=","),
                              yaxis2=dict(title="ROAS", overlaying="y", side="right", showgrid=False),
                              legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        with a2:
            st.markdown("**요약 표**")
            st.dataframe(addf.style.format({"광고비": "₩{:,.0f}", "광고매출": "₩{:,.0f}", "ROAS": "{:.2f}"}),
                         hide_index=True, use_container_width=True)
        tc = addf["광고비"].sum(); ts = addf["광고매출"].sum()
        if tc:
            st.caption(f"총 광고비 {_won_short(tc)} · 총 광고매출 {_won_short(ts)} · 통합 ROAS {ts/tc:.2f}")

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
