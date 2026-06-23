import base64
import requests
from pathlib import Path
import json
import os
import re
import shutil
from io import BytesIO
import uuid
from pathlib import Path
from datetime import datetime, date

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

try:
    from streamlit_image_coordinates import streamlit_image_coordinates
except Exception:
    streamlit_image_coordinates = None


st.set_page_config(page_title="TA 협력사 관리", layout="wide")


FACTORIES = ["NCC", "OXO", "IPA2", "IPA3", "3AA", "BDBTX", "BPA", "HDPE", "NPG", "PC"]

try:
    _streamlit_secrets = dict(st.secrets)
except Exception:
    _streamlit_secrets = {}

LG_CREDENTIALS = _streamlit_secrets.get("LG_CREDENTIALS", {})
ADMIN_PASSWORD = _streamlit_secrets.get("ADMIN_PASSWORD", "") or LG_CREDENTIALS.get("ADMIN_PASSWORD", "")

if isinstance(LG_CREDENTIALS, dict) and "ADMIN_PASSWORD" in LG_CREDENTIALS:
    LG_CREDENTIALS = {k: v for k, v in LG_CREDENTIALS.items() if k != "ADMIN_PASSWORD"}


CONTRACTORS = [
    {"name": "GS네오텍", "factory": "3AA", "order": "배관/장치 작업"},
    {"name": "진흥플랜트", "factory": "3AA", "order": "비계/보온 작업"},
    {"name": "케이에스컴프레셔", "factory": "3AA", "order": "회전기계 O/H"},
    {"name": "산단이앤지", "factory": "3AA", "order": "Column Cleaning"},
    {"name": "지구환경", "factory": "3AA", "order": "EA-131 외 Vessel Vacuum Cleaning"}
]


DATA_DIR = Path("data")
DAILY_IMAGE_DIR = DATA_DIR / "daily_images"
ANNOUNCEMENT_IMAGE_DIR = DATA_DIR / "announcement_images"
EVALUATION_IMAGE_DIR = DATA_DIR / "evaluation_images"
FACTORY_MAP_DIR = DATA_DIR / "factory_maps"

DAILY_REPORTS_FILE = DATA_DIR / "daily_reports.csv"
ANNOUNCEMENTS_FILE = DATA_DIR / "announcements.csv"
EVALUATIONS_FILE = DATA_DIR / "evaluations.csv"
EQUIPMENT_LOCATIONS_FILE = DATA_DIR / "equipment_locations.csv"
SETTINGS_FILE = DATA_DIR / "settings.json"

MAX_IMAGE_DIMENSION = 1200
TARGET_IMAGE_SIZE = 2 * 1024 * 1024
MIN_JPEG_QUALITY = 60
QUALITY_STEP = 5
MAP_DISPLAY_WIDTH = 700

SAMPLE_REPORTS = []
SAMPLE_ANNOUNCEMENTS = []
SAMPLE_EVALUATIONS = []

PAGE_MENU = ["대시보드", "일일 보고", "공지사항", "평가/마일리지"]
SETTINGS_PAGE = "설정"
EQUIPMENT_CATEGORIES = ["없음", "Cargo", "Crane", "Fork_lift"]


# =============================================================================
# 공통 유틸
# =============================================================================

def sanitize_text(value: str) -> str:
    return "".join(c for c in str(value) if c.isalnum() or c in (" ", "-", "_")).strip()


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


def get_default_equipment_item() -> dict:
    return {
        "equipment": "없음",
        "tons": 0,
        "x_pct": None,
        "y_pct": None,
        "map_file": "",
        "location_key_version": 0,
    }

def reset_daily_equipment_inputs():
    """
    기본 등록 과정에서 중장비 입력값을 완전히 초기화합니다.
    - 중장비 행 수 1개로 초기화
    - 중장비 종류 '없음'
    - ton 0
    - 위치 좌표 초기화
    - Streamlit 위젯 key 초기화
    """
    st.session_state.daily_equipment_rows = 1
    st.session_state.daily_equipment_data = [get_default_equipment_item()]

    keys_to_delete = []

    for key in list(st.session_state.keys()):
        key_str = str(key)

        if (
            key_str.startswith("equipment_type_")
            or key_str.startswith("equipment_tons_")
            or key_str.startswith("equipment_location_map_")
            or key_str.startswith("clear_equipment_location_")
        ):
            keys_to_delete.append(key)

    for key in keys_to_delete:
        try:
            del st.session_state[key]
        except Exception:
            pass

def safe_json_loads(value, default=None):
    if default is None:
        default = []
    try:
        if value is None or str(value).strip() == "":
            return default
        loaded = json.loads(value)
        return loaded if isinstance(loaded, list) else default
    except Exception:
        return default


def get_company_tasks(company: str) -> list[str]:
    return [c["order"] for c in CONTRACTORS if c["name"] == company]


# =============================================================================
# 이미지 / 도면 처리
# =============================================================================

def get_factory_map_path(factory: str) -> Path | None:
    if not factory:
        return None

    for ext in ["png", "jpg", "jpeg"]:
        candidate = FACTORY_MAP_DIR / f"{factory}.{ext}"
        if candidate.exists():
            return candidate

    return None


def resize_map_for_display(img: Image.Image, display_width: int = MAP_DISPLAY_WIDTH) -> Image.Image:
    img = img.convert("RGB")
    if img.width <= display_width:
        return img.copy()

    ratio = display_width / img.width
    display_height = int(img.height * ratio)
    return img.resize((display_width, display_height), Image.LANCZOS)

def get_korean_font(size: int = 12):
    # PIL 이미지 위에 한글을 깨지지 않게 표시하기 위한 폰트 로더입니다.
    # Codespaces / Ubuntu / 로컬 환경에서 사용 가능한 한글 폰트를 우선 탐색합니다.

    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",

        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.otf",

        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",

        "fonts/NotoSansKR-Regular.otf",
        "fonts/NotoSansKR-Bold.otf",
        "fonts/NanumGothic.ttf",
        "data/fonts/NotoSansKR-Regular.otf",
        "data/fonts/NanumGothic.ttf",
    ]

    for font_path in font_candidates:
        try:
            path = Path(font_path)
            if path.exists():
                return ImageFont.truetype(str(path), size)
        except Exception:
            pass

    search_dirs = [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path("fonts"),
        Path("data/fonts"),
    ]

    font_keywords = [
        "NotoSansCJK",
        "NotoSansKR",
        "Nanum",
        "UnDotum",
        "UnGungseo",
        "Malgun",
        "AppleGothic",
    ]

    for search_dir in search_dirs:
        if not search_dir.exists():
            continue

        for ext in ["*.ttf", "*.otf", "*.ttc"]:
            for font_file in search_dir.rglob(ext):
                try:
                    font_name = font_file.name.lower()
                    if any(keyword.lower() in font_name for keyword in font_keywords):
                        return ImageFont.truetype(str(font_file), size)
                except Exception:
                    pass

    return ImageFont.load_default()

def draw_location_marker(
    img: Image.Image,
    x_pct,
    y_pct,
    label: str = "장비 위치",
    color: str = "red",
) -> Image.Image:
    display_img = img.copy()

    if x_pct is None or y_pct is None or pd.isna(x_pct) or pd.isna(y_pct):
        return display_img

    try:
        x = int(display_img.width * float(x_pct))
        y = int(display_img.height * float(y_pct))
    except Exception:
        return display_img

    draw = ImageDraw.Draw(display_img)
    font = get_korean_font(12)

    radius = 8

    # 위치 점
    draw.ellipse(
        (x - radius, y - radius, x + radius, y + radius),
        fill=color,
        outline="white",
        width=3,
    )

    # 텍스트
    text = str(label)
    text_x = x + 11
    text_y = y - 10

    # 텍스트 배경 박스
    try:
        bbox = draw.textbbox((text_x, text_y), text, font=font)
        padding = 3

        draw.rectangle(
            (
                bbox[0] - padding,
                bbox[1] - padding,
                bbox[2] + padding,
                bbox[3] + padding,
            ),
            fill="white",
            outline=color,
            width=1,
        )
    except Exception:
        pass

    # 텍스트 표시
    draw.text(
        (text_x, text_y),
        text,
        fill=color,
        font=font,
    )

    return display_img


