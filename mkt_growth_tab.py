"""
MKT 그로스 분석 탭.
공개 구글 스프레드시트 'daily report' 시트(열 위치 고정)를 CSV로 불러와,
채널별 매출·광고 채널별 효율·자사몰 상세를 분석한다.

전제: daily report 시트는 헤더가 상단에 있고 날짜(col0)는 13행쯤부터 시작.
CSV로 받으면 상단 요약행이 함께 오므로, '날짜로 파싱되는 행'만 데이터로 사용한다.
열 위치는 고정 매핑(COLS). 시트에서 열 순서가 바뀌면 이 매핑을 갱신해야 함.
"""
import re
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---- daily report 열 매핑 (0-based 열 인덱스) ----
COLS = {
    "date": 0,
    # 자사몰
    "dtc_sales": 14, "dtc_visit": 15, "dtc_new": 17, "dtc_return": 19,
    "dtc_first_ord": 33, "dtc_first_amt": 34, "dtc_re_ord": 36, "dtc_re_amt": 37,
    "dtc_re_rate": 39, "dtc_refund_amt": 40, "dtc_refund_rate": 41,
    "dtc_cvr": 42, "dtc_aov": 43,
    # 스스(네이버)
    "ss_sales": 45, "ss_visit": 46, "ss_conv": 47, "ss_pay": 48,
    "ss_refund_cnt": 50, "ss_refund_amt": 51, "ss_refund_rate": 52,
    "ss_cvr": 53, "ss_aov": 54,
    # 기타 채널 매출
    "coupang_sales": 56, "etc_sales": 58, "popup_sales": 60,
    # 광고 채널 (광고비/매출/ROAS 중심)
    "meta_cost": 65, "meta_imp": 66, "meta_click": 67, "meta_buy": 70,
    "meta_sales": 73, "meta_roas": 75,
    "google_cost": 110, "google_sales": 118, "google_roas": 120,
    "gfa_cost": 121, "gfa_sales": 129, "gfa_roas": 131,
    "bk_cost": 133, "bk_sales": 141, "bk_roas": 143,
    "sa_cost": 147, "sa_sales": 155, "sa_roas": 157,
    "influ_yt": 145, "influ_pa": 146,
}


def _to_csv_url(url):
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        return None
    sheet_id = m.group(1)
    gid = "0"
    g = re.search(r"[#&?]gid=([0-9]+)", url)
    if g:
        gid = g.group(1)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _num(v):
    """숫자로 변환. #DIV/0! 등 에러·빈값은 0."""
    try:
        f = float(str(v).replace(",", ""))
        return f
    except Exception:
        return 0.0


@st.cache_data(ttl=300, show_spinner="시트를 불러오는 중...")
def _load_daily(csv_url):
    raw = pd.read_csv(csv_url, header=None)
    rows = []
    for i in range(len(raw)):
        d = pd.to_datetime(raw.iloc[i, 0], errors="coerce")
        if pd.isna(d):
            continue
        rec = {"date": d.date()}
        for k, c in COLS.items():
            if k == "date":
                continue
            rec[k] = _num(raw.iloc[i, c]) if c < raw.shape[1] else 0.0
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


