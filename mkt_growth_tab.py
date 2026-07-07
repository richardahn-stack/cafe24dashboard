"""
MKT 그로스 분석 탭.
공개 구글 스프레드시트(일단위 매출 목표 시트)를 CSV로 불러와,
우리 실제 매출(data/monthly)과 날짜 기준으로 합쳐 목표 대비 실적·ROAS 를 분석한다.

시트 필요 컬럼(자사몰 일단위 매출 목표 시트 기준):
  날짜, DTC 목표 매출, DTC_메타, DTC_구글, DTC_브검, DTC 합산 광고비
"""
import os
import re
import glob
import json
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


@st.cache_data(ttl=300)
def _load_actual_daily():
    """월별 파일의 cat_daily 를 일자별 총매출(dict: 'YYYY-MM-DD' -> 매출)로."""
    out = {}
    for path in sorted(glob.glob(os.path.join("data", "monthly", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                md = json.load(f)
        except Exception:
            continue
        for dt, cats in md.get("cat_daily", {}).items():
            s = sum((c["a"] if isinstance(c, dict) else c) for c in cats.values())
            out[dt] = out.get(dt, 0) + s
    return out


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


@st.cache_data(ttl=300, show_spinner="구글 시트를 불러오는 중...")
def _load_sheet(csv_url):
    return pd.read_csv(csv_url)


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
    st.caption("일단위 매출 목표 시트를 실제 매출과 합쳐 목표 대비 실적·ROAS를 봅니다.")

    default_url = ""
    try:
        default_url = st.secrets.get("MKT_SHEET_URL", "")
    except Exception:
        pass
    url = st.text_input("구글 시트 URL (자사몰 일단위 매출 목표 시트)", value=default_url,
                        placeholder="https://docs.google.com/spreadsheets/d/.../edit#gid=...",
                        help="해당 시트 탭을 연 URL(gid 포함)을 넣으세요. '링크가 있는 모든 사용자-보기' 공유 필요.")
    if not url:
        st.info("시트 URL을 넣으면 분석을 시작합니다. (시트를 '링크가 있는 모든 사용자 - 보기'로 공유하세요)")
        return

    csv_url = _to_csv_url(url)
    if not csv_url:
        st.error("구글 시트 URL 형식이 아니에요.")
        return
    try:
        raw = _load_sheet(csv_url)
    except Exception as e:
        st.error(f"시트를 불러오지 못했어요: {e}")
        return

    raw = raw.rename(columns={c: str(c).strip() for c in raw.columns})

    def find_col(*keys):
        for c in raw.columns:
            cc = str(c).replace(" ", "")
            for k in keys:
                if k.replace(" ", "") in cc:
                    return c
        return None

    col_date = find_col("날짜", "date")
    col_goal = find_col("목표 매출", "DTC 목표")
    col_meta = find_col("메타")
    col_google = find_col("구글")
    col_bk = find_col("브검", "브랜드검색")
    col_adsum = find_col("합산 광고비", "총 광고비")

    if not col_date or not col_goal:
        st.error("시트에서 '날짜' 또는 'DTC 목표 매출' 컬럼을 찾지 못했어요.")
        st.write("불러온 컬럼:", list(raw.columns))
        st.dataframe(raw.head(20), use_container_width=True)
        return

    df = pd.DataFrame()
    df["date"] = pd.to_datetime(raw[col_date], errors="coerce").dt.date
    df["goal"] = pd.to_numeric(raw[col_goal], errors="coerce")
    for name, c in [("meta", col_meta), ("google", col_google), ("bk", col_bk), ("adsum", col_adsum)]:
        df[name] = pd.to_numeric(raw[c], errors="coerce") if c else 0
    df = df.dropna(subset=["date"])
    if df.empty:
        st.warning("유효한 날짜 데이터가 없어요.")
        return
    if not col_adsum:
        df["adsum"] = df[["meta", "google", "bk"]].sum(axis=1)

    actual = _load_actual_daily()
    df["actual"] = df["date"].map(lambda d: actual.get(d.isoformat(), None))

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

    tot_goal = view["goal"].sum()
    tot_actual = view["actual"].dropna().sum()
    tot_ad = view["adsum"].sum()
    achieve = (tot_actual / tot_goal * 100) if tot_goal else 0
    roas = (tot_actual / tot_ad) if tot_ad else 0
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("목표 매출", _won_short(tot_goal))
    m2.metric("실제 매출", _won_short(tot_actual), f"{achieve:.0f}% 달성")
    m3.metric("광고비", _won_short(tot_ad))
    m4.metric("ROAS", f"{roas:.2f}" if tot_ad else "-",
              help="실제매출 ÷ 광고비 (실제 매출은 자사몰 데이터 기준)")
    if view["actual"].isna().all():
        st.warning("이 기간의 실제 매출 데이터가 없어요. (data/monthly에 해당 날짜가 없을 수 있어요)")

    def bucket(d):
        if unit == "일":
            return d.isoformat()
        if unit == "월":
            return d.strftime("%Y-%m")
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    view["bucket"] = view["date"].map(bucket)
    grp = view.groupby("bucket").agg(
        goal=("goal", "sum"), actual=("actual", "sum"),
        meta=("meta", "sum"), google=("google", "sum"),
        bk=("bk", "sum"), adsum=("adsum", "sum")).reset_index().sort_values("bucket")

    st.markdown("#### 목표 대비 실제 매출")
    fig = go.Figure()
    fig.add_trace(go.Bar(x=grp["bucket"], y=grp["goal"], name="목표", marker_color="#C9D6E5"))
    fig.add_trace(go.Bar(x=grp["bucket"], y=grp["actual"], name="실제", marker_color="#378ADD"))
    fig.update_layout(barmode="group", height=340, plot_bgcolor="white",
                      margin=dict(t=10, b=10, l=10, r=10),
                      yaxis=dict(gridcolor="#EEF1F5", tickformat=","),
                      legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown("#### 광고비 & ROAS")
    grp["roas"] = grp.apply(lambda r: (r["actual"] / r["adsum"]) if r["adsum"] else None, axis=1)
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=grp["bucket"], y=grp["meta"], name="메타", marker_color="#4267B2"))
    fig2.add_trace(go.Bar(x=grp["bucket"], y=grp["google"], name="구글", marker_color="#EA4335"))
    fig2.add_trace(go.Bar(x=grp["bucket"], y=grp["bk"], name="브검", marker_color="#34A853"))
    fig2.add_trace(go.Scatter(x=grp["bucket"], y=grp["roas"], name="ROAS", yaxis="y2",
                              mode="lines+markers", line=dict(color="#E0A800", width=3)))
    fig2.update_layout(barmode="stack", height=340, plot_bgcolor="white",
                       margin=dict(t=10, b=10, l=10, r=10),
                       yaxis=dict(title="광고비", gridcolor="#EEF1F5", tickformat=","),
                       yaxis2=dict(title="ROAS", overlaying="y", side="right", showgrid=False),
                       legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})

    st.markdown("#### 상세 데이터")
    grp["달성율"] = grp.apply(lambda r: (r["actual"] / r["goal"] * 100) if r["goal"] else 0, axis=1)
    disp = grp.rename(columns={"bucket": "기간", "goal": "목표", "actual": "실제",
                               "adsum": "광고비", "roas": "ROAS"})[
        ["기간", "목표", "실제", "달성율", "광고비", "ROAS"]]
    st.dataframe(
        disp.style.format({"목표": "₩{:,.0f}", "실제": "₩{:,.0f}", "달성율": "{:.0f}%",
                           "광고비": "₩{:,.0f}", "ROAS": "{:.2f}"}),
        hide_index=True, use_container_width=True)