def draw_multiple_location_markers(img: Image.Image, locations: list[dict]) -> Image.Image:
    display_img = img.copy()
    draw = ImageDraw.Draw(display_img)
    font = get_korean_font(12)

    colors = ["red", "blue", "green", "orange", "purple", "brown", "black"]

    for idx, loc in enumerate(locations):
        x_pct = loc.get("x_pct")
        y_pct = loc.get("y_pct")

        if x_pct is None or y_pct is None or pd.isna(x_pct) or pd.isna(y_pct):
            continue

        try:
            x = int(display_img.width * float(x_pct))
            y = int(display_img.height * float(y_pct))
        except Exception:
            continue

        color = colors[idx % len(colors)]
        company = str(loc.get("company", ""))
        equipment = str(loc.get("equipment", ""))
        tons = str(loc.get("tons", ""))
        seq = str(loc.get("equipment_seq", idx + 1))

        if company:
            label = f"{seq}. {company}/{equipment} {tons}ton"
        else:
            label = f"{seq}. {equipment} {tons}ton"

        radius = 7
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=color,
            outline="white",
            width=3,
        )
        text_x = x + 10
        text_y = y - 9

        try:
            bbox = draw.textbbox((text_x, text_y), label, font=font)
            padding = 3
            draw.rectangle(
                (
                    bbox[0] - padding,
                    bbox[1] - padding,
                    bbox[2] + padding,
                    bbox[3] + padding,
                ),
                fill="white",
                outline=color,
                width=1,
            )
        except Exception:
            pass

        draw.text(
            (text_x, text_y),
            label,
            fill=color,
            font=font,
        )

    return display_img


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
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
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

            quality = 85

            if img_format.upper() == "JPEG":
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
                new_size = (
                    max(300, int(current.width * downscale_factor)),
                    max(300, int(current.height * downscale_factor)),
                )
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
    if not filename:
        filename = f"image_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"

    destination = target_dir / filename

    uploaded_file.seek(0)
    raw_bytes = uploaded_file.read()
    optimized_bytes = resize_image_bytes(raw_bytes)

    with destination.open("wb") as f:
        f.write(optimized_bytes)

    return destination.name


# =============================================================================
# 빈 데이터프레임 / CSV
# =============================================================================

def get_empty_daily_reports() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "report_uid",
        "date",
        "factory",
        "company",
        "task",
        "work_order",
        "personnel",
        "entry_status",
        "equipment",
        "tons",
        "progress",
        "notes",
        "images",
        "start_time",
        "end_time",
        "equipment_locations",
    ])


def get_empty_equipment_locations() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "location_id",
        "report_uid",
        "date",
        "factory",
        "company",
        "task",
        "work_order",
        "equipment_seq",
        "equipment",
        "tons",
        "x_pct",
        "y_pct",
        "map_file",
        "created_at",
        "updated_at",
    ])


def get_empty_announcements() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "title", "content", "images"])


def get_empty_evaluations() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date",
        "factory",
        "company",
        "type",
        "reason",
        "warning_count",
        "mileage",
        "images",
    ])


def read_csv_safely(file_path: Path, empty_df_func):
    try:
        if not file_path.exists():
            empty_df = empty_df_func()
            empty_df.to_csv(file_path, index=False)
            return empty_df

        if file_path.stat().st_size == 0:
            empty_df = empty_df_func()
            empty_df.to_csv(file_path, index=False)
            return empty_df

        return pd.read_csv(file_path, dtype=str).fillna("")

    except pd.errors.EmptyDataError:
        empty_df = empty_df_func()
        empty_df.to_csv(file_path, index=False)
        return empty_df

    except Exception:
        return empty_df_func()