def render_mkt_tab():
    st.title("MKT 그로스 분석")
    st.caption("일별 마케팅 리포트(daily report 시트)를 채널별 매출·광고 효율로 분석합니다.")

    default_url = ""
    try:
        default_url = st.secrets.get("MKT_SHEET_URL", "")
    except Exception:
        pass
    url = st.text_input("구글 시트 URL (daily report 시트)", value=default_url,
                        placeholder="https://docs.google.com/spreadsheets/d/.../edit#gid=...",
                        help="daily report 탭을 연 URL(gid 포함). '링크가 있는 모든 사용자-보기' 공유 필요.")
    if not url:
        st.info("daily report 시트 URL을 넣으면 분석을 시작합니다.")
        return
    csv_url = _to_csv_url(url)
    if not csv_url:
        st.error("구글 시트 URL 형식이 아니에요.")
        return
    try:
        df = _load_daily(csv_url)
    except Exception as e:
        st.error(f"시트를 불러오지 못했어요: {e}")
        return
    if df.empty:
        st.warning("날짜로 인식되는 데이터 행이 없어요. daily report 시트가 맞는지 확인하세요.")
        return

    # ---- 기간/단위 필터 ----
    dmin, dmax = df["date"].min(), df["date"].max()
    c1, c2 = st.columns([2, 1])
    with c1:
        rng = st.date_input("기간", (max(dmin, dmax - timedelta(days=29)), dmax),
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

    # 채널 매출 합산 (그외+팝업 합침)
    view["etc_all"] = view["etc_sales"] + view["popup_sales"]

    # =========================================================
    # 1. 채널별 매출 종합
    # =========================================================
    st.header("1. 채널별 매출 종합")
    ch = {"자사몰": "dtc_sales", "네이버(스스)": "ss_sales",
          "쿠팡": "coupang_sales", "그외+팝업": "etc_all"}
    tot = {name: view[col].sum() for name, col in ch.items()}
    grand = sum(tot.values()) or 1
    cols = st.columns(len(ch))
    for i, (name, val) in enumerate(tot.items()):
        cols[i].metric(name, _won_short(val), f"{val/grand*100:.0f}%")

    gcol1, gcol2 = st.columns([1, 1.4])
    with gcol1:
        st.markdown("**채널 매출 비중**")
        names = [n for n in ch if tot[n] > 0]
        if names:
            fig = go.Figure(go.Pie(values=[tot[n] for n in names], labels=names, hole=0.5))
            fig.update_traces(textinfo="percent+label")
            fig.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with gcol2:
        st.markdown("**채널별 매출 추이**")
        g = view.groupby("bucket").agg(
            자사몰=("dtc_sales", "sum"), 네이버=("ss_sales", "sum"),
            쿠팡=("coupang_sales", "sum"), 그외팝업=("etc_all", "sum")).reset_index().sort_values("bucket")
        fig = go.Figure()
        for name, color in [("자사몰", "#378ADD"), ("네이버", "#3FA972"),
                            ("쿠팡", "#E0A800"), ("그외팝업", "#B8BCC2")]:
            fig.add_trace(go.Bar(x=g["bucket"], y=g[name], name=name, marker_color=color))
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
    ad_ch = {"메타": ("meta_cost", "meta_sales"), "구글": ("google_cost", "google_sales"),
             "GFA": ("gfa_cost", "gfa_sales"), "브랜드검색": ("bk_cost", "bk_sales"),
             "네이버SA": ("sa_cost", "sa_sales")}
    rows = []
    for name, (cc, sc) in ad_ch.items():
        cost = view[cc].sum(); sales = view[sc].sum()
        if cost == 0 and sales == 0:
            continue
        rows.append({"채널": name, "광고비": cost, "광고매출": sales,
                     "ROAS": (sales / cost) if cost else 0})
    influ = view["influ_yt"].sum() + view["influ_pa"].sum()
    if influ > 0:
        rows.append({"채널": "인플루언서", "광고비": influ, "광고매출": 0, "ROAS": 0})
    if rows:
        addf = pd.DataFrame(rows)
        ac1, ac2 = st.columns([1.2, 1])
        with ac1:
            st.markdown("**채널별 광고비 & ROAS**")
            fig = go.Figure()
            fig.add_trace(go.Bar(x=addf["채널"], y=addf["광고비"], name="광고비",
                                 marker_color="#C9D6E5"))
            fig.add_trace(go.Scatter(x=addf["채널"], y=addf["ROAS"], name="ROAS", yaxis="y2",
                                     mode="markers+text", marker=dict(size=12, color="#E0A800"),
                                     text=[f"{r:.1f}" for r in addf["ROAS"]], textposition="top center"))
            fig.update_layout(height=320, plot_bgcolor="white",
                              margin=dict(t=10, b=10, l=10, r=10),
                              yaxis=dict(title="광고비", gridcolor="#EEF1F5", tickformat=","),
                              yaxis2=dict(title="ROAS", overlaying="y", side="right", showgrid=False),
                              legend=dict(orientation="h", y=1.15))
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        with ac2:
            st.markdown("**요약 표**")
            st.dataframe(
                addf.style.format({"광고비": "₩{:,.0f}", "광고매출": "₩{:,.0f}", "ROAS": "{:.2f}"}),
                hide_index=True, use_container_width=True)
        tot_cost = addf["광고비"].sum()
        tot_sales = addf["광고매출"].sum()
        st.caption(f"총 광고비 {_won_short(tot_cost)} · 총 광고매출 {_won_short(tot_sales)} · "
                   f"통합 ROAS {tot_sales/tot_cost:.2f}" if tot_cost else "")

    # =========================================================
    # 3. 자사몰 상세 (첫구매·재구매·환불·유입)
    # =========================================================
    st.divider()
    st.header("3. 자사몰 상세")
    first_amt = view["dtc_first_amt"].sum()
    re_amt = view["dtc_re_amt"].sum()
    refund = view["dtc_refund_amt"].sum()
    dtc_sales = view["dtc_sales"].sum()
    visit = view["dtc_visit"].sum()
    new_v = view["dtc_new"].sum()
    re_v = view["dtc_return"].sum()
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("자사몰 매출", _won_short(dtc_sales))
    d2.metric("첫구매 매출", _won_short(first_amt),
              f"{first_amt/(first_amt+re_amt)*100:.0f}%" if (first_amt+re_amt) else "")
    d3.metric("재구매 매출", _won_short(re_amt),
              f"{re_amt/(first_amt+re_amt)*100:.0f}%" if (first_amt+re_amt) else "")
    d4.metric("환불액", _won_short(refund),
              f"환불율 {refund/dtc_sales*100:.1f}%" if dtc_sales else "")
    d5, d6, d7 = st.columns(3)
    d5.metric("총 유입", f"{visit:,.0f}")
    d6.metric("신규 방문", f"{new_v:,.0f}", f"{new_v/visit*100:.0f}%" if visit else "")
    d7.metric("재방문", f"{re_v:,.0f}", f"{re_v/visit*100:.0f}%" if visit else "")

    st.markdown("**첫구매 vs 재구매 매출 추이**")
    g = view.groupby("bucket").agg(
        첫구매=("dtc_first_amt", "sum"), 재구매=("dtc_re_amt", "sum")).reset_index().sort_values("bucket")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=g["bucket"], y=g["첫구매"], name="첫구매", marker_color="#378ADD"))
    fig.add_trace(go.Bar(x=g["bucket"], y=g["재구매"], name="재구매", marker_color="#3FA972"))
    fig.update_layout(barmode="stack", height=300, plot_bgcolor="white",
                      margin=dict(t=10, b=10, l=10, r=10),
                      yaxis=dict(gridcolor="#EEF1F5", tickformat=","),
                      legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
