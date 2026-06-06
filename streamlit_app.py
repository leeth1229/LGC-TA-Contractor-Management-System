import json
import os
import re
import shutil
from io import BytesIO
from pathlib import Path
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from datetime import datetime, date
from PIL import Image, UnidentifiedImageError

st.set_page_config(page_title="TA 협력사 관리", layout="wide")

FACTORIES = ["NCC", "OXO", "IPA2", "IPA3", "3AA", "BDBTX", "BPA", "HDPE", "NPG", "PC"]

import streamlit as st

try:
    _streamlit_secrets = dict(st.secrets)
except Exception:
    _streamlit_secrets = {}

LG_CREDENTIALS = _streamlit_secrets.get("LG_CREDENTIALS", {})
ADMIN_PASSWORD = _streamlit_secrets.get("ADMIN_PASSWORD", "") or LG_CREDENTIALS.get("ADMIN_PASSWORD", "")

# If ADMIN_PASSWORD was accidentally placed in the LG_CREDENTIALS section,
# keep it usable while preserving the actual login credentials.
if isinstance(LG_CREDENTIALS, dict) and "ADMIN_PASSWORD" in LG_CREDENTIALS:
    LG_CREDENTIALS = {k: v for k, v in LG_CREDENTIALS.items() if k != "ADMIN_PASSWORD"}

CONTRACTORS = [
    {"name": "대아", "factory": "NCC", "order": "NCC Cracking H/T 보수"},
    {"name": "동남로", "factory": "OXO", "order": "DC-101 내화물 보수"},
    {"name": "퍼펙트", "factory": "IPA2", "order": "GB-120 O/H"},
    {"name": "퍼펙트", "factory": "IPA3", "order": "GB-3120 O/H"},
    {"name": "대신", "factory": "HDPE", "order": "PE 배관 보수"},
]

DATA_DIR = Path("data")
DAILY_IMAGE_DIR = DATA_DIR / "daily_images"
ANNOUNCEMENT_IMAGE_DIR = DATA_DIR / "announcement_images"
EVALUATION_IMAGE_DIR = DATA_DIR / "evaluation_images"
SETTINGS_FILE = DATA_DIR / "settings.json"
MAX_IMAGE_DIMENSION = 1200
TARGET_IMAGE_SIZE = 2 * 1024 * 1024
MIN_JPEG_QUALITY = 60
QUALITY_STEP = 5

SAMPLE_REPORTS = []

SAMPLE_ANNOUNCEMENTS = []

SAMPLE_EVALUATIONS = []

PAGE_MENU = ["대시보드", "공지사항", "일일 보고", "평가/마일리지"]
SETTINGS_PAGE = "설정"
EQUIPMENT_CATEGORIES = ["없음", "카고크레인", "크레인", "지게차"]


def sanitize_text(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in (" ", "-", "_")).strip()


def format_minutes_to_time(minutes) -> str:
    if minutes is None or pd.isna(minutes):
        return "-"
    total = int(round(float(minutes)))
    hour = total // 60
    minute = total % 60
    return f"{hour:02d}:{minute:02d}"


def parse_equipment_entries(equipment_str: str, default_tons: int = 0) -> list[dict]:
    items = []
    if not equipment_str or str(equipment_str).strip() == "":
        return items
    for part in str(equipment_str).split(","):
        part = part.strip()
        if not part or part == "없음":
            continue
        name = part
        tons = default_tons
        if "+" in part:
            name_part, ton_part = part.split("+", 1)
            name = name_part.strip()
            ton_match = re.search(r"(\d+)", ton_part)
            if ton_match:
                tons = int(ton_match.group(1))
        items.append({"equipment": name, "tons": tons})
    return items


DAILY_REPORTS_FILE = DATA_DIR / "daily_reports.csv"
ANNOUNCEMENTS_FILE = DATA_DIR / "announcements.csv"
EVALUATIONS_FILE = DATA_DIR / "evaluations.csv"


def _jpeg_bytes(img: Image.Image, quality: int) -> bytes:
    output = BytesIO()
    img.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def _png_bytes(img: Image.Image) -> bytes:
    output = BytesIO()
    img.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _png_to_jpeg(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img.convert("RGBA"), mask=img.convert("RGBA").split()[-1])
        return background
    return img.convert("RGB")


def resize_image_bytes(file_bytes: bytes, max_dimension: int = MAX_IMAGE_DIMENSION) -> bytes:
    try:
        with Image.open(BytesIO(file_bytes)) as img:
            img_format = img.format or "PNG"
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

            if img_format.upper() == "JPEG":
                data = _jpeg_bytes(img, 85)
            else:
                data = _png_bytes(img)

            if len(data) <= TARGET_IMAGE_SIZE:
                return data

            if img_format.upper() == "JPEG":
                quality = 85
                while len(data) > TARGET_IMAGE_SIZE and quality >= MIN_JPEG_QUALITY:
                    quality -= QUALITY_STEP
                    data = _jpeg_bytes(img, quality)
                if len(data) <= TARGET_IMAGE_SIZE:
                    return data

            if img_format.upper() == "PNG":
                try:
                    quantized = img.convert("P", palette=Image.ADAPTIVE, colors=128)
                    data = _png_bytes(quantized)
                    if len(data) <= TARGET_IMAGE_SIZE:
                        return data
                except Exception:
                    pass

                try:
                    jpeg_img = _png_to_jpeg(img)
                    quality = 85
                    data = _jpeg_bytes(jpeg_img, quality)
                    while len(data) > TARGET_IMAGE_SIZE and quality >= MIN_JPEG_QUALITY:
                        quality -= QUALITY_STEP
                        data = _jpeg_bytes(jpeg_img, quality)
                    if len(data) <= TARGET_IMAGE_SIZE:
                        return data
                except Exception:
                    pass

            downscale_factor = 0.9
            current = img
            while len(data) > TARGET_IMAGE_SIZE and min(current.width, current.height) > 300:
                new_size = (max(300, int(current.width * downscale_factor)), max(300, int(current.height * downscale_factor)))
                current = current.resize(new_size, Image.LANCZOS)
                if img_format.upper() == "JPEG":
                    data = _jpeg_bytes(current, max(MIN_JPEG_QUALITY, quality))
                else:
                    try:
                        data = _jpeg_bytes(_png_to_jpeg(current), max(MIN_JPEG_QUALITY, quality))
                    except Exception:
                        data = _png_bytes(current)
                if len(data) <= TARGET_IMAGE_SIZE:
                    return data

            return data
    except UnidentifiedImageError:
        return file_bytes
    except Exception:
        return file_bytes


def save_uploaded_file(uploaded_file, target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = sanitize_text(uploaded_file.name)
    destination = target_dir / filename
    uploaded_file.seek(0)
    raw_bytes = uploaded_file.read()
    optimized_bytes = resize_image_bytes(raw_bytes)
    with destination.open("wb") as f:
        f.write(optimized_bytes)
    return destination.name


def load_daily_reports() -> pd.DataFrame:
    if DAILY_REPORTS_FILE.exists():
        df = pd.read_csv(DAILY_REPORTS_FILE, dtype=str).fillna("")
        for col in ["personnel", "tons", "progress"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        return df
    return pd.DataFrame(SAMPLE_REPORTS)


def load_announcements() -> pd.DataFrame:
    if ANNOUNCEMENTS_FILE.exists():
        return pd.read_csv(ANNOUNCEMENTS_FILE, dtype=str).fillna("")
    return pd.DataFrame(columns=["date", "title", "content", "images"])


def load_evaluations() -> pd.DataFrame:
    if EVALUATIONS_FILE.exists():
        df = pd.read_csv(EVALUATIONS_FILE, dtype=str).fillna("")
        for col in ["warning_count", "mileage"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        return df
    return pd.DataFrame(columns=["date", "factory", "company", "type", "reason", "warning_count", "mileage", "images"])


def save_daily_reports():
    st.session_state.daily_reports.to_csv(DAILY_REPORTS_FILE, index=False)


def save_announcements():
    st.session_state.announcements.to_csv(ANNOUNCEMENTS_FILE, index=False)


def save_evaluations():
    st.session_state.evaluations.to_csv(EVALUATIONS_FILE, index=False)


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r", encoding="utf-8") as f:
                settings = json.load(f)
            if isinstance(settings, dict):
                return settings
        except Exception:
            pass
    return {"dday": datetime.today().strftime("%Y-%m-%d")}


def save_settings(settings: dict):
    try:
        with SETTINGS_FILE.open("w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_empty_daily_reports() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date", "factory", "company", "task", "work_order", "personnel",
        "entry_status", "equipment", "tons", "progress", "notes", "images",
        "start_time", "end_time"
    ])


def get_empty_announcements() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "title", "content", "images"])