def load_daily_reports() -> pd.DataFrame:
    df = read_csv_safely(DAILY_REPORTS_FILE, get_empty_daily_reports)

    for col in get_empty_daily_reports().columns:
        if col not in df.columns:
            df[col] = ""

    missing_uid_mask = df["report_uid"].astype(str).str.strip() == ""
    if missing_uid_mask.any():
        df.loc[missing_uid_mask, "report_uid"] = [
            str(uuid.uuid4()) for _ in range(int(missing_uid_mask.sum()))
        ]

    for col in ["personnel", "tons", "progress"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


def load_equipment_locations() -> pd.DataFrame:
    df = read_csv_safely(EQUIPMENT_LOCATIONS_FILE, get_empty_equipment_locations)

    for col in get_empty_equipment_locations().columns:
        if col not in df.columns:
            df[col] = ""

    for col in ["equipment_seq", "tons"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    for col in ["x_pct", "y_pct"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def load_announcements() -> pd.DataFrame:
    df = read_csv_safely(ANNOUNCEMENTS_FILE, get_empty_announcements)

    for col in get_empty_announcements().columns:
        if col not in df.columns:
            df[col] = ""

    return df


def load_evaluations() -> pd.DataFrame:
    df = read_csv_safely(EVALUATIONS_FILE, get_empty_evaluations)

    for col in get_empty_evaluations().columns:
        if col not in df.columns:
            df[col] = ""

    for col in ["warning_count", "mileage"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    return df


def save_daily_reports():
    st.session_state.daily_reports.to_csv(DAILY_REPORTS_FILE, index=False)


def save_equipment_locations():
    st.session_state.equipment_locations.to_csv(EQUIPMENT_LOCATIONS_FILE, index=False)


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
# =============================================================================
# GitHub 수동 Push / 백업
# =============================================================================

def get_github_config():
    gh = st.secrets.get("GITHUB", {})
    return {
        "token": gh.get("TOKEN", ""),
        "owner": gh.get("OWNER", ""),
        "repo": gh.get("REPO", ""),
        "branch": gh.get("BRANCH", "main"),
        "data_path": gh.get("DATA_PATH", "data").strip("/"),
    }


def github_enabled() -> bool:
    cfg = get_github_config()
    return all([
        cfg["token"],
        cfg["owner"],
        cfg["repo"],
        cfg["branch"],
    ])


def github_headers():
    cfg = get_github_config()
    return {
        "Authorization": f"Bearer {cfg['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_repo_path(local_path: Path) -> str:
    """
    로컬 data 폴더 기준 상대경로를 GitHub repo 경로로 변환합니다.

    예:
    data/daily_reports.csv
    -> data/daily_reports.csv

    data/daily_images/2026-06-17/대아/a.jpg
    -> data/daily_images/2026-06-17/대아/a.jpg
    """
    cfg = get_github_config()

    try:
        relative_path = local_path.relative_to(DATA_DIR)
        return f"{cfg['data_path']}/{relative_path.as_posix()}"
    except Exception:
        return f"{cfg['data_path']}/{local_path.name}"


def github_api_url(repo_path: str) -> str:
    cfg = get_github_config()
    return f"https://api.github.com/repos/{cfg['owner']}/{cfg['repo']}/contents/{repo_path}"


def github_get_sha(local_path: Path) -> str | None:
    """
    GitHub에 이미 존재하는 파일이면 sha 반환.
    없으면 None.
    """
    if not github_enabled():
        raise RuntimeError("GitHub 설정이 없습니다.")

    cfg = get_github_config()
    repo_path = github_repo_path(local_path)

    res = requests.get(
        github_api_url(repo_path),
        headers=github_headers(),
        params={"ref": cfg["branch"]},
        timeout=20,
    )

    if res.status_code == 404:
        return None

    if not res.ok:
        raise RuntimeError(f"GitHub SHA 조회 실패: {res.status_code} / {res.text}")

    return res.json().get("sha")


def upload_file_to_github(local_path: Path, commit_message: str):
    """
    로컬 파일 1개를 GitHub에 업로드합니다.
    기존 파일이면 수정 commit, 신규 파일이면 생성 commit 됩니다.
    """
    if not github_enabled():
        raise RuntimeError("GitHub 설정이 없습니다. st.secrets의 [GITHUB] 설정을 확인하세요.")

    if not local_path.exists():
        raise FileNotFoundError(f"업로드할 파일이 없습니다: {local_path}")

    if local_path.is_dir():
        return None

    cfg = get_github_config()
    repo_path = github_repo_path(local_path)

    file_bytes = local_path.read_bytes()
    encoded_content = base64.b64encode(file_bytes).decode("utf-8")

    sha = github_get_sha(local_path)

    payload = {
        "message": commit_message,
        "content": encoded_content,
        "branch": cfg["branch"],
    }

    if sha:
        payload["sha"] = sha

    res = requests.put(
        github_api_url(repo_path),
        headers=github_headers(),
        json=payload,
        timeout=60,
    )

    if res.status_code not in [200, 201]:
        raise RuntimeError(f"GitHub 업로드 실패: {res.status_code} / {res.text}")

    return res.json()


def get_data_backup_files() -> list[Path]:
    """
    data 폴더 아래 모든 파일을 GitHub Push 대상으로 수집합니다.
    CSV, JSON, 이미지, 도면 파일 등을 포함합니다.
    """
    if not DATA_DIR.exists():
        return []

    files = []

    for file_path in DATA_DIR.rglob("*"):
        if file_path.is_file():
            files.append(file_path)

    return sorted(files, key=lambda p: str(p))


def get_backup_summary(files: list[Path]) -> dict:
    """
    백업 대상 파일 요약 정보를 반환합니다.
    """
    summary = {
        "total": len(files),
        "csv_json": 0,
        "images": 0,
        "maps": 0,
        "others": 0,
        "total_size_mb": 0.0,
    }

    image_exts = {".png", ".jpg", ".jpeg"}

    total_size = 0

    for file_path in files:
        suffix = file_path.suffix.lower()

        try:
            total_size += file_path.stat().st_size
        except Exception:
            pass

        if suffix in [".csv", ".json"]:
            summary["csv_json"] += 1
        elif suffix in image_exts:
            summary["images"] += 1

            try:
                file_path.relative_to(FACTORY_MAP_DIR)
                summary["maps"] += 1
            except Exception:
                pass
        else:
            summary["others"] += 1

    summary["total_size_mb"] = round(total_size / 1024 / 1024, 2)

    return summary


def push_data_folder_to_github():
    """
    data 폴더 전체를 GitHub에 Push합니다.
    파일 1개당 GitHub commit 1개가 생성됩니다.
    """
    files = get_data_backup_files()

    uploaded = []
    failed = []

    if not files:
        return uploaded, failed

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    progress_bar = st.progress(0)
    status_text = st.empty()

    total_files = len(files)

    for idx, file_path in enumerate(files, start=1):
        repo_path = github_repo_path(file_path)

        try:
            status_text.write(f"GitHub Push 중... ({idx}/{total_files}) {repo_path}")

            upload_file_to_github(
                file_path,
                commit_message=f"TA data backup: {repo_path} / {now_str}",
            )

            uploaded.append(repo_path)

        except Exception as e:
            failed.append((repo_path, str(e)))

        progress_bar.progress(idx / total_files)

    status_text.write("GitHub Push 작업 완료")

    return uploaded, failed

def list_github_directory(repo_path: str) -> list[dict]:
    """
    GitHub repo의 특정 디렉터리 내용을 조회합니다.
    파일/폴더 목록을 반환합니다.
    """
    if not github_enabled():
        raise RuntimeError("GitHub 설정이 없습니다.")

    cfg = get_github_config()

    res = requests.get(
        github_api_url(repo_path),
        headers=github_headers(),
        params={"ref": cfg["branch"]},
        timeout=30,
    )

    if res.status_code == 404:
        return []

    if not res.ok:
        raise RuntimeError(f"GitHub 디렉터리 조회 실패: {res.status_code} / {res.text}")

    data = res.json()

    if isinstance(data, dict):
        return [data]

    if isinstance(data, list):
        return data

    return []


def list_github_files_recursive(repo_dir_path: str) -> list[dict]:
    """
    GitHub repo의 특정 폴더 아래 파일을 재귀적으로 조회합니다.
    반환값 예:
    [
        {"path": "data/daily_reports.csv", "sha": "..."},
        ...
    ]
    """
    remote_files = []

    items = list_github_directory(repo_dir_path)

    for item in items:
        item_type = item.get("type")
        item_path = item.get("path")

        if item_type == "file":
            remote_files.append({
                "path": item_path,
                "sha": item.get("sha"),
            })

        elif item_type == "dir":
            remote_files.extend(list_github_files_recursive(item_path))

    return remote_files


def delete_github_file_by_path(repo_path: str, sha: str, commit_message: str):
    """
    GitHub repo의 파일 1개를 삭제합니다.
    """
    if not github_enabled():
        raise RuntimeError("GitHub 설정이 없습니다.")

    cfg = get_github_config()

    payload = {
        "message": commit_message,
        "sha": sha,
        "branch": cfg["branch"],
    }

    res = requests.delete(
        github_api_url(repo_path),
        headers=github_headers(),
        json=payload,
        timeout=60,
    )

    if res.status_code not in [200, 201]:
        raise RuntimeError(f"GitHub 파일 삭제 실패: {res.status_code} / {res.text}")

    return res.json()


def get_local_data_repo_paths() -> set[str]:
    """
    현재 앱 내부 data 폴더의 파일 경로를 GitHub repo 경로 형태로 반환합니다.
    """
    local_files = get_data_backup_files()
    return {github_repo_path(file_path) for file_path in local_files}


def sync_data_folder_to_github_with_delete():
    """
    data 폴더를 GitHub와 완전 동기화합니다.

    1. 로컬 data 폴더 파일 생성/수정 Push
    2. GitHub data 폴더에는 있지만 로컬에는 없는 파일 삭제

    주의:
    GitHub의 data 폴더 아래 파일 중 현재 앱에 없는 파일은 삭제됩니다.
    """
    uploaded = []
    upload_failed = []
    deleted = []
    delete_failed = []

    # 1단계: 로컬 파일 생성/수정 Push
    uploaded, upload_failed = push_data_folder_to_github()

    # 2단계: GitHub에만 남은 파일 삭제
    cfg = get_github_config()
    remote_root = cfg["data_path"]

    try:
        remote_files = list_github_files_recursive(remote_root)
    except Exception as e:
        delete_failed.append((remote_root, f"GitHub 원격 파일 목록 조회 실패: {e}"))
        return uploaded, upload_failed, deleted, delete_failed

    local_repo_paths = get_local_data_repo_paths()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 보호할 경로가 있으면 여기에 추가
    protected_prefixes = [
        # 예: "data/factory_maps/"
        # 도면도 완전 동기화 대상이면 비워두세요.
    ]

    for remote_file in remote_files:
        remote_path = remote_file.get("path", "")
        remote_sha = remote_file.get("sha", "")

        if not remote_path or not remote_sha:
            continue

        # 혹시 보호 경로가 있으면 삭제 제외
        if any(remote_path.startswith(prefix) for prefix in protected_prefixes):
            continue

        if remote_path not in local_repo_paths:
            try:
                delete_github_file_by_path(
                    repo_path=remote_path,
                    sha=remote_sha,
                    commit_message=f"TA data sync delete: {remote_path} / {now_str}",
                )
                deleted.append(remote_path)

            except Exception as e:
                delete_failed.append((remote_path, str(e)))

    return uploaded, upload_failed, deleted, delete_failed


# =============================================================================
# 중장비 위치 CSV 동기화
# =============================================================================

def upsert_equipment_locations_from_report(report_row: dict):
    report_uid = str(report_row.get("report_uid", "")).strip()
    if not report_uid:
        return

    if "equipment_locations" not in st.session_state:
        st.session_state.equipment_locations = load_equipment_locations()

    loc_df = st.session_state.equipment_locations.copy()

    if not loc_df.empty and "report_uid" in loc_df.columns:
        loc_df = loc_df[loc_df["report_uid"].astype(str) != report_uid]

    locations = safe_json_loads(report_row.get("equipment_locations", ""), default=[])
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rows = []

    for idx, loc in enumerate(locations, start=1):
        new_rows.append({
            "location_id": str(uuid.uuid4()),
            "report_uid": report_uid,
            "date": str(report_row.get("date", "")),
            "factory": str(report_row.get("factory", "")),
            "company": str(report_row.get("company", "")),
            "task": str(report_row.get("task", "")),
            "work_order": str(report_row.get("work_order", "")),
            "equipment_seq": idx,
            "equipment": str(loc.get("equipment", "")),
            "tons": int(loc.get("tons", 0) or 0),
            "x_pct": loc.get("x_pct"),
            "y_pct": loc.get("y_pct"),
            "map_file": str(loc.get("map_file", "")),
            "created_at": now_str,
            "updated_at": now_str,
        })

    if new_rows:
        loc_df = pd.concat([loc_df, pd.DataFrame(new_rows)], ignore_index=True)

    st.session_state.equipment_locations = loc_df.reset_index(drop=True)
    save_equipment_locations()


def delete_equipment_locations_by_report_uid(report_uid: str):
    report_uid = str(report_uid).strip()
    if not report_uid:
        return

    if "equipment_locations" not in st.session_state:
        st.session_state.equipment_locations = load_equipment_locations()

    loc_df = st.session_state.equipment_locations.copy()

    if loc_df.empty or "report_uid" not in loc_df.columns:
        return

    loc_df = loc_df[loc_df["report_uid"].astype(str) != report_uid].reset_index(drop=True)
    st.session_state.equipment_locations = loc_df
    save_equipment_locations()


def rebuild_equipment_locations_from_daily_reports():
    rows = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if st.session_state.daily_reports.empty:
        st.session_state.equipment_locations = get_empty_equipment_locations()
        save_equipment_locations()
        return

    for _, row in st.session_state.daily_reports.iterrows():
        report_uid = str(row.get("report_uid", "")).strip()
        if not report_uid:
            continue

        locations = safe_json_loads(row.get("equipment_locations", ""), default=[])

        for idx, loc in enumerate(locations, start=1):
            rows.append({
                "location_id": str(uuid.uuid4()),
                "report_uid": report_uid,
                "date": str(row.get("date", "")),
                "factory": str(row.get("factory", "")),
                "company": str(row.get("company", "")),
                "task": str(row.get("task", "")),
                "work_order": str(row.get("work_order", "")),
                "equipment_seq": idx,
                "equipment": str(loc.get("equipment", "")),
                "tons": int(loc.get("tons", 0) or 0),
                "x_pct": loc.get("x_pct"),
                "y_pct": loc.get("y_pct"),
                "map_file": str(loc.get("map_file", "")),
                "created_at": now_str,
                "updated_at": now_str,
            })

    if rows:
        st.session_state.equipment_locations = pd.DataFrame(rows)
    else:
        st.session_state.equipment_locations = get_empty_equipment_locations()

    save_equipment_locations()


# =============================================================================
# 초기화 / 공통
# =============================================================================

def reset_system_data():
    empty_daily = get_empty_daily_reports()
    empty_locations = get_empty_equipment_locations()
    empty_announcements = get_empty_announcements()
    empty_evaluations = get_empty_evaluations()

    empty_daily.to_csv(DAILY_REPORTS_FILE, index=False)
    empty_locations.to_csv(EQUIPMENT_LOCATIONS_FILE, index=False)
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
    st.session_state.equipment_locations = empty_locations
    st.session_state.announcements = empty_announcements
    st.session_state.evaluations = empty_evaluations
    st.session_state.uploaded_images = {}


def get_latest_progress_average(df: pd.DataFrame) -> float:
    if df.empty:
        return 0.0

    df_sorted = df.sort_values(by="date", ascending=True)
    latest_per_task = df_sorted.drop_duplicates(subset=["task"], keep="last")

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
    FACTORY_MAP_DIR.mkdir(parents=True, exist_ok=True)

    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.user = ""
        st.session_state.login_type = ""
        st.session_state.logged_factory = FACTORIES[0]

    if "daily_reports" not in st.session_state:
        st.session_state.daily_reports = load_daily_reports()

    if "equipment_locations" not in st.session_state:
        st.session_state.equipment_locations = load_equipment_locations()

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

    if "daily_equipment_rows" not in st.session_state:
        st.session_state.daily_equipment_rows = 1

    if "daily_equipment_data" not in st.session_state:
        st.session_state.daily_equipment_data = [get_default_equipment_item()]


def logout():
    st.session_state.logged_in = False
    st.session_state.user = ""
    st.session_state.login_type = ""
    st.session_state.settings_access_granted = False


# =============================================================================
# 로그인
# =============================================================================

def login_page():
    st.title("TA 대정비 협력사 관리 로그인")
    st.write("협력사 또는 LG.C 사용자로 로그인하여 공장별 작업 현황을 관리하세요.")

    login_type = st.radio("로그인 유형", ["협력사", "LG.C"])
    st.session_state.login_type = login_type

    if login_type == "협력사":
        factories_for_company = sorted({c["factory"] for c in CONTRACTORS})
        factory = st.selectbox("공장", factories_for_company)

        companies = sorted({c["name"] for c in CONTRACTORS if c["factory"] == factory})
        company = st.selectbox("협력사 선택", companies)
        
        # companies = sorted({c["name"] for c in CONTRACTORS})
        # company = st.selectbox("협력사 선택", companies)

        # factories_for_company = sorted({c["factory"] for c in CONTRACTORS if c["name"] == company})
        # factory = st.selectbox("작업 공장", factories_for_company)

        if st.button("로그인"):
            st.session_state.logged_in = True
            st.session_state.logged_factory = factory
            st.session_state.user = company
            st.rerun()

    else:
        username = st.text_input("LG.C 사용자명", value="admin1")
        password = st.text_input("비밀번호", type="password")

        if st.button("로그인"):
            if username in LG_CREDENTIALS and password == LG_CREDENTIALS[username]:
                st.session_state.logged_in = True
                st.session_state.logged_factory = FACTORIES[0]
                st.session_state.user = username
                st.success("LG.C 사용자로 로그인되었습니다.")
                st.rerun()
            else:
                st.error("LG.C 사용자명 또는 비밀번호가 올바르지 않습니다.")


# =============================================================================
# 대시보드
# =============================================================================

def get_factory_reports(factory=None):
    df = st.session_state.daily_reports.copy()
    if factory and factory != "전체":
        df = df[df["factory"] == factory]
    return df


def update_date_axis(fig, min_date, max_date):
    min_date = pd.to_datetime(min_date)
    max_date = pd.to_datetime(max_date)

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


def show_dashboard_start_time_distribution(reports: pd.DataFrame):
    st.subheader("공장별 시작시간 분포")

    if reports.empty or "start_time" not in reports.columns:
        st.info("시작 시간 데이터가 없습니다.")
        return

    start_time_series = reports.copy()
    start_time_series["start_time_parsed"] = pd.to_datetime(
        start_time_series["start_time"],
        format="%H:%M",
        errors="coerce",
    )

    start_time_series = start_time_series[start_time_series["start_time_parsed"].notna()]

    if start_time_series.empty:
        st.info("시작 시간 데이터가 부족하여 분포를 생성할 수 없습니다.")
        return

    start_time_series["start_hour"] = (
        start_time_series["start_time_parsed"].dt.hour
        + start_time_series["start_time_parsed"].dt.minute / 60
    )

    start_time_series = start_time_series[
        (start_time_series["start_hour"] >= 7)
        & (start_time_series["start_hour"] <= 11)
    ]

    if start_time_series.empty:
        st.info("07시~11시 구간의 시작 시간 데이터가 부족하여 분포를 생성할 수 없습니다.")
        return

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

        normal_y = (
            (1 / (sigma * np.sqrt(2 * np.pi)))
            * np.exp(-0.5 * ((x_values - mu) / sigma) ** 2)
            * 10
        )

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
                line=dict(width=2),
            )
        )

    dist_fig.update_layout(
        title="공장별 작업 시작 시간 정규 분포 (07-11시)",
        xaxis_title="작업 시작 시간",
        yaxis_title="비율 (%)",
        xaxis=dict(range=[6, 12], dtick=1, fixedrange=True),
        yaxis=dict(fixedrange=True),
        dragmode=False,
        template="plotly_white",
        margin=dict(t=50, l=20, r=20, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.4,
            xanchor="center",
            x=0.5,
            title_text="",
        ),
    )

    st.plotly_chart(dist_fig, use_container_width=True, config={"scrollZoom": False, "displayModeBar": False})


def show_dashboard_equipment_location_daily_view(default_factory="전체"):
    st.subheader("일단위 중장비 위치 현황")
    st.write("업체들이 등록한 중장비 위치를 날짜/공장/중장비 종류별로 확인합니다.")

    if "equipment_locations" not in st.session_state:
        st.session_state.equipment_locations = load_equipment_locations()

    loc_df = st.session_state.equipment_locations.copy()

    col1, col2, col3 = st.columns([1, 1, 1])

    selected_date = col1.date_input(
        "중장비 위치 조회 날짜",
        value=datetime.today().date(),
        key="dashboard_equipment_location_date",
    )

    factory_options = ["전체"] + FACTORIES
    default_factory_index = factory_options.index(default_factory) if default_factory in factory_options else 0

    selected_factory = col2.selectbox(
        "중장비 위치 조회 공장",
        factory_options,
        index=default_factory_index,
        key="dashboard_equipment_location_factory",
    )

    selected_equipment = col3.selectbox(
        "중장비 종류",
        ["전체"] + [e for e in EQUIPMENT_CATEGORIES if e != "없음"],
        index=0,
        key="dashboard_equipment_location_equipment",
    )

    if st.session_state.login_type == "LG.C":
        if st.button("위치 CSV 재생성", key="dashboard_rebuild_equipment_location_csv"):
            rebuild_equipment_locations_from_daily_reports()
            st.success("daily_reports 기준으로 equipment_locations.csv를 재생성했습니다.")
            st.rerun()

    if loc_df.empty:
        st.info("등록된 중장비 위치 데이터가 없습니다.")
        return

    loc_df["date"] = loc_df["date"].astype(str)
    selected_date_str = selected_date.strftime("%Y-%m-%d")

    filtered = loc_df[loc_df["date"] == selected_date_str].copy()

    if selected_factory != "전체":
        filtered = filtered[filtered["factory"] == selected_factory]

    if selected_equipment != "전체":
        filtered = filtered[filtered["equipment"] == selected_equipment]

    if filtered.empty:
        st.info("선택한 조건에 해당하는 중장비 위치 데이터가 없습니다.")
        return

    filtered["tons"] = pd.to_numeric(filtered["tons"], errors="coerce").fillna(0).astype(int)
    filtered["equipment_seq"] = pd.to_numeric(filtered["equipment_seq"], errors="coerce").fillna(0).astype(int)
    filtered["x_pct"] = pd.to_numeric(filtered["x_pct"], errors="coerce")
    filtered["y_pct"] = pd.to_numeric(filtered["y_pct"], errors="coerce")

    located = filtered[filtered["x_pct"].notna() & filtered["y_pct"].notna()].copy()
    unlocated = filtered[filtered["x_pct"].isna() | filtered["y_pct"].isna()].copy()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("총 중장비", len(filtered))
    m2.metric("위치 지정", len(located))
    m3.metric("위치 미지정", len(unlocated))
    m4.metric("참여 업체 수", filtered["company"].nunique())

    st.markdown("---")

    factories_to_show = sorted(located["factory"].dropna().unique())

    if not factories_to_show:
        st.warning("좌표가 지정된 중장비가 없습니다.")
    else:
        for factory in factories_to_show:
            factory_locations = located[located["factory"] == factory].copy()

            if factory_locations.empty:
                continue

            st.markdown(f"### {factory} 중장비 위치도")

            map_path = get_factory_map_path(factory)

            if map_path is None:
                st.warning(f"{factory} 공장 도면 이미지가 없습니다. data/factory_maps/{factory}.png 파일을 등록해 주세요.")
                continue

            try:
                original_map = Image.open(map_path)
                display_map = resize_map_for_display(original_map, MAP_DISPLAY_WIDTH)
                marker_locations = factory_locations.to_dict("records")
                marked_map = draw_multiple_location_markers(display_map, marker_locations)

                st.image(marked_map, use_container_width=True)

            except Exception as e:
                st.error(f"{factory} 도면 표시 중 오류가 발생했습니다: {e}")

    st.markdown("---")
    st.subheader("중장비 위치 상세 목록")

    display_cols = [
        "date",
        "factory",
        "company",
        "task",
        "work_order",
        "equipment_seq",
        "equipment",
        "tons",
        "x_pct",
        "y_pct",
        "updated_at",
    ]

    existing_cols = [col for col in display_cols if col in filtered.columns]

    display_df = filtered[existing_cols].copy().rename(columns={
        "date": "날짜",
        "factory": "공장",
        "company": "협력사",
        "task": "공사명",
        "work_order": "작업명",
        "equipment_seq": "순번",
        "equipment": "중장비",
        "tons": "ton",
        "x_pct": "X좌표비율",
        "y_pct": "Y좌표비율",
        "updated_at": "수정일시",
    })

    st.dataframe(display_df.reset_index(drop=True), use_container_width=True)


def show_dashboard():
    st.title("대시보드")
    st.write("공장별 일일 작업 현황과 진척도, 중장비 사용 현황을 확인합니다.")

    factory_filter = st.selectbox("공장 선택", ["전체"] + FACTORIES, index=0)
    reports = get_factory_reports(factory_filter)

    if not reports.empty:
        reports = reports.copy()
        reports["date"] = pd.to_datetime(reports["date"], errors="coerce")
        reports = reports[reports["date"].notna()]

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
        reports = reports[
            (reports["date"].dt.date >= start_date)
            & (reports["date"].dt.date <= end_date)
        ]

    total_personnel = int(reports["personnel"].sum()) if not reports.empty else 0
    equipment_count = 0
    avg_start = "-"
    avg_end = "-"

    if not reports.empty:
        equipment_df_temp = reports[["date", "equipment"]].copy()
        equipment_df_temp["equipment"] = equipment_df_temp["equipment"].str.split(", ")
        equipment_df_temp = equipment_df_temp.explode("equipment")
        equipment_df_temp["equipment"] = equipment_df_temp["equipment"].str.strip()
        equipment_count = equipment_df_temp[~equipment_df_temp["equipment"].isin(["없음", ""])].shape[0]

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

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("총 작업 건수", len(reports))
    c2.metric("진척도", f"{get_latest_progress_average(reports):.1f}%")
    c3.metric("등록 협력사 수", reports["company"].nunique() if not reports.empty else 0)
    c4.metric("총 출입 인원", total_personnel)
    c5.metric("총 출입 중장비 대수", equipment_count)
    c6.metric("평균 시작/종료", f"{avg_start} / {avg_end}")

    if reports.empty:
        st.info("등록된 작업이 없습니다. 공장 또는 기간 선택을 확인해 주세요.")
        st.markdown("---")
        show_dashboard_equipment_location_daily_view(default_factory=factory_filter)
        return

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

    fig1 = px.line(progress_trend, x="date", y="progress", markers=True, title="작업 진척도")
    fig1.update_yaxes(range=[0, 100])
    update_date_axis(fig1, plot_start, plot_end)
    fig1.update_layout(
        dragmode=False,
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True),
        margin=dict(l=20, r=20, t=40, b=40),
    )
    st.plotly_chart(fig1, use_container_width=True, config={"scrollZoom": False, "displayModeBar": False})

    personnel_trend = reports.groupby(reports["date"].dt.date)["personnel"].sum().reset_index()
    personnel_trend["date"] = pd.to_datetime(personnel_trend["date"])

    fig_personnel = px.line(personnel_trend, x="date", y="personnel", title="작업인원")

    if not personnel_trend.empty:
        peak_row = personnel_trend.loc[personnel_trend["personnel"].idxmax()]
        fig_personnel.add_trace(
            go.Scatter(
                x=[peak_row["date"]],
                y=[int(peak_row["personnel"])],
                mode="markers+text",
                marker=dict(color="red", size=12),
                text=[f"피크 {int(peak_row['personnel'])}"],
                textposition="top center",
                showlegend=False,
            )
        )

    update_date_axis(fig_personnel, plot_start, plot_end)
    fig_personnel.update_layout(
        dragmode=False,
        xaxis=dict(fixedrange=True),
        yaxis=dict(fixedrange=True),
        margin=dict(l=20, r=20, t=40, b=40),
    )
    st.plotly_chart(fig_personnel, use_container_width=True, config={"scrollZoom": False, "displayModeBar": False})

    equipment_rows = []
    for _, row in reports[["date", "equipment", "tons"]].iterrows():
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
                title="중장비+ton 일별 스택 바 차트",
                labels={"combo": "중장비+ton", "count": "건수", "date": "날짜"},
            )
            update_date_axis(fig2, plot_start, plot_end)
            fig2.update_layout(
                barmode="stack",
                xaxis_title="date",
                yaxis_title="건수",
                yaxis_autorange=True,
                dragmode=False,
                xaxis=dict(fixedrange=True),
                yaxis=dict(fixedrange=True),
                margin=dict(l=20, r=20, t=40, b=40),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=-0.3,
                    xanchor="center",
                    x=0.5,
                ),
            )
            st.plotly_chart(fig2, use_container_width=True, config={"scrollZoom": False, "displayModeBar": False})

    st.subheader("공장별 작업 요약")

    metric_options = {
        "진척도": "진척도",
        "출입 인원": "인원합계",
        "이동식 크레인": "크레인건수",
    }

    selected_metric = st.selectbox("그래프로 볼 항목", list(metric_options.keys()), index=0)

    if selected_metric == "진척도":
        time_series = reports.groupby([reports["date"].dt.date, "factory"]).agg({"progress": "mean"}).reset_index()
        time_series = time_series.rename(columns={"progress": "진척도"})
    elif selected_metric == "출입 인원":
        time_series = reports.groupby([reports["date"].dt.date, "factory"]).agg({"personnel": "sum"}).reset_index()
        time_series = time_series.rename(columns={"personnel": "인원합계"})
    else:
        equipment_rows = []
        for _, row in reports[["date", "factory", "equipment", "tons"]].iterrows():
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

    if not time_series.empty:
        time_series["date"] = pd.to_datetime(time_series["date"])
        fig_summary = px.line(
            time_series,
            x="date",
            y=metric_options[selected_metric],
            color="factory",
            markers=True,
            title=f"공장별 일별 {selected_metric}",
        )
        update_date_axis(fig_summary, start_date, end_date)
        fig_summary.update_layout(
            xaxis_title="date",
            yaxis_title=selected_metric,
            hovermode="x unified",
            dragmode=False,
            xaxis=dict(fixedrange=True),
            yaxis=dict(fixedrange=True),
            margin=dict(t=50, l=20, r=20, b=40),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.3,
                xanchor="center",
                x=0.5,
                title_text="",
            ),
        )
        st.plotly_chart(fig_summary, use_container_width=True, config={"scrollZoom": False, "displayModeBar": False})
    else:
        st.info("선택한 기간에 해당하는 데이터를 찾을 수 없습니다.")

    st.markdown("---")
    show_dashboard_start_time_distribution(reports)

    st.markdown("---")
    show_dashboard_equipment_location_daily_view(default_factory=factory_filter)


# =============================================================================
# 일일보고
# =============================================================================

def render_equipment_location_selector(idx: int, factory: str, equipment_type: str, equipment_ton: int):
    if equipment_type == "없음":
        return

    with st.expander(f"📍 위치 지정 - 중장비 {idx + 1}: {equipment_type} {equipment_ton}ton", expanded=False):
        if streamlit_image_coordinates is None:
            st.error(
                "도면 클릭 기능을 사용하려면 `streamlit-image-coordinates` 패키지가 필요합니다.\n\n"
                "`pip install streamlit-image-coordinates` 후 다시 실행해 주세요."
            )
            return

        map_path = get_factory_map_path(factory)

        if map_path is None:
            st.warning(
                f"{factory} 공장 도면 이미지가 없습니다.\n\n"
                f"`data/factory_maps/{factory}.png` 파일을 먼저 넣어주세요."
            )
            return

        try:
            original_map = Image.open(map_path)
            display_map = resize_map_for_display(original_map, MAP_DISPLAY_WIDTH)

            current = st.session_state.daily_equipment_data[idx]
            equipment_label = f"{equipment_type}+{equipment_ton}ton"

            marked_map = draw_location_marker(
                display_map,
                current.get("x_pct"),
                current.get("y_pct"),
                label=equipment_label,
            )

            st.caption("도면에서 중장비 위치를 클릭하세요. 클릭한 위치가 저장됩니다.")
            location_key_version = current.get("location_key_version", 0)
            clicked = streamlit_image_coordinates(
                marked_map,
                key=f"equipment_location_map_{idx}_{factory}_{location_key_version}",
            )

            if clicked is not None:
                click_x = clicked.get("x")
                click_y = clicked.get("y")

                if click_x is not None and click_y is not None:
                    x_pct = round(click_x / marked_map.width, 6)
                    y_pct = round(click_y / marked_map.height, 6)

                    old_x = st.session_state.daily_equipment_data[idx].get("x_pct")
                    old_y = st.session_state.daily_equipment_data[idx].get("y_pct")

                    st.session_state.daily_equipment_data[idx]["x_pct"] = x_pct
                    st.session_state.daily_equipment_data[idx]["y_pct"] = y_pct
                    st.session_state.daily_equipment_data[idx]["map_file"] = map_path.name

                    # 클릭 위치가 바뀐 경우 즉시 rerun해서 마커를 바로 다시 그림
                    if old_x != x_pct or old_y != y_pct:
                        st.rerun()

            if (
                st.session_state.daily_equipment_data[idx].get("x_pct") is not None
                and st.session_state.daily_equipment_data[idx].get("y_pct") is not None
            ):
                st.info("현재 이 중장비의 위치가 지정되어 있습니다.")

                if st.button(f"위치 초기화 - 중장비 {idx + 1}", key=f"clear_equipment_location_{idx}"):
                    st.session_state.daily_equipment_data[idx]["x_pct"] = None
                    st.session_state.daily_equipment_data[idx]["y_pct"] = None
                    st.session_state.daily_equipment_data[idx]["map_file"] = ""

                    current_version = st.session_state.daily_equipment_data[idx].get("location_key_version", 0)
                    st.session_state.daily_equipment_data[idx]["location_key_version"] = current_version + 1

                    st.rerun()

        except Exception as e:
            st.error(f"도면 이미지를 불러오는 중 오류가 발생했습니다: {e}")