def get_empty_evaluations() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date", "factory", "company", "type", "reason",
        "warning_count", "mileage", "images"
    ])


def reset_system_data():
    empty_daily = get_empty_daily_reports()
    empty_announcements = get_empty_announcements()
    empty_evaluations = get_empty_evaluations()

    empty_daily.to_csv(DAILY_REPORTS_FILE, index=False)
    empty_announcements.to_csv(ANNOUNCEMENTS_FILE, index=False)
    empty_evaluations.to_csv(EVALUATIONS_FILE, index=False)

    for image_dir in [DAILY_IMAGE_DIR, ANNOUNCEMENT_IMAGE_DIR, EVALUATION_IMAGE_DIR]:
        if image_dir.exists():
            for child in image_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()

    st.session_state.daily_reports = empty_daily
    st.session_state.announcements = empty_announcements
    st.session_state.evaluations = empty_evaluations
    st.session_state.uploaded_images = {}


def get_company_tasks(company: str) -> list[str]:
    return [c["order"] for c in CONTRACTORS if c["name"] == company]


# def get_latest_progress_average(df: pd.DataFrame) -> float:
#     if df.empty:
#         return 0.0
#     latest_date = df["date"].dt.date.max()
#     latest_reports = df[df["date"].dt.date == latest_date]
#     latest_reports = latest_reports.drop_duplicates(subset=["task"], keep="last")
#     avg = latest_reports["progress"].mean() if not latest_reports.empty else 0.0
#     return float(round(avg, 1))
def get_latest_progress_average(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0
    
    # 1. 안전하게 날짜 정렬 (과거 -> 최신 순)
    df_sorted = df.sort_values(by="date", ascending=True)
    
    # 2. 각 작업(task)별로 가장 마지막(최신)에 등록된 행만 남기고 이전 내역은 제거
    # 이렇게 하면 6/6일 자 HDPE(46%)와 6/3일 자 NCC(22%)가 각각 1줄씩 완벽히 추출됩니다.
    latest_per_task = df_sorted.drop_duplicates(subset=["task"], keep="last")
    
    # 3. 추출된 최신 진척도들의 평균 계산 (46과 22의 평균 = 34.0%)
    if not latest_per_task.empty:
        avg = latest_per_task["progress"].mean()
    else:
        avg = 0.0
        
    return float(round(avg, 1))

def get_report_image_paths(row) -> list[str]:
    image_names = [n.strip() for n in str(row.get("images", "")).split(",") if n.strip()]
    if not image_names:
        return []
    date_value = row.get("date", "")
    if isinstance(date_value, (pd.Timestamp, datetime)):
        date_value = date_value.strftime("%Y-%m-%d")
    else:
        date_value = str(date_value)
    base_dir = DAILY_IMAGE_DIR / date_value / sanitize_text(row["company"])
    return [str(base_dir / name) for name in image_names if (base_dir / name).exists()]


def initialize_state():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    ANNOUNCEMENT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    EVALUATION_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.user = ""
        st.session_state.login_type = ""
        st.session_state.logged_factory = FACTORIES[0]
    if "daily_reports" not in st.session_state:
        st.session_state.daily_reports = load_daily_reports()
    if "announcements" not in st.session_state:
        st.session_state.announcements = load_announcements()
    if "evaluations" not in st.session_state:
        st.session_state.evaluations = load_evaluations()
    if "settings" not in st.session_state:
        st.session_state.settings = load_settings()
    if "uploaded_images" not in st.session_state:
        st.session_state.uploaded_images = {}
    if "settings_access_granted" not in st.session_state:
        st.session_state.settings_access_granted = False


def logout():
    st.session_state.logged_in = False
    st.session_state.user = ""
    st.session_state.login_type = ""
    st.session_state.settings_access_granted = False


def login_page():
    st.title("TA 대정비 협력사 관리 로그인")
    st.write("협력사 또는 LG.C 사용자로 로그인하여 공장별 작업 현황을 관리하세요.")

    login_type = st.radio("로그인 유형", ["협력사", "LG.C"])
    st.session_state.login_type = login_type

    if login_type == "협력사":
        companies = sorted({c["name"] for c in CONTRACTORS})
        company = st.selectbox("협력사 선택", companies)
        factories_for_company = sorted({c["factory"] for c in CONTRACTORS if c["name"] == company})
        factory = st.selectbox("작업 공장", factories_for_company)
        if st.button("로그인"):
            st.session_state.logged_in = True
            st.session_state.user = company
            st.session_state.logged_factory = factory
            st.rerun()
    else:
        username = st.text_input("LG.C 사용자명", value="admin1")
        password = st.text_input("비밀번호", type="password")
        if st.button("로그인"):
            if username in LG_CREDENTIALS and password == LG_CREDENTIALS[username]:
                st.session_state.logged_in = True
                st.session_state.user = username
                st.session_state.logged_factory = FACTORIES[0]
                st.success("LG.C 사용자로 로그인되었습니다.")
                st.rerun()
            else:
                st.error("LG.C 사용자명 또는 비밀번호가 올바르지 않습니다.")


def get_factory_reports(factory=None):
    df = st.session_state.daily_reports.copy()
    if factory and factory != "전체":
        df = df[df["factory"] == factory]
    return df


def update_date_axis(fig, min_date, max_date):
    if min_date == max_date:
        fig.update_xaxes(tickformat="%m/%d", range=[min_date, max_date])
    else:
        fig.update_xaxes(
            tickformat="%m/%d",
            range=[min_date, max_date],
            tickmode="array",
            tickvals=[min_date, max_date],
            ticktext=[min_date.strftime("%m/%d"), max_date.strftime("%m/%d")],
        )


def show_dashboard():
    st.title("대시보드")
    st.write("공장별 일일 작업 현황과 진척도, 중장비 사용 현황을 확인합니다.")

    factory_filter = st.selectbox("공장 선택", ["전체"] + FACTORIES, index=0)
    reports = get_factory_reports(factory_filter)

    if not reports.empty:
        reports = reports.copy()
        reports["date"] = pd.to_datetime(reports["date"])
    today_date = datetime.today().date()
    dday_str = st.session_state.settings.get("dday", today_date.strftime("%Y-%m-%d"))
    try:
        dday_date = datetime.strptime(dday_str, "%Y-%m-%d").date()
    except Exception:
        dday_date = today_date
    if dday_date > today_date:
        dday_date = today_date

    date_range = st.date_input("기간 선택", [dday_date, today_date], key="dashboard_date_range")
    if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
        st.warning("기간을 두 개 날짜 이상 선택해 주세요.")
        return

    start_date, end_date = date_range
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    if not reports.empty:
        reports = reports[(reports["date"].dt.date >= start_date) & (reports["date"].dt.date <= end_date)]

    total_personnel = int(reports["personnel"].sum()) if not reports.empty else 0
    equipment_count = 0
    avg_start = "-"
    avg_end = "-"
    if not reports.empty:
        equipment_df = reports[["date", "equipment"]].copy()
        equipment_df["equipment"] = equipment_df["equipment"].str.split(", ")
        equipment_df = equipment_df.explode("equipment")
        equipment_df["equipment"] = equipment_df["equipment"].str.strip()
        equipment_count = equipment_df[~equipment_df["equipment"].isin(["없음", ""])].shape[0]

        time_report = reports.copy()
        time_report["start_time_parsed"] = pd.to_datetime(time_report["start_time"], format="%H:%M", errors="coerce")
        time_report["end_time_parsed"] = pd.to_datetime(time_report["end_time"], format="%H:%M", errors="coerce")
        time_report["start_minutes"] = time_report["start_time_parsed"].dt.hour * 60 + time_report["start_time_parsed"].dt.minute
        time_report["end_minutes"] = time_report["end_time_parsed"].dt.hour * 60 + time_report["end_time_parsed"].dt.minute
        start_mean = time_report["start_minutes"].mean()
        end_mean = time_report["end_minutes"].mean()
        if not pd.isna(start_mean):
            avg_start = format_minutes_to_time(start_mean)
        if not pd.isna(end_mean):
            avg_end = format_minutes_to_time(end_mean)

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    range_label = start_date.strftime("%m/%d") if start_date == end_date else f"{start_date.strftime('%m/%d')} ~ {end_date.strftime('%m/%d')}"
    col1.metric("총 작업 건수", len(reports))
    avg_progress = get_latest_progress_average(reports)
    col2.metric("진척도", f"{avg_progress:.1f}%")
    col3.metric("등록 협력사 수", reports["company"].nunique())
    col4.metric("총 출입 인원", total_personnel)
    col5.metric("총 출입 중장비 대수", equipment_count)
    col6.metric("평균 시작/종료", f"{avg_start} / {avg_end}")

    st.subheader("공장별 일일 보고 현황")
    if reports.empty:
        st.info("등록된 작업이 없습니다. 공장 또는 기간 선택을 확인해 주세요.")
    else:
        report_display = reports.copy()
        report_display["date"] = report_display["date"].dt.strftime("%Y-%m-%d")
        report_display = report_display.drop(columns=["images", "tons"], errors="ignore").rename(columns={
            "date": "날짜",
            "factory": "공장",
            "company": "협력사",
            "task": "공사명",
            "entry_status": "작업현황",
            "work_order": "작업명",
            "personnel": "출입인원",
            "equipment": "중장비 현황",
            "progress": "진척도",
            "notes": "작업사항",
        })
        st.dataframe(report_display.reset_index(drop=True), use_container_width=True)

    if not reports.empty:
        plot_start = pd.to_datetime(start_date)
        plot_end = pd.to_datetime(end_date)

        progress_series = reports.copy()
        progress_series["task_id"] = (
            progress_series["factory"].astype(str)
            + "||"
            + progress_series["company"].astype(str)
            + "||"
            + progress_series["task"].astype(str)
        )
        progress_series = progress_series[["date", "task_id", "progress"]].sort_values(["task_id", "date"])

        last_report_date = progress_series["date"].max()
        full_dates = pd.date_range(plot_start, last_report_date, freq="D")
        task_progress_frames = []
        for task_id, task_group in progress_series.groupby("task_id"):
            task_group = task_group.set_index("date").sort_index()
            task_group = task_group[~task_group.index.duplicated(keep="last")]
            task_daily = task_group.reindex(full_dates, method="ffill")
            task_daily = task_daily.loc[task_daily.index >= task_group.index.min()]
            if not task_daily.empty:
                task_daily = task_daily.reset_index().rename(columns={"index": "date"})
                task_daily["task_id"] = task_id
                task_progress_frames.append(task_daily)

        if task_progress_frames:
            progress_trend = pd.concat(task_progress_frames, ignore_index=True)
            progress_trend = progress_trend.groupby("date")["progress"].mean().reset_index()
        else:
            progress_trend = pd.DataFrame({"date": full_dates, "progress": [0] * len(full_dates)})

        # ==========================================
        # 1. 작업 진척도 라인 차트
        # ==========================================
        fig1 = px.line(progress_trend, x="date", y="progress", markers=True, title="작업 진척도")
        fig1.update_yaxes(range=[0, 100])
        update_date_axis(fig1, plot_start, plot_end)
        
        # 💡 [모바일 최적화] 터치 드래그 및 마우스 확대/축소/이동 방지 설정
        fig1.update_layout(
            dragmode=False,                  # 드래그 영역 선택 확대 방지
            xaxis=dict(fixedrange=True),     # X축 확대 고정 (터치 스크롤 허용)
            yaxis=dict(fixedrange=True),     # Y축 확대 고정
            margin=dict(l=20, r=20, t=40, b=40) # 모바일 여백 최적화
        )
        # 💡 [출력 설정] 차트 상단 툴바 메뉴 제거 및 터치 줌 방지
        st.plotly_chart(fig1, use_container_width=True, config={'scrollZoom': False, 'displayModeBar': False})


        # ==========================================
        # 2. 작업인원 라인 차트
        # ==========================================
        personnel_trend = reports.groupby(reports["date"].dt.date)["personnel"].sum().reset_index()
        personnel_trend["date"] = pd.to_datetime(personnel_trend["date"])
        fig_personnel = px.line(personnel_trend, x="date", y="personnel", title="작업인원")
        
        if not personnel_trend.empty:
            peak_row = personnel_trend.loc[personnel_trend["personnel"].idxmax()]
            peak_date = peak_row["date"]
            peak_value = int(peak_row["personnel"])
            fig_personnel.add_trace(
                go.Scatter(
                    x=[peak_date],
                    y=[peak_value],
                    mode="markers+text",
                    marker=dict(color="red", size=12),
                    text=[f"피크 {peak_value}"],
                    textposition="top center",
                    showlegend=False,
                )
            )
        update_date_axis(fig_personnel, plot_start, plot_end)
        
        # 💡 [모바일 최적화] 터치 드래그 및 마우스 확대/축소/이동 방지 설정
        fig_personnel.update_layout(
            dragmode=False,
            xaxis=dict(fixedrange=True),
            yaxis=dict(fixedrange=True),
            margin=dict(l=20, r=20, t=40, b=40)
        )
        st.plotly_chart(fig_personnel, use_container_width=True, config={'scrollZoom': False, 'displayModeBar': False})


        # ==========================================
        # 3. 중장비 스택 바 차트
        # ==========================================
        equipment_rows = []
        for _, row in reports[["date", "equipment"]].iterrows():
            parsed = parse_equipment_entries(row["equipment"], default_tons=row.get("tons", 0))
            for item in parsed:
                equipment_rows.append({
                    "date": row["date"],
                    "equipment": item["equipment"],
                    "tons": item["tons"],
                })
        equipment_df = pd.DataFrame(equipment_rows)
        
        if not equipment_df.empty:
            equipment_df["equipment"] = equipment_df["equipment"].str.strip()
            equipment_df = equipment_df[~equipment_df["equipment"].isin(["없음", ""])]
            equipment_df["combo"] = equipment_df["equipment"] + "+" + equipment_df["tons"].astype(str) + "ton"
            equipment_trend = equipment_df.groupby([equipment_df["date"].dt.date, "combo"]).size().reset_index(name="count")
            equipment_trend["date"] = pd.to_datetime(equipment_trend["date"])
            
            if not equipment_trend.empty:
                fig2 = px.bar(
                    equipment_trend,
                    x="date",
                    y="count",
                    color="combo",
                    title="중장비+톤 일별 스택 바 차트",
                    labels={"combo": "중장비+톤", "count": "건수", "date": "날짜"},
                )
                
                # 기존의 여러 개로 흩어져 있던 update_layout 설정을 하나로 결합하고 줌 고정 속성을 주입했습니다.
                update_date_axis(fig2, plot_start, plot_end)
                fig2.update_layout(
                    barmode="stack",
                    xaxis_title="date",
                    yaxis_title="건수",
                    yaxis_autorange=True,
                    dragmode=False,               # 드래그 줌 금지
                    xaxis=dict(fixedrange=True),  # X축 고정
                    yaxis=dict(fixedrange=True),  # Y축 고정
                    margin=dict(l=20, r=20, t=40, b=40),
                    legend=dict(                  # 모바일 화면 대응을 위해 범례 위치 조절
                        orientation="h",          # 가로 정렬
                        yanchor="bottom",
                        y=-0.3,                   # 차트 아래쪽으로 배치
                        xanchor="center",
                        x=0.5
                    )
                )
                st.plotly_chart(fig2, use_container_width=True, config={'scrollZoom': False, 'displayModeBar': False})
        else:
            equipment_df = pd.DataFrame(columns=["date", "equipment", "tons", "combo"])

    st.subheader("공장별 작업 요약")
    if not reports.empty:
        metric_options = {
            "진척도": "진척도",
            "출입 인원": "인원합계",
            "이동식 크레인": "크레인건수",
        }
        selected_metric = st.selectbox("그래프로 볼 항목", list(metric_options.keys()), index=0)

        if selected_metric == "진척도":
            time_series = reports.groupby([reports["date"].dt.date, "factory"]).agg({"progress": "mean"}).reset_index()
            time_series = time_series.rename(columns={"progress": "진척도"})
        elif selected_metric == "총 출입 인원":
            time_series = reports.groupby([reports["date"].dt.date, "factory"]).agg({"personnel": "sum"}).reset_index()
            time_series = time_series.rename(columns={"personnel": "인원합계"})
        else:
            equipment_rows = []
            for _, row in reports[["date", "factory", "equipment"]].iterrows():
                parsed = parse_equipment_entries(row["equipment"], default_tons=row.get("tons", 0))
                for item in parsed:
                    equipment_rows.append({
                        "date": row["date"],
                        "factory": row["factory"],
                        "equipment": item["equipment"],
                    })
            equipment_summary = pd.DataFrame(equipment_rows)
            if not equipment_summary.empty:
                equipment_summary["equipment"] = equipment_summary["equipment"].str.strip()
                crane_summary = equipment_summary[equipment_summary["equipment"] == "크레인"]
                time_series = crane_summary.groupby([crane_summary["date"].dt.date, "factory"]).size().reset_index(name="크레인건수")
            else:
                time_series = pd.DataFrame(columns=["date", "factory", "크레인건수"])

        # --------------------------------------------------
        # [그래프 1] 공장별 일별 트렌드 차트
        # --------------------------------------------------
        if not time_series.empty:
            time_series["date"] = pd.to_datetime(time_series["date"])
            fig_summary = px.line(
                time_series,
                x="date",
                y=metric_options[selected_metric],
                color="factory",
                markers=True,
                title=f"공장별 일별 {selected_metric}"
            )
            
            # [수정] 여러 줄로 흩어진 요소를 결합하고 모바일 줌 잠금 주입
            update_date_axis(fig_summary, start_date, end_date)
            fig_summary.update_layout(
                xaxis_title="date",
                yaxis_title=selected_metric,
                hovermode="x unified",
                dragmode=False,                  # 드래그 줌 금지
                xaxis=dict(fixedrange=True),     # X축 스케일 고정 (웹 브라우저 스크롤 허용)
                yaxis=dict(fixedrange=True),     # Y축 스케일 고정
                margin=dict(t=50, l=20, r=20, b=40),
                legend=dict(                     # 💡 범례가 우측을 가리지 않도록 차트 하단 가로 배치
                    orientation="h",
                    yanchor="bottom",
                    y=-0.3,
                    xanchor="center",
                    x=0.5,
                    title_text=""                # 모바일 화면 확보를 위해 '공장' 타이틀 생략
                )
            )
            st.plotly_chart(fig_summary, use_container_width=True, config={'scrollZoom': False, 'displayModeBar': False})
        else:
            st.info("선택한 기간에 해당하는 데이터를 찾을 수 없습니다.")

        st.subheader("공장별 작업 시작 시간 정규 분포")
        start_time_series = reports.copy()
        start_time_series["start_time_parsed"] = pd.to_datetime(start_time_series["start_time"], format="%H:%M", errors="coerce")
        start_time_series = start_time_series[start_time_series["start_time_parsed"].notna()]
        start_time_series["start_hour"] = start_time_series["start_time_parsed"].dt.hour + start_time_series["start_time_parsed"].dt.minute / 60
        start_time_series = start_time_series[(start_time_series["start_hour"] >= 7) & (start_time_series["start_hour"] <= 11)]

        # --------------------------------------------------
        # [그래프 2] 시작 시간 정규 분포 곡선
        # --------------------------------------------------
        if not start_time_series.empty:
            x_values = np.linspace(6, 12, 121)
            dist_fig = go.Figure()
            for factory, group in start_time_series.groupby("factory"):
                hours = group["start_hour"].astype(float).to_numpy()
                if len(hours) == 0:
                    continue
                mu = float(np.mean(hours))
                sigma = float(np.std(hours, ddof=1)) if len(hours) > 1 else 0.5
                if sigma <= 0:
                    sigma = 0.5
                normal_y = (1 / (sigma * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x_values - mu) / sigma) ** 2) * 10
                mu_minutes = int(round(mu * 60))
                mu_hour = mu_minutes // 60
                mu_min = mu_minutes % 60
                sigma_minutes = sigma * 60
                dist_fig.add_trace(
                    go.Scatter(
                        x=x_values,
                        y=normal_y,
                        mode="lines",
                        name=f"{factory} (μ={mu_hour:02d}:{mu_min:02d}, σ={sigma_minutes:.1f}분)",
                        line=dict(width=2)
                    )
                )
                
            # [수정] 모바일 드래그 프리징 현상 방지 패치 및 하단 스택 정렬
            dist_fig.update_layout(
                title="공장별 작업 시작 시간 정규 분포 (07-11시)",
                xaxis_title="작업 시작 시간",
                yaxis_title="비율 (%)",
                xaxis=dict(range=[6, 12], dtick=1, fixedrange=True), # 줌 잠금
                yaxis=dict(fixedrange=True),                          # 줌 잠금
                dragmode=False,
                template="plotly_white",
                margin=dict(t=50, l=20, r=20, b=40),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=-0.4,                                           # 통계 텍스트가 기므로 여백을 조금 더 하단으로 배치
                    xanchor="center",
                    x=0.5,
                    title_text=""
                )
            )
            st.plotly_chart(dist_fig, use_container_width=True, config={'scrollZoom': False, 'displayModeBar': False})
        else:
            st.info("07시~11시 구간의 시작 시간 데이터가 부족하여 분포를 생성할 수 없습니다.")

        factory_summary = reports.groupby("factory").agg(
            진척도=("progress", "mean"),
            인원합계=("personnel", "sum")
        ).reset_index()
    else:
        st.write("선택한 기간에 해당하는 보고 데이터가 없습니다.")