def show_daily_report():
    st.title("일일 보고 등록")
    st.write("오늘 작업 내용을 입력하고 오전/추가 보고를 함께 관리하세요.")

    if st.session_state.login_type == "LG.C":
        show_daily_report_readonly_for_lgc()
        return

    default_construction = "현장 점검 및 보수"

    if st.session_state.login_type == "협력사":
        default_company = st.session_state.user
        default_factory = st.session_state.logged_factory
        company_tasks = get_company_tasks(default_company)

        if company_tasks:
            default_construction = company_tasks[0]

    st.subheader("기본 등록")

    report_date = st.date_input(
        "작업일",
        value=datetime.today(),
        key="daily_report_date_input",
    )

    current_report_date_str = report_date.strftime("%Y-%m-%d")

    if "last_daily_report_date_input" not in st.session_state:
        st.session_state.last_daily_report_date_input = current_report_date_str

    elif st.session_state.last_daily_report_date_input != current_report_date_str:
        st.session_state.last_daily_report_date_input = current_report_date_str
        reset_daily_equipment_inputs()
        st.rerun()

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

    if button_col1.button("중장비 추가", key="add_equipment_row"):
        if st.session_state.daily_equipment_rows < 5:
            st.session_state.daily_equipment_rows += 1
            st.session_state.daily_equipment_data.append(get_default_equipment_item())
            st.rerun()
        else:
            st.warning("중장비는 최대 5개까지 추가할 수 있습니다.")

    if button_col2.button("중장비 초기화", key="reset_equipment_rows"):
        reset_daily_equipment_inputs()
        st.rerun()

    if len(st.session_state.daily_equipment_data) != st.session_state.daily_equipment_rows:
        st.session_state.daily_equipment_data = st.session_state.daily_equipment_data[:st.session_state.daily_equipment_rows]
        while len(st.session_state.daily_equipment_data) < st.session_state.daily_equipment_rows:
            st.session_state.daily_equipment_data.append(get_default_equipment_item())

    header_col1, header_col2 = st.columns([2, 1])
    header_col1.markdown("**중장비**")
    header_col2.markdown("**ton**")

    for idx in range(st.session_state.daily_equipment_rows):
        current = st.session_state.daily_equipment_data[idx]

        eq_col1, eq_col2 = st.columns([2, 1])

        equipment_type = eq_col1.selectbox(
            f"중장비 {idx + 1}",
            EQUIPMENT_CATEGORIES,
            index=EQUIPMENT_CATEGORIES.index(current.get("equipment", "없음"))
            if current.get("equipment", "없음") in EQUIPMENT_CATEGORIES
            else 0,
            key=f"equipment_type_{idx}",
        )

        equipment_ton = eq_col2.number_input(
            f"ton {idx + 1}",
            min_value=0,
            max_value=1500,
            value=int(current.get("tons", 0)),
            key=f"equipment_tons_{idx}",
        )

        st.session_state.daily_equipment_data[idx]["equipment"] = equipment_type
        st.session_state.daily_equipment_data[idx]["tons"] = int(equipment_ton)

        render_equipment_location_selector(idx, factory, equipment_type, int(equipment_ton))

    # st.markdown("---")

    if st.button("기본 등록", type="primary", key="daily_morning_submit"):
        equipment_rows = st.session_state.daily_equipment_data
        parsed_equipment = [item for item in equipment_rows if item.get("equipment") != "없음"]

        equipment_text = ", ".join(
            f"{item['equipment']}+{int(item['tons'])}ton" for item in parsed_equipment
        ) if parsed_equipment else "없음"

        total_tons = sum(int(item.get("tons", 0)) for item in parsed_equipment)

        equipment_locations = []
        for item in parsed_equipment:
            equipment_locations.append({
                "equipment": item.get("equipment", ""),
                "tons": int(item.get("tons", 0)),
                "x_pct": item.get("x_pct"),
                "y_pct": item.get("y_pct"),
                "map_file": item.get("map_file", ""),
            })

        equipment_locations_json = json.dumps(equipment_locations, ensure_ascii=False)

        report_date_str = report_date.strftime("%Y-%m-%d")

        # 같은 날짜 + 같은 공장 + 같은 협력사 데이터가 있으면 덮어쓰기
        existing_mask = (
            (st.session_state.daily_reports["date"].astype(str) == report_date_str)
            & (st.session_state.daily_reports["factory"].astype(str) == str(factory))
            & (st.session_state.daily_reports["company"].astype(str) == str(company))
        )

        if existing_mask.any():
            # 기존 행 1개 선택
            existing_id = st.session_state.daily_reports[existing_mask].index[0]
            existing_row = st.session_state.daily_reports.loc[existing_id]

            # 기존 report_uid 유지해야 equipment_locations.csv도 정상 동기화됨
            existing_report_uid = str(existing_row.get("report_uid", "")).strip()
            if not existing_report_uid:
                existing_report_uid = str(uuid.uuid4())

            new_row = {
                "report_uid": existing_report_uid,
                "date": report_date_str,
                "factory": factory,
                "company": company,
                "task": construction_name,
                "work_order": project_name,
                "personnel": int(personnel),
                "entry_status": entry_status,
                "equipment": equipment_text,
                "tons": int(total_tons),

                # 아래 수정 정보는 기존 값 유지
                "start_time": existing_row.get("start_time", ""),
                "end_time": existing_row.get("end_time", ""),
                "progress": existing_row.get("progress", 0),
                "notes": existing_row.get("notes", ""),
                "images": existing_row.get("images", ""),

                # 중장비 위치는 새 기본 등록값으로 덮어쓰기
                "equipment_locations": equipment_locations_json,
            }

            for col, value in new_row.items():
                if col not in st.session_state.daily_reports.columns:
                    st.session_state.daily_reports[col] = ""
                st.session_state.daily_reports.at[existing_id, col] = value

            save_daily_reports()
            upsert_equipment_locations_from_report(new_row)

            reset_daily_equipment_inputs()

            st.success(f"{report_date_str} 기존 기본 보고를 덮어쓰기 저장했습니다.")
            st.rerun()

        else:
            report_uid = str(uuid.uuid4())

            new_row = {
                "report_uid": report_uid,
                "date": report_date_str,
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
                "equipment_locations": equipment_locations_json,
            }

            st.session_state.daily_reports = pd.concat(
                [st.session_state.daily_reports, pd.DataFrame([new_row])],
                ignore_index=True,
            )

            save_daily_reports()
            upsert_equipment_locations_from_report(new_row)

            reset_daily_equipment_inputs()

            st.success("아침 기본 보고와 중장비 위치 정보가 저장되었습니다.")
            st.rerun()


    st.markdown("---")
    show_daily_report_update_area()