def show_daily_report():
    st.title("일일 보고 등록")
    st.write("오늘 작업 내용을 입력하고 오전/추가 보고를 함께 관리하세요.")

    if st.session_state.login_type == "LG.C":
        st.info("LG.C 관리자는 일일 보고 등록 및 수정 기능을 사용할 수 없습니다.")
        if st.session_state.daily_reports.empty:
            st.write("등록된 일일 보고가 없습니다.")
            return

        reports = st.session_state.daily_reports.copy()
        reports["date"] = pd.to_datetime(reports["date"], errors="coerce")
        companies = sorted(reports["company"].dropna().unique())
        selected_company = st.selectbox("협력사 선택", ["전체"] + companies, index=0)
        if selected_company != "전체":
            reports = reports[reports["company"] == selected_company]

        tasks = sorted(reports["task"].dropna().unique())
        selected_task = st.selectbox("공사명 선택", ["전체"] + tasks, index=0)
        if selected_task != "전체":
            reports = reports[reports["task"] == selected_task]

        if reports.empty:
            st.write("선택한 조건에 해당하는 일일 보고가 없습니다.")
            return

        total_reports = len(reports)
        total_personnel = int(reports["personnel"].astype(int).sum()) if not reports.empty else 0
        latest_progress = get_latest_progress_average(reports)
        equipment_rows = []
        for _, row in reports.iterrows():
            equipment_rows.extend(parse_equipment_entries(row["equipment"], default_tons=row.get("tons", 0)))
        equipment_count = len(equipment_rows)

        time_report = reports.copy()
        time_report["start_time_parsed"] = pd.to_datetime(time_report["start_time"], format="%H:%M", errors="coerce")
        time_report["end_time_parsed"] = pd.to_datetime(time_report["end_time"], format="%H:%M", errors="coerce")
        start_avg = "-"
        end_avg = "-"
        if time_report["start_time_parsed"].notna().any():
            start_minutes = time_report.loc[time_report["start_time_parsed"].notna(), "start_time_parsed"].dt.hour * 60 + time_report.loc[time_report["start_time_parsed"].notna(), "start_time_parsed"].dt.minute
            avg_start = start_minutes.mean()
            if not pd.isna(avg_start):
                avg_start = float(avg_start)
                avg_start_hour = int(avg_start // 60)
                avg_start_min = int(round(avg_start % 60))
                if avg_start_min == 60:
                    avg_start_hour += 1
                    avg_start_min = 0
                start_avg = f"{avg_start_hour:02d}:{avg_start_min:02d}"
        if time_report["end_time_parsed"].notna().any():
            end_minutes = time_report.loc[time_report["end_time_parsed"].notna(), "end_time_parsed"].dt.hour * 60 + time_report.loc[time_report["end_time_parsed"].notna(), "end_time_parsed"].dt.minute
            avg_end = end_minutes.mean()
            if not pd.isna(avg_end):
                avg_end = float(avg_end)
                avg_end_hour = int(avg_end // 60)
                avg_end_min = int(round(avg_end % 60))
                if avg_end_min == 60:
                    avg_end_hour += 1
                    avg_end_min = 0
                end_avg = f"{avg_end_hour:02d}:{avg_end_min:02d}"

        st.subheader("업체별 현황")
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("보고 건수", total_reports)
        c2.metric("최근 진척도", f"{latest_progress:.1f}%")
        c3.metric("총 출입 인원", total_personnel)
        c4.metric("중장비 건수", equipment_count)
        c5.metric("평균 시작시간", start_avg)
        c6.metric("평균 종료시간", end_avg)

        report_display = reports.copy()
        report_display["date"] = report_display["date"].dt.strftime("%Y-%m-%d")
        report_display = report_display.drop(columns=["images", "tons"], errors="ignore").rename(columns={
            "date": "날짜",
            "factory": "공장",
            "company": "협력사",
            "task": "공사명",
            "entry_status": "작업현황",
            "work_order": "작업명",
            "personnel": "출입인원",
            "equipment": "중장비 현황",
            "start_time": "시작시간",
            "end_time": "종료시간",
            "progress": "진척도",
            "notes": "작업사항",
        })
        st.dataframe(report_display.reset_index(drop=True), use_container_width=True)

        st.subheader("작업 사진")
        for _, row in reports.iterrows():
            image_paths = get_report_image_paths(row)
            if image_paths:
                with st.expander(f"{row['date'].strftime('%Y-%m-%d')} | {row['company']} / {row['task']} - 이미지 보기"):
                    st.image(image_paths, use_column_width=True)
        return

    default_construction = "현장 점검 및 보수"
    if st.session_state.login_type == "협력사":
        default_company = st.session_state.user
        default_factory = st.session_state.logged_factory
        company_tasks = get_company_tasks(default_company)
        if company_tasks:
            default_construction = company_tasks[0]

    if "daily_equipment_rows" not in st.session_state:
        st.session_state.daily_equipment_rows = 1
    if "daily_equipment_data" not in st.session_state:
        st.session_state.daily_equipment_data = [{"equipment": "없음", "tons": 0}]

    with st.form(key="daily_report_morning_form"):
        st.subheader("기본 등록")
        report_date = st.date_input("작업일", value=datetime.today())
        if st.session_state.login_type == "협력사":
            factory = st.selectbox("공장", [default_factory], disabled=True)
            company = st.selectbox("협력사", [default_company], disabled=True)
            construction_name = st.text_input("공사명", value=default_construction, disabled=True)
            project_name = st.text_input("작업명", value="작업명 입력")
        else:
            factory = st.selectbox("공장", FACTORIES, index=0)
            company = st.selectbox("협력사", sorted({c["name"] for c in CONTRACTORS if c["factory"] == factory}))
            construction_name = st.text_input("공사명", value="공사명 입력")
            project_name = st.text_input("작업명", value="작업명 입력")

        personnel = st.number_input("출입인원", min_value=1, max_value=100, value=5)
        entry_status = st.selectbox("작업현황", ["정상작업", "우천취소", "기타"], index=0)

        st.markdown("**중장비 입력**")
        button_col1, button_col2 = st.columns([1, 1])
        add_equipment = button_col1.form_submit_button("중장비 추가", key="add_equipment_row")
        reset_equipment = button_col2.form_submit_button("중장비 초기화", key="reset_equipment_rows")

        if add_equipment:
            if st.session_state.daily_equipment_rows < 5:
                st.session_state.daily_equipment_rows += 1
                st.session_state.daily_equipment_data.append({"equipment": "없음", "tons": 0})
            else:
                st.warning("중장비는 최대 5개까지 추가할 수 있습니다.")

        if reset_equipment:
            st.session_state.daily_equipment_rows = 1
            st.session_state.daily_equipment_data = [{"equipment": "없음", "tons": 0}]

        if len(st.session_state.daily_equipment_data) != st.session_state.daily_equipment_rows:
            st.session_state.daily_equipment_data = st.session_state.daily_equipment_data[:st.session_state.daily_equipment_rows]
            while len(st.session_state.daily_equipment_data) < st.session_state.daily_equipment_rows:
                st.session_state.daily_equipment_data.append({"equipment": "없음", "tons": 0})

        # st.write("중장비 목록")
        header_col1, header_col2 = st.columns([2, 1])
        header_col1.markdown("**중장비**")
        header_col2.markdown("**톤수**")

        for idx in range(st.session_state.daily_equipment_rows):
            current = st.session_state.daily_equipment_data[idx]
            eq_col1, eq_col2 = st.columns([2, 1])
            equipment_type = eq_col1.selectbox(
                f"중장비 {idx + 1}",
                EQUIPMENT_CATEGORIES,
                index=EQUIPMENT_CATEGORIES.index(current.get("equipment", "없음")) if current.get("equipment", "없음") in EQUIPMENT_CATEGORIES else 0,
                key=f"equipment_type_{idx}",
            )
            equipment_ton = eq_col2.number_input(
                f"톤수 {idx + 1}",
                min_value=0,
                max_value=1500,
                value=int(current.get("tons", 0)),
                key=f"equipment_tons_{idx}",
            )
            st.session_state.daily_equipment_data[idx] = {"equipment": equipment_type, "tons": int(equipment_ton)}

        morning_submit = st.form_submit_button("기본 등록")
        if morning_submit:
            equipment_rows = st.session_state.daily_equipment_data
            parsed_equipment = [item for item in equipment_rows if item["equipment"] != "없음"]
            equipment_text = ", ".join(
                f"{item['equipment']}+{int(item['tons'])}톤" for item in parsed_equipment
            ) if parsed_equipment else "없음"
            total_tons = sum(int(item["tons"]) for item in parsed_equipment)
            new_row = {
                "date": report_date.strftime("%Y-%m-%d"),
                "factory": factory,
                "company": company,
                "task": construction_name,
                "work_order": project_name,
                "personnel": int(personnel),
                "entry_status": entry_status,
                "equipment": equipment_text,
                "tons": int(total_tons),
                "start_time": "",
                "end_time": "",
                "progress": 0,
                "notes": "",
                "images": "",
            }
            st.session_state.daily_reports = pd.concat([st.session_state.daily_reports, pd.DataFrame([new_row])], ignore_index=True)
            save_daily_reports()
            st.success("아침 기본 보고가 등록되었습니다.")

    st.markdown("---")
    # st.subheader("수정 정보 등록")

    # filtered_reports = st.session_state.daily_reports
    # if st.session_state.login_type == "협력사":
    #     filtered_reports = filtered_reports[filtered_reports["company"] == st.session_state.user]

    # if filtered_reports.empty:
    #     st.write("등록된 일일 보고가 없습니다. 먼저 아침 기본 보고를 등록해주세요.")
    # else:
    #     update_reports = filtered_reports.sort_values(by=["date", "factory"], ascending=[False, True]).reset_index().rename(columns={"index": "report_id"})
    #     update_reports["date"] = pd.to_datetime(update_reports["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    #     update_options = [
    #         f"{int(row['report_id'])} | {row['date']} / {row['company']} / {row['task']} / {row['work_order']}"
    #         for _, row in update_reports.iterrows()
    #     ]
    #     selected_report = st.selectbox("수정할 보고를 선택하세요", update_options, key="update_report_select")
    #     selected_id = int(selected_report.split(" | ")[0])
    #     report_row = filtered_reports.loc[selected_id]

    #     with st.form(key="daily_report_update_form"):
    #         st.write("기존 오전 입력 정보는 수정할 수 없으며, 추가/마감 정보를 등록합니다.")
    #         with st.expander("기존 입력 정보 보기", expanded=False):
    #             st.text_input("작업일", value=report_row["date"], disabled=True)
    #             st.text_input("공장", value=report_row["factory"], disabled=True)
    #             st.text_input("협력사", value=report_row["company"], disabled=True)
    #             st.text_input("공사명", value=report_row["task"], disabled=True)
    #             st.text_input("작업명", value=report_row["work_order"], disabled=True)
    #             st.text_input("출입인원", value=str(report_row["personnel"]), disabled=True)
    #             st.text_input("중장비", value=report_row["equipment"], disabled=True)
    #             st.text_input("톤수", value=str(report_row.get("tons", "")), disabled=True)
    #         raw_start_time = report_row.get("start_time")
    #         if pd.isna(raw_start_time) or str(raw_start_time).strip().lower() == "nan" or str(raw_start_time).strip() == "":
    #             raw_start_time = "08:00"
    #         raw_end_time = report_row.get("end_time")
    #         if pd.isna(raw_end_time) or str(raw_end_time).strip().lower() == "nan" or str(raw_end_time).strip() == "":
    #             raw_end_time = "17:00"
    #         start_time_value = datetime.strptime(str(raw_start_time), "%H:%M").time()
    #         end_time_value = datetime.strptime(str(raw_end_time), "%H:%M").time()
    #         start_time = st.time_input("작업 시작시간", value=start_time_value, step=300)
    #         end_time = st.time_input("작업 종료시간", value=end_time_value, step=300)
    #         progress = st.slider("작업 진척도", min_value=0, max_value=100, value=int(report_row.get("progress") or 0), step=1)
    #         notes = st.text_area("금일 작업 사항", value=str(report_row.get("notes", "")), height=120)
    #         uploaded_files = st.file_uploader("작업 사진 업로드", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
    #         update_submit = st.form_submit_button("수정 저장")

    #         if update_submit:
    #             if uploaded_files:
    #                 image_names = []
    #                 save_dir = DAILY_IMAGE_DIR / report_row["date"] / sanitize_text(report_row["company"])
    #                 for file in uploaded_files:
    #                     saved_name = save_uploaded_file(file, save_dir)
    #                     image_names.append(saved_name)
    #                 existing_images = [n.strip() for n in str(report_row.get("images", "")).split(",") if n.strip()]
    #                 all_images = existing_images + image_names
    #                 st.session_state.daily_reports.at[selected_id, "images"] = ", ".join(all_images)
    #                 st.session_state.daily_reports.at[selected_id, "start_time"] = start_time.strftime("%H:%M")
    #                 st.session_state.daily_reports.at[selected_id, "end_time"] = end_time.strftime("%H:%M")
    #                 st.session_state.daily_reports.at[selected_id, "progress"] = int(progress)
    #                 st.session_state.daily_reports.at[selected_id, "notes"] = notes
    #                 save_daily_reports()
    #                 st.success("일일 보고 추가/수정 정보가 저장되었습니다.")
    #                 st.rerun()

    #     st.markdown("---")
    #     st.subheader("최근 등록된 일일 보고")
    #     display_reports = update_reports.copy()
    #     display_reports["date"] = pd.to_datetime(display_reports["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    #     display_reports = display_reports.drop(columns=["images", "tons"], errors="ignore").rename(columns={
    #         "date": "날짜",
    #         "factory": "공장",
    #         "company": "협력사",
    #         "task": "공사명",
    #         "work_order": "작업명",
    #         "personnel": "출입인원",
    #         "entry_status": "작업현황",
    #         "equipment": "중장비 현황",
    #         "start_time": "시작시간",
    #         "end_time": "종료시간",
    #         "progress": "진척도",
    #         "notes": "작업사항",
    #     })
    #     st.dataframe(display_reports.reset_index(drop=True), use_container_width=True)

    #     st.subheader("사진 보기")
    #     for _, row in update_reports.iterrows():
    #         image_paths = get_report_image_paths(row)
    #         if image_paths:
    #             with st.expander(f"{row['date']} / {row['company']} / {row['task']} - 이미지 보기", expanded=False):
    #                 st.write(f"총 {len(image_paths)}장")
    #                 cols = st.columns(min(len(image_paths), 4))
    #                 for idx, image_path in enumerate(image_paths):
    #                     col = cols[idx % len(cols)]
    #                     try:
    #                         col.image(image_path, use_column_width=True)
    #                     except Exception:
    #                         col.write(f"이미지를 로드할 수 없습니다: {image_path}")

    #     st.markdown("---")
    #     st.subheader("잘못 등록한 보고 삭제")
    #     delete_options = [
    #         f"{int(row['report_id'])} | {row['날짜']} / {row['협력사']} / {row['작업명']} / {row['공장']}"
    #         for _, row in display_reports.iterrows()
    #     ]
    #     selected_delete = st.selectbox("삭제할 보고를 선택하세요", delete_options, key="delete_report_select")
    #     selected_id = int(selected_delete.split(" | ")[0])
    #     if st.button("선택한 보고 삭제"):
    #         st.session_state.daily_reports = st.session_state.daily_reports.drop(index=selected_id).reset_index(drop=True)
    #         save_daily_reports()
    #         st.success("선택한 일일 보고가 삭제되었습니다. 필요하면 다시 등록하세요.")
    
    st.subheader("수정 정보 등록")
    filtered_reports = st.session_state.daily_reports
    if st.session_state.login_type == "협력사":
        filtered_reports = filtered_reports[filtered_reports["company"] == st.session_state.user]

    if filtered_reports.empty:
        st.write("등록된 일일 보고가 없습니다. 먼저 아침 기본 보고를 등록해주세요.")
    else:
        # ⚠️ 중요: Loc 접근 시 정적 인덱스를 안전하게 매핑하기 위해 reset_index()를 사용하되 본래 index를 유지합니다.
        update_reports = filtered_reports.copy()
        update_reports["report_id"] = update_reports.index
        update_reports = update_reports.sort_values(by=["date", "factory"], ascending=[False, True])
        update_reports["date"] = pd.to_datetime(update_reports["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        
        update_options = [
            f"{int(row['report_id'])} | {row['date']} / {row['company']} / {row['task']} / {row['work_order']}"
            for _, row in update_reports.iterrows()
        ]
        selected_report = st.selectbox("수정할 보고를 선택하세요", update_options, key="update_report_select")
        selected_id = int(selected_report.split(" | ")[0])
        report_row = filtered_reports.loc[selected_id]
        st.write("👆 **상단에서 수정할 작업 일보를 먼저 선택해 주세요.** 자동으로 해당 데이터가 불러와지며 추가 및 마감 정보를 편리하게 등록하실 수 있습니다.")
        with st.form(key="daily_report_update_form"):
            
            # with st.expander("기존 입력 정보 보기", expanded=False):
            #     st.text_input("작업일", value=report_row["date"], disabled=True)
            #     st.text_input("공장", value=report_row["factory"], disabled=True)
            #     st.text_input("협력사", value=report_row["company"], disabled=True)
            #     st.text_input("공사명", value=report_row["task"], disabled=True)
            #     st.text_input("작업명", value=report_row["work_order"], disabled=True)
            #     st.text_input("출입인원", value=str(report_row["personnel"]), disabled=True)
            #     st.text_input("중장비", value=report_row["equipment"], disabled=True)
            #     st.text_input("톤수", value=str(report_row.get("tons", "")), disabled=True)
                
            raw_start_time = report_row.get("start_time")
            if pd.isna(raw_start_time) or str(raw_start_time).strip().lower() == "nan" or str(raw_start_time).strip() == "":
                raw_start_time = "08:00"
            raw_end_time = report_row.get("end_time")
            if pd.isna(raw_end_time) or str(raw_end_time).strip().lower() == "nan" or str(raw_end_time).strip() == "":
                raw_end_time = "17:00"
                
            start_time_value = datetime.strptime(str(raw_start_time), "%H:%M").time()
            end_time_value = datetime.strptime(str(raw_end_time), "%H:%M").time()
            start_time = st.time_input("작업 시작시간", value=start_time_value, step=300)
            end_time = st.time_input("작업 종료시간", value=end_time_value, step=300)
            st.write("💡 **작업 시작/종료를 입력해 주세요.** 입력하신 시간을 바탕으로 아침 작업 발행 순서를 조율이 가능합니다.")
            progress = st.slider("**작업 진척도**", min_value=0, max_value=100, value=int(report_row.get("progress") or 0), step=1)
            st.write("📊**진척도 반영**: 오늘 작업이 완료된 만큼 슬라이더를 움직여 주세요.")
            notes = st.text_area("금일 작업 사항", value=str(report_row.get("notes", "")), height=120, placeholder=(
            "- NCC Cracking 배관 용접 및 비파괴 검사 완료\n"
            "- GB-120 내부 클리닝 및 O/H 가스켓 교체\n"
            "- 현장 정리정돈 및 안전 점검 이상 없음"
            ))
            uploaded_files = st.file_uploader("작업 사진 업로드 (선택사항)", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
            
            update_submit = st.form_submit_button("수정 저장")

            if update_submit:
                # 1. 일반 텍스트 데이터 업데이트
                st.session_state.daily_reports.at[selected_id, "start_time"] = start_time.strftime("%H:%M")
                st.session_state.daily_reports.at[selected_id, "end_time"] = end_time.strftime("%H:%M")
                st.session_state.daily_reports.at[selected_id, "progress"] = int(progress)
                st.session_state.daily_reports.at[selected_id, "notes"] = notes
                
                # 2. 이미지 처리 (새로 올릴 때만 기존 물리 파일 삭제)
                if uploaded_files:
                    save_dir = DAILY_IMAGE_DIR / report_row["date"] / sanitize_text(report_row["company"])
                    
                    # [추가] 덮어쓰기 전 기존 물리 파일 삭제 로직
                    old_images_raw = str(report_row.get("images", "")).split(",")
                    for old_img in old_images_raw:
                        old_img_name = old_img.strip()
                        if old_img_name:
                            old_file_path = save_dir / old_img_name
                            if old_file_path.exists():
                                try:
                                    old_file_path.unlink()  # 실제 파일 삭제
                                except Exception:
                                    pass # 파일이 이미 없거나 권한 에러 시 무시
                    
                    # 새 이미지 리사이즈 및 저장
                    image_names = []
                    for file in uploaded_files:
                        saved_name = save_uploaded_file(file, save_dir)
                        image_names.append(saved_name)
                    
                    # 새 목록으로 완전 대체
                    st.session_state.daily_reports.at[selected_id, "images"] = ", ".join(image_names)
                
                # 3. CSV 저장 후 즉시 리런
                save_daily_reports()
                st.success("일일 보고 추가/수정 정보가 저장되었습니다.")
                st.rerun()

        st.markdown("---")
        st.subheader("최근 등록된 일일 보고")
        display_reports = update_reports.copy()
        display_reports["date"] = pd.to_datetime(display_reports["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        
        # 데이터프레임 매핑 시 복사본을 정제하여 화면 출력 오류 방지
        display_reports = display_reports.drop(columns=["images", "tons", "report_id"], errors="ignore").rename(columns={
            "date": "날짜",
            "factory": "공장",
            "company": "협력사",
            "task": "공사명",
            "work_order": "작업명",
            "personnel": "출입인원",
            "entry_status": "작업현황",
            "equipment": "중장비 현황",
            "start_time": "시작시간",
            "end_time": "종료시간",
            "progress": "진척도",
            "notes": "작업사항",
        })
        st.dataframe(display_reports.reset_index(drop=True), use_container_width=True)

        st.subheader("사진 보기")
        for _, row in update_reports.iterrows():
            image_paths = get_report_image_paths(row)
            if image_paths:
                with st.expander(f"{row['date']} / {row['company']} / {row['task']} - 이미지 보기", expanded=False):
                    st.write(f"총 {len(image_paths)}장")
                    cols = st.columns(min(len(image_paths), 4))
                    for idx, image_path in enumerate(image_paths):
                        col = cols[idx % len(cols)]
                        try:
                            col.image(image_path, use_column_width=True)
                        except Exception:
                            col.write(f"이미지를 로드할 수 없습니다: {image_path}")

        st.markdown("---")
        st.subheader("잘못 등록한 보고 삭제")
        delete_options = [
            f"{int(row['report_id'])} | {row['date']} / {row['company']} / {row['work_order']} / {row['factory']}"
            for _, row in update_reports.iterrows()
        ]
        selected_delete = st.selectbox("삭제할 보고를 선택하세요", delete_options, key="delete_report_select")
        selected_id = int(selected_delete.split(" | ")[0])
        
        if st.button("선택한 보고 삭제"):
            # 삭제 대상 행 데이터 추출
            target_row = st.session_state.daily_reports.loc[selected_id]
            
            # [추가] 삭제 대상 보고에 연결된 실제 물리 이미지 파일 전부 삭제
            if "images" in target_row and str(target_row["images"]).strip():
                del_dir = DAILY_IMAGE_DIR / target_row["date"] / sanitize_text(target_row["company"])
                del_images_raw = str(target_row["images"]).split(",")
                for del_img in del_images_raw:
                    del_img_name = del_img.strip()
                    if del_img_name:
                        del_file_path = del_dir / del_img_name
                        if del_file_path.exists():
                            try:
                                del_file_path.unlink()  # 실제 파일 제거
                            except Exception:
                                pass
                                
            # 데이터프레임에서 행 제외 후 인덱스 재정렬
            st.session_state.daily_reports = st.session_state.daily_reports.drop(index=selected_id).reset_index(drop=True)
            save_daily_reports()
            st.success("선택한 일일 보고와 저장된 이미지 파일이 완전히 삭제되었습니다.")
            st.rerun()

def show_announcements():
    st.title("공지사항")
    st.write("공지사항을 작성하고 전체 공장 및 협력사에 전달할 수 있습니다.")

    if st.session_state.login_type == "협력사":
        st.info("협력사는 공지사항을 등록할 수 없습니다. 공지사항 목록만 확인 가능합니다.")
    else:
        with st.form(key="announcement_form"):
            ann_date = st.date_input("공지일", value=datetime.today())
            ann_title = st.text_input("제목")
            ann_content = st.text_area("내용", height=140)
            ann_images = st.file_uploader("공지 이미지 업로드 (선택사항)", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
            ann_submit = st.form_submit_button("등록")

            if ann_submit:
                if not ann_title or not ann_content:
                    st.error("제목과 내용을 모두 입력해주세요.")
                else:
                    image_names = []
                    if ann_images:
                        save_dir = ANNOUNCEMENT_IMAGE_DIR / ann_date.strftime("%Y-%m-%d") / sanitize_text(ann_title)
                        for file in ann_images:
                            saved_name = save_uploaded_file(file, save_dir)
                            image_names.append(saved_name)
                        
                    new_ann = {
                        "date": ann_date.strftime("%Y-%m-%d"),
                        "title": ann_title,
                        "content": ann_content,
                        "images": ", ".join(image_names),
                    }
                    st.session_state.announcements = pd.concat([st.session_state.announcements, pd.DataFrame([new_ann])], ignore_index=True)
                    save_announcements()
                    st.success("공지사항이 등록되었습니다.")
                    st.rerun()  # 💡 [추가] 등록 즉시 공지 목록 갱신 리런

    st.markdown("---")
    st.subheader("공지 목록")
    
    # ⚠️ 원본 인덱스가 흐트러져 엉뚱한 기업의 파일이 지워지는 사고를 완벽히 차단합니다.
    display_ann = st.session_state.announcements.copy()
    display_ann["ann_id"] = display_ann.index
    display_ann = display_ann.sort_values(by="date", ascending=False)
    
    for _, row in display_ann.iterrows():
        st.write(f"**{row['date']} - {row['title']}**")
        st.write(row["content"])
        if row.get("images"):
            image_names = [n.strip() for n in str(row["images"]).split(",") if n.strip()]
            ann_dir = ANNOUNCEMENT_IMAGE_DIR / row["date"] / sanitize_text(row["title"])
            image_paths = [str(ann_dir / name) for name in image_names if (ann_dir / name).exists()]
            if image_paths:
                st.image(image_paths, width=250)
        st.write("---")

    if st.session_state.login_type != "협력사" and not display_ann.empty:
        st.subheader("잘못 등록된 공지 삭제")
        
        delete_options = [
            f"{int(row['ann_id'])} | {row['date']} / {row['title']}" 
            for _, row in display_ann.iterrows()
        ]
        selected_delete = st.selectbox("삭제할 공지를 선택하세요", delete_options, key="delete_announcement_select")
        selected_id = int(selected_delete.split(" | ")[0])
        
        if st.button("선택한 공지 삭제"):
            # 1. 원본 데이터프레임에서 삭제 대상 데이터 추출
            target_row = st.session_state.announcements.loc[selected_id]
            
            # 2. 💡 [추가] 해당 공지에 첨부된 실제 서버 디렉토리 내의 물리 파일 추적 삭제
            if "images" in target_row and str(target_row["images"]).strip():
                del_dir = ANNOUNCEMENT_IMAGE_DIR / target_row["date"] / sanitize_text(target_row["title"])
                del_images_raw = str(target_row["images"]).split(",")
                for del_img in del_images_raw:
                    del_img_name = del_img.strip()
                    if del_img_name:
                        del_file_path = del_dir / del_img_name
                        if del_file_path.exists():
                            try:
                                del_file_path.unlink()  # 하드디스크 내부 파일 완전 제거
                            except Exception:
                                pass # 에러로 인한 시스템 중단 방지
                
                # 3. 💡 [추가 기능] 공지용 전용 하위 폴더가 완전히 비어있다면 폴더까지 깔끔하게 자동 청소
                try:
                    if del_dir.exists() and not any(del_dir.iterdir()):
                        del_dir.rmdir()
                except Exception:
                    pass

            # 4. 데이터프레임 행 제거 및 영구 저장용 파일 업데이트
            st.session_state.announcements = st.session_state.announcements.drop(index=selected_id).reset_index(drop=True)
            save_announcements()
            st.success("선택한 공지 데이터 및 첨부 이미지 파일이 시스템에서 완전히 삭제되었습니다.")
            st.rerun()  # 💡 [추가] 삭제 즉시 목록 갱신 리런


def show_evaluation():
    st.title("평가 / 마일리지")
    st.write("작업 업체에 대한 우수 평가, 경고, 마일리지 관리를 할 수 있습니다.")

    if st.session_state.login_type == "협력사":
        st.info("💡 **협력사 안내 사항**\n\n* 협력사 계정은 평가 및 마일리지를 직접 등록할 수 없습니다.\n* **안전 유의**: 경고가 누적 3회 이상 발생 시, 현장 안전 조치(Unsafety) 및 출입 제한 등이 발생할 수 있으므로 각별히 인지하시기 바랍니다.")
    else:
        eval_type = st.radio(
            "구분",
            ["칭찬", "경고"],
            horizontal=True,
            key="eval_type",
        )
        with st.form(key="evaluation_form"):
            eval_date = st.date_input("평가일", value=datetime.today())
            eval_company = st.selectbox("협력사", sorted({c["name"] for c in CONTRACTORS}))
            eval_factory = next((c["factory"] for c in CONTRACTORS if c["name"] == eval_company), FACTORIES[0])
            eval_reason = st.text_area(
                "마일리지 / 경고, 사유",
                height=120,
                placeholder="위반 항목 또는 마일리지 점수 부여 사유와 현장 영향성 등을 구체적으로 작성해 주세요.",
            )
            if eval_type == "경고":
                warning_count = 1
                mileage = 0
            else:
                warning_count = 0
                mileage = st.number_input(
                    "마일리지",
                    min_value=0,
                    max_value=100,
                    value=0,
                    key="eval_mileage",
                )
            eval_images = st.file_uploader("평가 이미지 업로드 (선택사항)", type=["png", "jpg", "jpeg"], accept_multiple_files=True)
            eval_submit = st.form_submit_button("저장")

            if eval_submit:
                if not eval_reason:
                    st.error("사유를 입력해주세요.")
                else:
                    image_names = []
                    if eval_images:
                        save_dir = EVALUATION_IMAGE_DIR / eval_date.strftime("%Y-%m-%d") / sanitize_text(eval_company)
                        for file in eval_images:
                            saved_name = save_uploaded_file(file, save_dir)
                            image_names.append(saved_name)
                        
                    new_eval = {
                        "date": eval_date.strftime("%Y-%m-%d"),
                        "factory": eval_factory,
                        "company": eval_company,
                        "type": eval_type,
                        "reason": eval_reason,
                        "warning_count": int(warning_count),
                        "mileage": int(mileage),
                        "images": ", ".join(image_names),
                    }
                    st.session_state.evaluations = pd.concat([st.session_state.evaluations, pd.DataFrame([new_eval])], ignore_index=True)
                    save_evaluations()
                    st.success("평가가 저장되었습니다.")
                    st.rerun()  # 💡 [추가] 저장 즉시 대시보드 리프레시 반영

    st.markdown("---")
    st.subheader("협력사별 누적 평가 / 마일리지")
    eval_df = st.session_state.evaluations.copy()
    if not eval_df.empty:
        # 안전한 집계를 위해 사본의 인덱스를 보존하고 정제 진행
        eval_df["eval_id"] = eval_df.index
        company_summary = eval_df.groupby("company").agg(
            경고누계=("warning_count", "sum"),
            마일리지누계=("mileage", "sum")
        ).reset_index()
        st.dataframe(company_summary.sort_values(by="마일리지누계", ascending=False), use_container_width=True)

    st.markdown("---")
    st.subheader("최근 평가 / 경고 현황")
    
    # ⚠️ 인덱스가 꼬여 엉뚱한 파일이 지워지는 문제를 완벽 차단하기 위해 원본 고유 index 유지
    display_eval = st.session_state.evaluations.copy()
    display_eval["eval_id"] = display_eval.index
    display_eval = display_eval.sort_values(by="date", ascending=False)
    
    for _, row in display_eval.iterrows():
        row_date = row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], pd.Timestamp) else str(row["date"])
        image_names = [n.strip() for n in str(row.get("images", "")).split(",") if n.strip()]
        eval_dir = EVALUATION_IMAGE_DIR / row_date / sanitize_text(row["company"])
        image_paths = [str(eval_dir / name) for name in image_names if (eval_dir / name).exists()]

        st.write(f"**{row_date} - {row['company']} / {row['type']}**")
        st.write(row["reason"])
        if image_paths:
            st.image(image_paths, width=250)
        st.write("---")

    if not display_eval.empty and st.session_state.login_type == "LG.C":
        st.markdown("---")
        st.subheader("잘못 등록된 평가 삭제")
        delete_options = []
        for _, row in display_eval.iterrows():
            row_date = row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], pd.Timestamp) else str(row["date"])
            delete_options.append(f"{int(row['eval_id'])} | {row_date} / {row['company']} / {row['type']}")

        selected_delete = st.selectbox("삭제할 평가를 선택하세요", delete_options, key="delete_evaluation_select")
        selected_id = int(selected_delete.split(" | ")[0])
        
        if st.button("선택한 평가 삭제"):
            # 1. 삭제할 원본 대상 행 추출
            target_row = st.session_state.evaluations.loc[selected_id]
            target_date = target_row["date"].strftime("%Y-%m-%d") if isinstance(target_row["date"], pd.Timestamp) else str(target_row["date"])
            
            # 2. 💡 [추가] 해당 평가건에 등록되었던 서버 내 실제 물리 이미지 파일 자동 완전 추적 삭제
            if "images" in target_row and str(target_row["images"]).strip():
                del_dir = EVALUATION_IMAGE_DIR / target_date / sanitize_text(target_row["company"])
                del_images_raw = str(target_row["images"]).split(",")
                for del_img in del_images_raw:
                    del_img_name = del_img.strip()
                    if del_img_name:
                        del_file_path = del_dir / del_img_name
                        if del_file_path.exists():
                            try:
                                del_file_path.unlink()  # 하드디스크에서 파일 삭제
                            except Exception:
                                pass # 열려있는 파일 등 예외 발생 시 크래시 방지
            
            # 3. 데이터프레임 행 삭제 및 영구 보관용 CSV 갱신
            st.session_state.evaluations = st.session_state.evaluations.drop(index=selected_id).reset_index(drop=True)
            save_evaluations()
            st.success("선택한 평가 데이터 및 실제 이미지 파일이 디스크에서 완전히 삭제되었습니다.")
            st.rerun()  # 💡 [추가] 즉시 대시보드 리프레시 반영

def show_settings():
    st.title("관리자 설정")
    st.write("LG.C 관리자 전용: 저장된 시스템 데이터를 초기화하고 대시보드 기본 시작일을 설정할 수 있습니다.")

    st.subheader("대시보드 시작일(D-day)")
    current_dday = st.session_state.settings.get("dday", datetime.today().strftime("%Y-%m-%d"))
    try:
        dday_default = datetime.strptime(current_dday, "%Y-%m-%d").date()
    except Exception:
        dday_default = datetime.today().date()

    dday_input = st.date_input("D-day", value=dday_default, key="settings_dday")
    if st.button("D-day 저장", key="save_dday"):
        st.session_state.settings["dday"] = dday_input.strftime("%Y-%m-%d")
        save_settings(st.session_state.settings)
        st.success("대시보드 시작일이 저장되었습니다.")

    st.markdown("---")
    st.write("세션 데이터를 새로고침하면 저장된 CSV와 설정을 다시 로드합니다. 데이터 초기화는 수행되지 않습니다.")
    if st.button("세션 새로고침", key="refresh_session"):
        st.session_state.daily_reports = load_daily_reports()
        st.session_state.announcements = load_announcements()
        st.session_state.evaluations = load_evaluations()
        st.session_state.settings = load_settings()
        st.session_state.uploaded_images = {}
        st.success("세션 상태가 새로고침되었습니다.")

    st.markdown("---")
    st.write("초기화 대상: 일일 보고, 공지사항, 평가/마일리지, 업로드된 이미지 파일")
    st.warning("데이터 초기화는 되돌릴 수 없습니다. 신중하게 사용하세요.")
    if st.button("전체 데이터 초기화", key="reset_system_data"):
        reset_system_data()
        st.success("시스템 데이터가 초기화되었습니다.")


def main():
    initialize_state()
    st.sidebar.title("TA 협력사 관리")
    st.sidebar.write("로그인을 통해 공장별 작업 현황을 확인하세요.")

    if "logged_in" in st.session_state and st.session_state.logged_in:
        st.sidebar.markdown(f"**접속자:** {st.session_state.user}")
        st.sidebar.markdown(f"**로그인 유형:** {st.session_state.login_type}")
        st.sidebar.button("로그아웃", on_click=logout)

        page_options = PAGE_MENU.copy()
        if st.session_state.login_type == "협력사":
            page_options = [page for page in page_options if page != "대시보드"]
        elif st.session_state.login_type == "LG.C":
            page_options.append(SETTINGS_PAGE)

        page = st.sidebar.selectbox("메뉴", page_options)

        if page != SETTINGS_PAGE:
            st.session_state.settings_access_granted = False

        if page == "대시보드":
            show_dashboard()
        elif page == "공지사항":
            show_announcements()
        elif page == "일일 보고":
            show_daily_report()
        elif page == "평가/마일리지":
            show_evaluation()
        elif page == SETTINGS_PAGE:
            if not st.session_state.get("settings_access_granted", False):
                with st.form(key="settings_auth_form"):
                    auth_password = st.text_input("추가 관리자 비밀번호 입력", type="password")
                    auth_submit = st.form_submit_button("인증")
                    if auth_submit:
                        if auth_password and ADMIN_PASSWORD and auth_password == ADMIN_PASSWORD:
                            st.session_state.settings_access_granted = True
                            st.success("관리자 설정 접근이 승인되었습니다.")
                            st.rerun()
                        else:
                            st.error("비밀번호가 올바르지 않습니다.")
            else:
                show_settings()
    else:
        login_page()


if __name__ == "__main__":
    main()