def show_daily_report_readonly_for_lgc():
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
        start_minutes = (
            time_report.loc[time_report["start_time_parsed"].notna(), "start_time_parsed"].dt.hour * 60
            + time_report.loc[time_report["start_time_parsed"].notna(), "start_time_parsed"].dt.minute
        )
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
        end_minutes = (
            time_report.loc[time_report["end_time_parsed"].notna(), "end_time_parsed"].dt.hour * 60
            + time_report.loc[time_report["end_time_parsed"].notna(), "end_time_parsed"].dt.minute
        )
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
    report_display = report_display.drop(
        columns=["images", "tons", "equipment_locations", "report_uid"],
        errors="ignore",
    ).rename(columns={
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

    show_report_images(reports)
    show_equipment_location_view(reports)

def show_report_images(reports: pd.DataFrame):
    st.subheader("작업 사진")

    for _, row in reports.iterrows():
        image_paths = get_report_image_paths(row)

        if image_paths:
            row_date = row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], pd.Timestamp) else str(row["date"])

            with st.expander(f"{row_date} | {row['company']} / {row['task']} - 이미지 보기"):
                st.image(image_paths, use_container_width=True)
                
def show_equipment_location_view(reports: pd.DataFrame):
    st.subheader("중장비 위치 보기")

    any_location = False

    for _, row in reports.iterrows():
        locations = safe_json_loads(row.get("equipment_locations", ""), default=[])

        if not locations:
            continue

        any_location = True

        row_date = row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], pd.Timestamp) else str(row["date"])
        row_company = row["company"]
        row_task = row["task"]
        row_factory = row["factory"]

        with st.expander(f"{row_date} / {row_company} / {row_task} - 중장비 위치 보기", expanded=False):
            map_path = get_factory_map_path(row_factory)

            if map_path is None:
                st.warning(f"{row_factory} 공장 도면 이미지가 없습니다.")
                continue

            try:
                original_map = Image.open(map_path)
                display_map = resize_map_for_display(original_map, MAP_DISPLAY_WIDTH)

                marker_locations = []
                for idx, loc in enumerate(locations, start=1):
                    loc_copy = dict(loc)
                    loc_copy["equipment_seq"] = idx
                    marker_locations.append(loc_copy)

                marked_map = draw_multiple_location_markers(display_map, marker_locations)
                st.image(marked_map, use_container_width=True)

                for idx, loc in enumerate(locations):
                    company = str(loc.get("company", ""))
                    equipment = str(loc.get("equipment", ""))
                    tons = str(loc.get("tons", ""))
                    seq = str(loc.get("equipment_seq", idx + 1))

                    x_pct = loc.get("x_pct")
                    y_pct = loc.get("y_pct")

                    if company:
                        label = f"{seq}. {company}/{equipment}+{tons}ton"
                    else:
                        label = f"{seq}. {equipment}+{tons}ton"

                    if x_pct is None or y_pct is None:
                        st.write(f"{idx + 1}. {equipment} {tons}ton - 위치 미지정")
                    else:
                        st.write(f"{idx + 1}. {equipment} {tons}ton - 위치 지정 완료")

            except Exception as e:
                st.error(f"중장비 위치 이미지를 표시하는 중 오류가 발생했습니다: {e}")

    if not any_location:
        st.info("등록된 중장비 위치 정보가 없습니다.")

def show_daily_report_update_area():
    st.subheader("수정 정보 등록")

    filtered_reports = st.session_state.daily_reports

    if st.session_state.login_type == "협력사":
        filtered_reports = filtered_reports[filtered_reports["company"] == st.session_state.user]

    if filtered_reports.empty:
        st.write("등록된 일일 보고가 없습니다. 먼저 아침 기본 보고를 등록해주세요.")
        return

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

    st.write("👆 **상단에서 수정할 작업 일보를 먼저 선택해 주세요.**")

    with st.form(key="daily_report_update_form"):
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

        st.write("💡 **작업 시작/종료를 입력해 주세요.**")

        progress = st.slider(
            "**작업 진척도**",
            min_value=0,
            max_value=100,
            value=int(report_row.get("progress") or 0),
            step=1,
        )

        st.write("📊 **진척도 반영**: 오늘 작업이 완료된 만큼 슬라이더를 움직여 주세요.")

        notes = st.text_area(
            "금일 작업 사항",
            value=str(report_row.get("notes", "")),
            height=120,
            placeholder=(
                "- NCC Cracking 배관 용접 및 비파괴 검사 완료\n"
                "- GB-120 내부 클리닝 및 O/H 가스켓 교체\n"
                "- 현장 정리정돈 및 안전 점검 이상 없음"
            ),
        )

        uploaded_files = st.file_uploader(
            "작업 사진 업로드 (선택사항)",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
        )

        update_submit = st.form_submit_button("수정 저장")

        if update_submit:
            st.session_state.daily_reports.at[selected_id, "start_time"] = start_time.strftime("%H:%M")
            st.session_state.daily_reports.at[selected_id, "end_time"] = end_time.strftime("%H:%M")
            st.session_state.daily_reports.at[selected_id, "progress"] = int(progress)
            st.session_state.daily_reports.at[selected_id, "notes"] = notes

            if uploaded_files:
                save_dir = DAILY_IMAGE_DIR / report_row["date"] / sanitize_text(report_row["company"])

                old_images_raw = str(report_row.get("images", "")).split(",")

                for old_img in old_images_raw:
                    old_img_name = old_img.strip()

                    if old_img_name:
                        old_file_path = save_dir / old_img_name

                        if old_file_path.exists():
                            try:
                                old_file_path.unlink()
                            except Exception:
                                pass

                image_names = []

                for file in uploaded_files:
                    saved_name = save_uploaded_file(file, save_dir)
                    image_names.append(saved_name)

                st.session_state.daily_reports.at[selected_id, "images"] = ", ".join(image_names)

            save_daily_reports()

            updated_row = st.session_state.daily_reports.loc[selected_id].to_dict()
            upsert_equipment_locations_from_report(updated_row)

            st.success("일일 보고 추가/수정 정보와 중장비 위치 정보가 저장되었습니다.")
            st.rerun()

    st.markdown("---")
    st.subheader("최근 등록된 일일 보고")

    display_reports = update_reports.copy()
    display_reports["date"] = pd.to_datetime(display_reports["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    display_reports = display_reports.drop(
        columns=["images", "tons", "report_id", "equipment_locations", "report_uid"],
        errors="ignore",
    ).rename(columns={
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
                        col.image(image_path, use_container_width=True)
                    except Exception:
                        col.write(f"이미지를 로드할 수 없습니다: {image_path}")
    
    st.markdown("---")
    show_equipment_location_view(update_reports)
    
    st.markdown("---")
    st.subheader("잘못 등록한 보고 삭제")

    delete_options = [
        f"{int(row['report_id'])} | {row['date']} / {row['company']} / {row['work_order']} / {row['factory']}"
        for _, row in update_reports.iterrows()
    ]

    selected_delete = st.selectbox("삭제할 보고를 선택하세요", delete_options, key="delete_report_select")
    selected_id = int(selected_delete.split(" | ")[0])

    if st.button("선택한 보고 삭제"):
        target_row = st.session_state.daily_reports.loc[selected_id]
        target_report_uid = str(target_row.get("report_uid", "")).strip()

        if "images" in target_row and str(target_row["images"]).strip():
            del_dir = DAILY_IMAGE_DIR / target_row["date"] / sanitize_text(target_row["company"])
            del_images_raw = str(target_row["images"]).split(",")

            for del_img in del_images_raw:
                del_img_name = del_img.strip()

                if del_img_name:
                    del_file_path = del_dir / del_img_name

                    if del_file_path.exists():
                        try:
                            del_file_path.unlink()
                        except Exception:
                            pass

        
        
        delete_equipment_locations_by_report_uid(target_report_uid)

        st.session_state.daily_reports = (
            st.session_state.daily_reports
            .drop(index=selected_id)
            .reset_index(drop=True)
        )
    
        save_daily_reports()

        st.success("선택한 일일 보고, 작업 사진, 중장비 위치 정보가 삭제되었습니다.")
        st.rerun()


# =============================================================================
# 공지사항
# =============================================================================

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
            ann_images = st.file_uploader(
                "공지 이미지 업로드 (선택사항)",
                type=["png", "jpg", "jpeg"],
                accept_multiple_files=True,
            )
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

                    st.session_state.announcements = pd.concat(
                        [st.session_state.announcements, pd.DataFrame([new_ann])],
                        ignore_index=True,
                    )
                    save_announcements()
                    st.success("공지사항이 등록되었습니다.")
                    st.rerun()

    st.markdown("---")
    st.subheader("공지 목록")

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
            target_row = st.session_state.announcements.loc[selected_id]

            if "images" in target_row and str(target_row["images"]).strip():
                del_dir = ANNOUNCEMENT_IMAGE_DIR / target_row["date"] / sanitize_text(target_row["title"])
                del_images_raw = str(target_row["images"]).split(",")

                for del_img in del_images_raw:
                    del_img_name = del_img.strip()

                    if del_img_name:
                        del_file_path = del_dir / del_img_name

                        if del_file_path.exists():
                            try:
                                del_file_path.unlink()
                            except Exception:
                                pass

                try:
                    if del_dir.exists() and not any(del_dir.iterdir()):
                        del_dir.rmdir()
                except Exception:
                    pass

            st.session_state.announcements = st.session_state.announcements.drop(index=selected_id).reset_index(drop=True)
            save_announcements()
            st.success("선택한 공지 데이터 및 첨부 이미지 파일이 삭제되었습니다.")
            st.rerun()


# =============================================================================
# 평가 / 마일리지
# =============================================================================

def show_evaluation():
    st.title("평가 / 마일리지")
    st.write("작업 업체에 대한 우수 평가, 경고, 마일리지 관리를 할 수 있습니다.")

    if st.session_state.login_type == "협력사":
        st.info(
            "💡 **협력사 안내 사항**\n\n"
            "* 협력사 계정은 평가 및 마일리지를 직접 등록할 수 없습니다.\n"
            "* **안전 유의**: 경고가 누적 3회 이상 발생 시 현장 안전 조치 및 출입 제한 등이 발생할 수 있습니다."
        )
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

            eval_images = st.file_uploader(
                "평가 이미지 업로드 (선택사항)",
                type=["png", "jpg", "jpeg"],
                accept_multiple_files=True,
            )

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

                    st.session_state.evaluations = pd.concat(
                        [st.session_state.evaluations, pd.DataFrame([new_eval])],
                        ignore_index=True,
                    )
                    save_evaluations()
                    st.success("평가가 저장되었습니다.")
                    st.rerun()

    st.markdown("---")
    st.subheader("협력사별 누적 평가 / 마일리지")

    eval_df = st.session_state.evaluations.copy()

    if not eval_df.empty:
        eval_df["eval_id"] = eval_df.index

        company_summary = eval_df.groupby("company").agg(
            경고누계=("warning_count", "sum"),
            마일리지누계=("mileage", "sum"),
        ).reset_index()

        st.dataframe(company_summary.sort_values(by="마일리지누계", ascending=False), use_container_width=True)

    st.markdown("---")
    st.subheader("최근 평가 / 경고 현황")

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
            target_row = st.session_state.evaluations.loc[selected_id]
            target_date = target_row["date"].strftime("%Y-%m-%d") if isinstance(target_row["date"], pd.Timestamp) else str(target_row["date"])

            if "images" in target_row and str(target_row["images"]).strip():
                del_dir = EVALUATION_IMAGE_DIR / target_date / sanitize_text(target_row["company"])
                del_images_raw = str(target_row["images"]).split(",")

                for del_img in del_images_raw:
                    del_img_name = del_img.strip()

                    if del_img_name:
                        del_file_path = del_dir / del_img_name

                        if del_file_path.exists():
                            try:
                                del_file_path.unlink()
                            except Exception:
                                pass

            st.session_state.evaluations = st.session_state.evaluations.drop(index=selected_id).reset_index(drop=True)
            save_evaluations()
            st.success("선택한 평가 데이터 및 실제 이미지 파일이 삭제되었습니다.")
            st.rerun()


# =============================================================================
# 설정
# =============================================================================

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
    st.subheader("GitHub 데이터 Push / 백업")

    if not github_enabled():
        st.warning(
            "GitHub 설정이 없습니다.\n\n"
            "Streamlit Secrets에 [GITHUB] TOKEN, OWNER, REPO, BRANCH, DATA_PATH를 설정해 주세요."
        )
    else:
        cfg = get_github_config()
        backup_files = get_data_backup_files()
        summary = get_backup_summary(backup_files)

        st.info(
            f"현재 GitHub 저장소: {cfg['owner']}/{cfg['repo']} / branch: {cfg['branch']}\n\n"
            "관리자가 최종 확인 후 현재 앱 내부 data 폴더 전체를 GitHub에 Push합니다."
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("전체 파일", summary["total"])
        c2.metric("CSV/JSON", summary["csv_json"])
        c3.metric("이미지", summary["images"])
        c4.metric("총 용량", f"{summary['total_size_mb']} MB")

        st.caption(
                    "Push 대상: data 폴더 아래 모든 파일입니다. "
                    "daily_reports.csv, equipment_locations.csv, 이미지, 공장 도면 등이 포함됩니다."
                )

        col_push1, col_push2 = st.columns(2)

        with col_push1:
            if st.button("data 폴더 GitHub Push", key="github_push_data_folder", type="primary"):
                with st.spinner("GitHub로 data 폴더를 Push하는 중입니다. 파일 수가 많으면 시간이 걸릴 수 있습니다."):
                    uploaded, failed = push_data_folder_to_github()

                if uploaded:
                    st.success(f"GitHub Push 완료: {len(uploaded)}개 파일")

                    with st.expander("Push 완료 파일 목록", expanded=False):
                        for file_name in uploaded:
                            st.write(f"- {file_name}")

                if failed:
                    st.error(f"GitHub Push 실패: {len(failed)}개 파일")

                    with st.expander("Push 실패 파일 목록", expanded=True):
                        for file_name, err in failed:
                            st.write(f"- {file_name}")
                            st.code(err, language="text")

        with col_push2:
            if st.button("data 폴더 GitHub 완전 동기화", key="github_sync_data_folder_with_delete"):
                st.warning(
                    "완전 동기화는 GitHub data 폴더에는 있지만 현재 앱 data 폴더에는 없는 파일을 삭제합니다."
                )

                with st.spinner("GitHub data 폴더를 현재 앱 data 폴더와 완전 동기화하는 중입니다."):
                    uploaded, upload_failed, deleted, delete_failed = sync_data_folder_to_github_with_delete()

                if uploaded:
                    st.success(f"생성/수정 완료: {len(uploaded)}개 파일")

                    with st.expander("생성/수정 완료 파일 목록", expanded=False):
                        for file_name in uploaded:
                            st.write(f"- {file_name}")

                if deleted:
                    st.warning(f"GitHub에서 삭제 완료: {len(deleted)}개 파일")

                    with st.expander("삭제된 GitHub 파일 목록", expanded=False):
                        for file_name in deleted:
                            st.write(f"- {file_name}")

                if upload_failed:
                    st.error(f"생성/수정 실패: {len(upload_failed)}개 파일")

                    with st.expander("생성/수정 실패 목록", expanded=True):
                        for file_name, err in upload_failed:
                            st.write(f"- {file_name}")
                            st.code(err, language="text")

                if delete_failed:
                    st.error(f"삭제 실패: {len(delete_failed)}개 파일")

                    with st.expander("삭제 실패 목록", expanded=True):
                        for file_name, err in delete_failed:
                            st.write(f"- {file_name}")
                            st.code(err, language="text")
        
    st.markdown("---")
    st.subheader("공장 도면 이미지 안내")

    st.write("중장비 위치 지정에 사용할 공장 도면 이미지를 아래 폴더에 넣어주세요.")
    st.code("data/factory_maps/{공장명}.png", language="text")

    existing_maps = []

    for factory in FACTORIES:
        map_path = get_factory_map_path(factory)

        if map_path:
            existing_maps.append({"공장": factory, "도면파일": str(map_path)})
        else:
            existing_maps.append({"공장": factory, "도면파일": "미등록"})

    st.dataframe(pd.DataFrame(existing_maps), use_container_width=True)

    st.markdown("---")
    st.write("세션 데이터를 새로고침하면 저장된 CSV와 설정을 다시 로드합니다. 데이터 초기화는 수행되지 않습니다.")

    if st.button("세션 새로고침", key="refresh_session"):
        st.session_state.daily_reports = load_daily_reports()
        st.session_state.equipment_locations = load_equipment_locations()
        st.session_state.announcements = load_announcements()
        st.session_state.evaluations = load_evaluations()
        st.session_state.settings = load_settings()
        st.session_state.uploaded_images = {}
        st.success("세션 상태가 새로고침되었습니다.")

    st.markdown("---")
    st.write("초기화 대상: 일일 보고, 중장비 위치, 공지사항, 평가/마일리지, 업로드된 이미지 파일")
    st.warning("데이터 초기화는 되돌릴 수 없습니다. 신중하게 사용하세요.")

    if st.button("전체 데이터 초기화", key="reset_system_data"):
        reset_system_data()
        st.success("시스템 데이터가 초기화되었습니다.")


# =============================================================================
# main
# =============================================================================

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
        elif page == "일일 보고":
            show_daily_report()
        elif page == "공지사항":
            show_announcements()
        elif page == "평가/마일리지":
            show_evaluation()
        elif page == SETTINGS_PAGE:
            if not st.session_state.get("settings_access_granted", False):
                auth_password = st.text_input(
                    "추가 관리자 비밀번호 입력",
                    type="password",
                    key="settings_auth_password"
                )

                if st.button("인증", key="settings_auth_button"):
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

