import html
import json
import os
import random
import secrets
import smtplib
import sqlite3
import string
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, redirect, url_for, session, Response, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from PIL import Image, ImageFilter, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    HAS_WEBPUSH = True
except ImportError:
    HAS_WEBPUSH = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

SCRIPT_MTIME = os.path.getmtime(__file__) if os.path.exists(__file__) else 0
IG_USERNAME = "grande_studio3"
IG_WEB_URL = f"https://www.instagram.com/{IG_USERNAME}/"
IG_APP_URL = f"instagram://user?username={IG_USERNAME}"

app = Flask(__name__, static_folder=STATIC_DIR)
app.secret_key = os.environ.get("SECRET_KEY") or "grande_studio_persistent_secret_key_2026"
app.permanent_session_lifetime = timedelta(days=3650)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

DB_PATH = os.path.join(BASE_DIR, "grande_studio.db")
VAPID_KEYS_PATH = os.path.join(BASE_DIR, "vapid_keys.json")

WORK_START = "09:00"
WORK_END = "18:30"
SLOT_STEP_MIN = 30

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@grandestudio.local")

FAILED_LOGIN_TRACKER: dict[str, list[float]] = {}
MAX_FAILED_ATTEMPTS = 5
LOCK_WINDOW_SECONDS = 300


def get_vapid_keys() -> dict:
    if os.path.exists(VAPID_KEYS_PATH):
        try:
            with open(VAPID_KEYS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("private_key") and data.get("public_key"):
                    return data
        except Exception:
            pass

    if HAS_WEBPUSH:
        try:
            import base64
            from cryptography.hazmat.primitives import serialization
            vapid = Vapid()
            vapid.generate_keys()
            priv_pem = vapid.private_pem().decode("utf-8")
            pub_raw = vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint
            )
            pub_b64 = base64.urlsafe_b64encode(pub_raw).rstrip(b"=").decode("utf-8")
            keys = {"private_key": priv_pem, "public_key": pub_b64}
            with open(VAPID_KEYS_PATH, "w", encoding="utf-8") as f:
                json.dump(keys, f)
            return keys
        except Exception as e:
            print(f"[VAPID] Gabim gjatë gjenerimit të çelësave: {e}")

    return {"private_key": "", "public_key": ""}


def prepare_clean_nails_asset() -> str | None:
    clean_path = os.path.join(STATIC_DIR, "thonjet_clean.png")
    candidates = [
        os.path.join(BASE_DIR, "thonjët.jpg"),
        os.path.join(BASE_DIR, "Thonjët.jpg"),
        os.path.join(BASE_DIR, "thonjet.jpg"),
        os.path.join(BASE_DIR, "Thonjet.jpg"),
        os.path.join(STATIC_DIR, "thonjët.jpg"),
        os.path.join(STATIC_DIR, "thonjet.jpg"),
    ]
    src_file = next((p for p in candidates if os.path.exists(p)), None)
    if not src_file:
        return "/static/thonjet_clean.png" if os.path.exists(clean_path) else None
    return "/static/thonjet_clean.png"


def prepare_ig_avatar_asset() -> str | None:
    clean_path = os.path.join(STATIC_DIR, "ig_avatar_clean.png")
    candidates = [
        os.path.join(BASE_DIR, "896023468_18110131619343947_7234878166828640368_n.jpg"),
        os.path.join(BASE_DIR, "ig_profile.jpg"),
        os.path.join(STATIC_DIR, "ig_profile.jpg"),
    ]
    src_file = next((p for p in candidates if os.path.exists(p)), None)
    if not src_file:
        return "/static/ig_avatar_clean.png" if os.path.exists(clean_path) else None
    return "/static/ig_avatar_clean.png"


def prepare_app_icons() -> None:
    icon_192 = os.path.join(STATIC_DIR, "icon-192.png")
    icon_512 = os.path.join(STATIC_DIR, "icon-512.png")
    if os.path.exists(icon_192) and os.path.exists(icon_512):
        return
    if HAS_PIL:
        try:
            for size, out_path in [(192, icon_192), (512, icon_512)]:
                img = Image.new("RGBA", (size, size), (114, 9, 29, 255))
                draw = ImageDraw.Draw(img)
                pad = int(size * 0.06)
                draw.ellipse((pad, pad, size - pad, size - pad), fill=(69, 3, 15, 255), outline=(242, 118, 224, 255), width=max(3, size // 64))
                img.save(out_path, "PNG")
            return
        except Exception:
            pass
    for sz, pth in [(192, icon_192), (512, icon_512)]:
        if not os.path.exists(pth):
            with open(pth, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admin_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            email TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS salon_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            work_start TEXT NOT NULL DEFAULT '09:00',
            work_end TEXT NOT NULL DEFAULT '18:30',
            slot_step_min INTEGER NOT NULL DEFAULT 30,
            hygiene_note TEXT NOT NULL DEFAULT 'Veglat e thonjve, qerpikëve dhe vetullave sterilizohen pas çdo klienteje.',
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL DEFAULT '',
            contact_type TEXT NOT NULL,
            contact_value TEXT UNIQUE NOT NULL,
            client_phone TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            reset_pending INTEGER DEFAULT 0,
            reset_token TEXT DEFAULT '',
            reset_sent_via TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            duration_min INTEGER NOT NULL,
            price REAL NOT NULL,
            description TEXT DEFAULT ''
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            client_phone TEXT NOT NULL,
            client_contact TEXT DEFAULT '',
            service_id INTEGER,
            appt_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            notes TEXT DEFAULT '',
            status TEXT DEFAULT 'Konfirmuar',
            is_new INTEGER DEFAULT 1,
            client_notified INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (service_id) REFERENCES services (id) ON DELETE SET NULL
        )
    """)

    cur.execute("PRAGMA table_info(appointments)")
    appt_cols = [row["name"] for row in cur.fetchall()]
    if "is_new" not in appt_cols:
        cur.execute("ALTER TABLE appointments ADD COLUMN is_new INTEGER DEFAULT 0")
    if "client_contact" not in appt_cols:
        cur.execute("ALTER TABLE appointments ADD COLUMN client_contact TEXT DEFAULT ''")
    if "client_notified" not in appt_cols:
        cur.execute("ALTER TABLE appointments ADD COLUMN client_notified INTEGER DEFAULT 0")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT UNIQUE NOT NULL,
            subscription_json TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            client_contact TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("SELECT COUNT(*) AS cnt FROM salon_settings")
    if cur.fetchone()["cnt"] == 0:
        cur.execute(
            """
            INSERT INTO salon_settings (id, work_start, work_end, slot_step_min, hygiene_note, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
            """,
            (WORK_START, WORK_END, SLOT_STEP_MIN, "Veglat e thonjve, qerpikëve dhe vetullave sterilizohen pas çdo klienteje.", datetime.now().isoformat()),
        )

    cur.execute("SELECT COUNT(*) AS cnt FROM services")
    if cur.fetchone()["cnt"] == 0:
        default_services = [
            ("Thonj", "Manikyr me Xhel (Gel Polish)", 60, 15.0, "Pastrim kutikulash, formësim dhe xhel."),
            ("Thonj", "Zgjatje me Tipsa / Xhel", 90, 25.0, "Zgjatje natyrale ose të gjata me xhel."),
            ("Qerpikë", "Lash Lift & Tint", 60, 25.0, "Ngritje dhe ngjyrosje për qerpikë natyralë."),
            ("Vetulla", "Laminim Vetullash + Formësim", 45, 20.0, "Rregullim dhe formësim i saktë i vetullave."),
            ("Kombinim", "Total Glow: Thonj Xhel + Qerpikë + Vetulla", 150, 52.0, "Trajtim i plotë i kombinuar."),
        ]
        cur.executemany(
            "INSERT INTO services (category, name, duration_min, price, description) VALUES (?, ?, ?, ?, ?)",
            default_services,
        )

    conn.commit()
    conn.close()


def normalize_contact_identifier(raw_val: str) -> tuple[str, str]:
    val = (raw_val or "").strip()
    if "@" in val and "." in val.split("@")[-1]:
        return "email", val.lower()
    ig_clean = val.lstrip("@").strip().lower()
    return "instagram", f"@{ig_clean}" if ig_clean else ""


def get_salon_settings() -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM salon_settings WHERE id = 1")
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"work_start": WORK_START, "work_end": WORK_END, "slot_step_min": SLOT_STEP_MIN, "hygiene_note": ""}


def dispatch_web_push(subs: list[sqlite3.Row], payload_dict: dict) -> None:
    if not HAS_WEBPUSH or not subs:
        return
    vapid = get_vapid_keys()
    if not vapid.get("private_key"):
        return

    payload = json.dumps(payload_dict, ensure_ascii=False)
    dead_ids = []

    for sub in subs:
        try:
            sub_info = json.loads(sub["subscription_json"])
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=vapid["private_key"],
                vapid_claims={"sub": "mailto:noreply@grandestudio.local"},
                ttl=86400,
                headers={"Urgency": "high", "Topic": payload_dict.get("tag", "grande-studio")}
            )
        except WebPushException as ex:
            if ex.response and ex.response.status_code in (404, 410):
                dead_ids.append(sub["id"])
        except Exception:
            pass

    if dead_ids:
        conn = get_db()
        cur = conn.cursor()
        cur.executemany("DELETE FROM push_subscriptions WHERE id = ?", [(i,) for i in dead_ids])
        conn.commit()
        conn.close()


def send_push_to_admins(title: str, body: str, url: str = "/admin", appt_id: int | None = None) -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, subscription_json FROM push_subscriptions WHERE role = 'admin'")
    subs = cur.fetchall()
    conn.close()
    actions = [{"action": "confirm_appt", "title": "✅ Konfirmo"}, {"action": "cancel_appt", "title": "❌ Anulo"}] if appt_id else []
    dispatch_web_push(subs, {
        "title": title,
        "body": body,
        "url": url,
        "appt_id": appt_id,
        "tag": f"grande-studio-admin-{appt_id or 'alert'}",
        "actions": actions,
        "timestamp": datetime.now().isoformat()
    })


def send_push_to_client(client_contact: str, title: str, body: str, url: str = "/", appt_id: int | None = None) -> None:
    conn = get_db()
    cur = conn.cursor()
    clean_contact = (client_contact or "").strip().lower()
    if clean_contact:
        cur.execute("SELECT id, subscription_json FROM push_subscriptions WHERE role = 'client' AND (LOWER(client_contact) = ? OR client_contact = '')", (clean_contact,))
    else:
        cur.execute("SELECT id, subscription_json FROM push_subscriptions WHERE role = 'client'")
    subs = cur.fetchall()
    conn.close()
    dispatch_web_push(subs, {
        "title": title,
        "body": body,
        "url": url,
        "appt_id": appt_id,
        "tag": f"grande-studio-client-{appt_id or 'alert'}",
        "actions": [],
        "timestamp": datetime.now().isoformat()
    })


def get_admin_account():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin_account WHERE id = 1")
    acc = cur.fetchone()
    conn.close()
    return acc


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def time_to_min(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def min_to_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def get_available_slots(appt_date: str, duration_min: int):
    settings = get_salon_settings()
    w_start = settings.get("work_start") or WORK_START
    w_end = settings.get("work_end") or WORK_END
    step_min = int(settings.get("slot_step_min") or SLOT_STEP_MIN)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT start_time, end_time FROM appointments WHERE appt_date = ? AND status IN ('Konfirmuar', 'Në Pritje', 'Bllokuar')", (appt_date,))
    busy = [(time_to_min(r["start_time"]), time_to_min(r["end_time"])) for r in cur.fetchall()]
    conn.close()

    start_m = time_to_min(w_start)
    end_m = time_to_min(w_end)
    slots = []
    curr = start_m
    while curr + duration_min <= end_m:
        cand_start = curr
        cand_end = curr + duration_min
        overlap = any(not (cand_end <= b_start or cand_start >= b_end) for b_start, b_end in busy)
        if not overlap:
            slots.append(min_to_time(cand_start))
        curr += step_min
    return slots


def get_banner_artwork_html() -> str:
    nails_url = prepare_clean_nails_asset()
    ig_avatar_url = prepare_ig_avatar_asset()
    ig_top_html = f'<img src="{ig_avatar_url}" alt="Instagram" class="ig-banner-avatar">' if ig_avatar_url else '<div class="ig-banner-avatar ig-fallback-badge">IG</div>'
    nails_bottom_html = f'<img src="{nails_url}" alt="Nails" class="nails-banner-bottom">' if nails_url else ''
    return f"{ig_top_html}\n{nails_bottom_html}"


def get_ig_nav_button_html() -> str:
    return f"""
    <a href="{IG_WEB_URL}" data-ig-app="{IG_APP_URL}" data-ig-web="{IG_WEB_URL}" onclick="openInstagramProfile(event)" class="btn-3d btn-ig-3d">
      <span>IG</span>
    </a>
    """


BASE_STYLE = """
<style>
  :root {
    --vishnje-dark: #45030f;
    --vishnje: #72091d;
    --vishnje-bright: #b81d3d;
    --bg: #fdf6f7;
    --card: #ffffff;
    --text: #261217;
    --muted: #6e4e57;
    --border: #edd3da;
    --ok: #1f7a3d;
    --warn: #b41e2d;
  }
  * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { margin: 0; min-height: 100vh; color: var(--text); background: #26030a; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 18px 12px; }
  .iphone-chassis { width: 412px; max-width: 100%; height: 852px; max-height: calc(100vh - 64px); background: #141112; border-radius: 52px; padding: 13px; box-shadow: 0 28px 65px rgba(0,0,0,0.65); display: flex; flex-direction: column; position: relative; }
  .iphone-screen { flex: 1; border-radius: 40px; overflow: hidden; display: flex; flex-direction: column; position: relative; background: #fdf5f7; }
  .app-scroll-area { flex: 1; overflow-y: auto; overflow-x: hidden; -webkit-overflow-scrolling: touch; }
  @media (max-width: 520px), (display-mode: standalone) {
    body { padding: 0; background: var(--bg); display: block; }
    .iphone-chassis { width: 100%; height: auto; min-height: 100vh; max-height: none; border-radius: 0; padding: 0; box-shadow: none; }
    .iphone-screen { border-radius: 0; min-height: 100vh; }
  }
  header { background: linear-gradient(145deg, var(--vishnje-bright), var(--vishnje)); color: #fff; padding: 12px; position: sticky; top: 0; z-index: 30; }
  .header-inner { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
  .btn-3d { display: inline-flex; align-items: center; justify-content: center; gap: 4px; border: none; border-radius: 11px; font-weight: 700; cursor: pointer; text-decoration: none; }
  .btn-vishnje-3d { background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje)); color: #fff; padding: 10px 14px; font-size: 0.88rem; box-shadow: 0 4px 0 var(--vishnje-dark); }
  .btn-confirm-3d { background: linear-gradient(180deg, #16a34a, #15803d); color: #fff; padding: 8px 10px; font-size: 0.76rem; box-shadow: 0 3px 0 #14532d; }
  .btn-cancel-3d { background: linear-gradient(180deg, #e11d48, #be123c); color: #fff; padding: 8px 10px; font-size: 0.76rem; box-shadow: 0 3px 0 #881337; }
  .btn-ghost-3d { background: rgba(255,255,255,0.15); color: #fff; padding: 6px 8px; font-size: 0.7rem; border: 1px solid rgba(255,255,255,0.28); }
  .btn-ig-3d { background: linear-gradient(135deg, #f58529, #dd2a7b, #8134af); color: #fff; padding: 6px 9px; font-size: 0.72rem; }
  .container { padding: 13px 12px 28px; }
  .combo-banner { background: linear-gradient(135deg, #47020e, #7a0a1f); color: #fff; border-radius: 17px; padding: 13px; margin-bottom: 13px; position: relative; overflow: hidden; min-height: 160px; }
  .ig-banner-avatar { position: absolute; right: 20px; top: 10px; width: 64px; height: 64px; border-radius: 50%; object-fit: cover; border: 2px solid #fff; z-index: 2; }
  .ig-fallback-badge { display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #f58529, #dd2a7b); color: #fff; font-weight: bold; }
  .nails-banner-bottom { position: absolute; right: 2px; bottom: 10px; width: 140px; max-width: 44%; opacity: 0.9; z-index: 1; }
  .tabs-bar { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 13px; }
  .tab-btn { background: #fff; color: var(--vishnje); border: 1px solid var(--border); border-radius: 12px; padding: 9px 6px; font-size: 0.74rem; font-weight: 700; cursor: pointer; text-align: center; }
  .tab-btn.active { background: var(--vishnje); color: #fff; }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }
  .card-3d { background: rgba(255,255,255,0.96); border: 1px solid var(--border); border-radius: 16px; padding: 14px; margin-bottom: 13px; box-shadow: 0 5px 0 #e6cbd2; }
  h2 { margin: 0 0 9px; font-size: 0.98rem; color: var(--vishnje); }
  label { display: block; font-size: 0.8rem; font-weight: 700; margin: 10px 0 4px; color: var(--vishnje-dark); }
  input, select, textarea { width: 100%; padding: 9px 10px; border: 1.5px solid #e2c2ca; border-radius: 10px; font-size: 0.86rem; background: #fffdfd; color: var(--text); }
  .slots { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .slot-3d { padding: 7px 10px; border-radius: 9px; border: 1px solid #dfb9c3; background: #fff; color: var(--vishnje); font-size: 0.8rem; font-weight: 700; cursor: pointer; }
  .slot-3d.active { background: var(--vishnje); color: #fff; }
  .alert { padding: 10px; border-radius: 12px; margin-bottom: 11px; font-size: 0.82rem; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
  .alert-ok { background: #ebfaef; color: var(--ok); border: 1px solid #a8e4ba; }
  .alert-err { background: #fff0f2; color: var(--warn); border: 1px solid #f7bcc4; }
  .dismiss-btn { background: rgba(0,0,0,0.08); border: none; border-radius: 8px; padding: 4px 8px; cursor: pointer; }
  .live-toast-banner { position: fixed; top: 14px; left: 50%; transform: translateX(-50%); width: 92%; max-width: 390px; background: linear-gradient(135deg, #9f1239, #e11d48); color: #fff; padding: 14px; border-radius: 16px; z-index: 9999; box-shadow: 0 15px 35px rgba(159, 18, 57, 0.55); border: 2px solid #fda4af; display: none; }
  table { width: 100%; border-collapse: collapse; font-size: 0.76rem; }
  th, td { padding: 7px 5px; border-bottom: 1px solid var(--border); text-align: left; }
  th { color: var(--vishnje); font-size: 0.7rem; text-transform: uppercase; }
  .badge { padding: 2px 7px; border-radius: 999px; font-size: 0.68rem; font-weight: 800; }
  .b-ok { background: #d3f9d8; color: #1f7a3d; }
  .b-wait { background: #fff3bf; color: #9c6f00; }
  .b-can { background: #f1f3f5; color: #495057; }
  .btn-sm-3d { padding: 6px 9px; border-radius: 8px; border: none; cursor: pointer; font-size: 0.74rem; font-weight: 700; margin-bottom: 3px; }
  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  @media (max-width: 420px) { .row-2 { grid-template-columns: 1fr; } }
</style>
"""


def render_iphone_shell(title: str, inner_html: str, role_tag: str = "client", client_contact_tag: str = "") -> str:
    now_clock = datetime.now().strftime("%H:%M")
    push_role = "admin" if role_tag == "admin" else "client"
    safe_client_contact = (client_contact_tag or "").replace("'", "").strip().lower()

    return f"""
    <!doctype html>
    <html lang="sq">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
      <title>{title}</title>
      <link rel="manifest" href="/manifest.json">
      <meta name="theme-color" content="#72091d">
      <meta name="mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-capable" content="yes">
      <link rel="apple-touch-icon" href="/static/icon-192.png">
      {BASE_STYLE}
    </head>
    <body>
      <div class="iphone-chassis">
        <div class="iphone-screen">
          <div style="background:#4f0413; color:#fff; padding:8px 16px; display:flex; justify-content:space-between; font-size:0.75rem; font-weight:bold;">
            <span>{now_clock}</span><span>Grande Studio 5G</span>
          </div>
          <div class="app-scroll-area">
            {inner_html}
          </div>
        </div>
      </div>
      <script>
        const CURRENT_ROLE = '{role_tag}';
        const PUSH_SUB_ROLE = '{push_role}';
        const PUSH_CLIENT_CONTACT = '{safe_client_contact}';

        if (PUSH_CLIENT_CONTACT) {{
          try {{ localStorage.setItem('gs_client_contact', PUSH_CLIENT_CONTACT); }} catch(e) {{}}
        }}

        function unlockAudioContext() {{
          try {{
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (!window.__gsAudioCtx) window.__gsAudioCtx = new AudioCtx();
            if (window.__gsAudioCtx.state === 'suspended') window.__gsAudioCtx.resume();
          }} catch(e) {{}}
        }}

        function playAutoAlertSound() {{
          try {{
            unlockAudioContext();
            const ctx = window.__gsAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
            const notes = [587.33, 880.00, 1174.66];
            notes.forEach((freq, idx) => {{
              const osc = ctx.createOscillator();
              const gain = ctx.createGain();
              osc.frequency.value = freq;
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.start(ctx.currentTime + idx * 0.14);
              osc.stop(ctx.currentTime + idx * 0.14 + 0.35);
            }});
          }} catch(e) {{}}
        }}

        function urlBase64ToUint8Array(base64String) {{
          const padding = '='.repeat((4 - base64String.length % 4) % 4);
          const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
          const rawData = window.atob(base64);
          const outputArray = new Uint8Array(rawData.length);
          for (let i = 0; i < rawData.length; ++i) {{
            outputArray[i] = rawData.charCodeAt(i);
          }}
          return outputArray;
        }}

        async function registerPushNotifications() {{
          try {{
            if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) return;
            let perm = Notification.permission;
            if (perm === 'default') {{
              perm = await Notification.requestPermission();
            }}
            if (perm === 'granted') {{
              const reg = await navigator.serviceWorker.ready;
              const keyRes = await fetch('/api/push/public-key');
              const keyData = await keyRes.json();
              if (keyData.publicKey) {{
                let sub = await reg.pushManager.getSubscription();
                if (!sub) {{
                  sub = await reg.pushManager.subscribe({{
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(keyData.publicKey)
                  }});
                }}
                const subJson = sub.toJSON ? sub.toJSON() : JSON.parse(JSON.stringify(sub));
                subJson.role = PUSH_SUB_ROLE;
                subJson.client_contact = PUSH_CLIENT_CONTACT || localStorage.getItem('gs_client_contact') || '';
                await fetch('/api/push/subscribe', {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  body: JSON.stringify(subJson)
                }});
              }}
            }}
          }} catch (e) {{}}
        }}

        if ('serviceWorker' in navigator) {{
          window.addEventListener('load', () => {{
            navigator.serviceWorker.register('/sw.js').then(() => {{
              registerPushNotifications();
            }});
          }});

          navigator.serviceWorker.addEventListener('message', (event) => {{
            if (event.data && event.data.type === 'GS_PUSH_RECEIVED') {{
              playAutoAlertSound();
              if (navigator.vibrate) navigator.vibrate([300, 150, 300]);
            }}
          }});
        }}

        document.addEventListener('pointerdown', () => {{
          unlockAudioContext();
          registerPushNotifications();
        }}, {{ once: true }});

        function openInstagramProfile(ev) {{
          if (ev) ev.preventDefault();
          window.location.href = '{IG_APP_URL}';
          setTimeout(() => {{ window.open('{IG_WEB_URL}', '_blank'); }}, 600);
        }}
      </script>
    </body>
    </html>
    """


@app.route("/manifest.json")
def manifest():
    prepare_app_icons()
    return jsonify({
        "name": "Grande Studio",
        "short_name": "GrandeStudio",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#fdf6f7",
        "theme_color": "#72091d",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}]
    })


@app.route("/sw.js")
def service_worker():
    js = """
    self.addEventListener('install', e => self.skipWaiting());
    self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

    self.addEventListener('push', event => {
      let data = {
        title: '🔔 Grande Studio',
        body: 'Keni një njoftim të ri!',
        url: '/'
      };
      if (event.data) {
        try {
          data = Object.assign(data, event.data.json());
        } catch (e) {
          data.body = event.data.text();
        }
      }

      const options = {
        body: data.body,
        icon: '/static/icon-192.png',
        badge: '/static/icon-192.png',
        vibrate: [400, 200, 400, 200, 500],
        requireInteraction: true,
        actions: data.actions || [],
        data: { url: data.url || '/', appt_id: data.appt_id || null }
      };

      event.waitUntil(
        Promise.all([
          self.registration.showNotification(data.title, options),
          self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
            for (const client of clientList) {
              client.postMessage({ type: 'GS_PUSH_RECEIVED', payload: data });
            }
          })
        ])
      );
    });

    self.addEventListener('notificationclick', event => {
      event.notification.close();
      const action = event.action;
      const notifData = event.notification.data || {};
      const targetUrl = notifData.url || '/';

      if ((action === 'confirm_appt' || action === 'cancel_appt') && notifData.appt_id) {
        const newStatus = action === 'confirm_appt' ? 'Konfirmuar' : 'Anuluar';
        event.waitUntil(
          fetch('/api/push/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ appt_id: notifData.appt_id, status: newStatus })
          }).then(() => {
            return self.registration.showNotification('Statusi u përditësua', {
              body: `Termini u bë: ${newStatus}`,
              icon: '/static/icon-192.png'
            });
          }).catch(() => {
            if (clients.openWindow) return clients.openWindow('/admin');
          })
        );
        return;
      }

      event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
          for (const client of clientList) {
            if ('focus' in client) {
              client.navigate(targetUrl);
              return client.focus();
            }
          }
          if (clients.openWindow) {
            return clients.openWindow(targetUrl);
          }
        })
      );
    });
    """
    return Response(js, mimetype="application/javascript")


@app.route("/api/push/public-key")
def api_push_public_key():
    keys = get_vapid_keys()
    return jsonify({"publicKey": keys.get("public_key", ""), "supported": HAS_WEBPUSH})


@app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    data = request.get_json(silent=True)
    if not data or not data.get("endpoint"):
        return jsonify({"ok": False}), 400
    role = "admin" if (session.get("admin_logged_in") or data.get("role") == "admin") else "client"
    client_contact = (session.get("client_contact") or data.get("client_contact") or "").strip().lower()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO push_subscriptions (endpoint, subscription_json, role, client_contact, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET subscription_json = excluded.subscription_json, role = excluded.role, client_contact = excluded.client_contact
    """, (data["endpoint"], json.dumps(data), role, client_contact, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/push/action", methods=["POST"])
def api_push_action():
    data = request.get_json(silent=True) or {}
    appt_id = data.get("appt_id")
    new_status = data.get("status")
    if not appt_id or new_status not in ("Konfirmuar", "Anuluar"):
        return jsonify({"ok": False}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,))
    appt = cur.fetchone()
    if appt:
        cur.execute("UPDATE appointments SET status = ?, is_new = 0, client_notified = 0 WHERE id = ?", (new_status, appt_id))
        conn.commit()
        send_push_to_client(
            appt["client_contact"] or "",
            title=f"✅ Statusi u përditësua: {new_status}",
            body=f"Termini juaj për datën {appt['appt_date']} u {new_status.lower()}.",
            url="/",
            appt_id=int(appt_id)
        )
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/client/status-updates")
def api_client_status_updates():
    contact = (session.get("client_contact") or request.args.get("contact") or "").strip().lower()
    if not contact:
        return jsonify({"updates": []})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.appt_date, a.start_time, a.status, s.name AS service_name
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE LOWER(a.client_contact) = ? AND a.client_notified = 0 AND a.status IN ('Konfirmuar', 'Anuluar')
        ORDER BY a.id DESC
    """, (contact,))
    rows = [dict(r) for r in cur.fetchall()]
    if rows:
        cur.executemany("UPDATE appointments SET client_notified = 1 WHERE id = ?", [(r["id"],) for r in rows])
        conn.commit()
    conn.close()
    return jsonify({"updates": rows})


@app.route("/api/admin/new-bookings")
@admin_required
def api_admin_new_bookings():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, a.client_name, a.client_phone, a.appt_date, a.start_time, a.end_time, a.notes, a.status, s.name AS service_name, s.category, s.price
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE a.is_new = 1 AND a.status IN ('Në Pritje', 'Konfirmuar')
        ORDER BY a.id DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"count": len(rows), "bookings": rows})


@app.route("/api/slots")
def api_slots():
    appt_date = request.args.get("date", "")
    service_id = request.args.get("service_id", type=int)
    if not appt_date or not service_id:
        return jsonify({"slots": []})
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT duration_min FROM services WHERE id = ?", (service_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"slots": []})
    return jsonify({"slots": get_available_slots(appt_date, row["duration_min"])})


@app.route("/", methods=["GET", "POST"])
def index():
    init_db()
    if session.get("admin_logged_in") and get_admin_account():
        return redirect(url_for("admin_panel"))

    prepare_clean_nails_asset()
    prepare_ig_avatar_asset()
    prepare_app_icons()
    banner_art_html = get_banner_artwork_html()
    ig_nav_html = get_ig_nav_button_html()
    settings = get_salon_settings()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM services ORDER BY id")
    services = cur.fetchall()

    msg = None
    msg_type = "ok"
    active_tab = request.form.get("active_tab") or request.args.get("tab") or "book"
    today_str = datetime.now().strftime("%Y-%m-%d")

    if request.method == "POST":
        post_mode = request.form.get("post_mode", "booking")

        if post_mode == "client_auth":
            raw_contact = request.form.get("auth_contact", "").strip()
            raw_password = request.form.get("auth_password", "")
            _, c_val = normalize_contact_identifier(raw_contact)
            cur.execute("SELECT * FROM clients WHERE contact_value = ?", (c_val,))
            existing = cur.fetchone()
            if existing and check_password_hash(existing["password_hash"], raw_password):
                session.permanent = True
                session["client_logged_in"] = True
                session["client_id"] = existing["id"]
                session["client_contact"] = existing["contact_value"]
                session["client_name"] = existing["full_name"] or existing["contact_value"]
                msg = f"Mirësevini, {existing['contact_value']}!"
            else:
                msg = "Të dhënat e kyçjes janë të pasakta."
                msg_type = "err"
            active_tab = "book"

        elif post_mode == "client_register":
            raw_contact = request.form.get("reg_contact", "").strip()
            raw_password = request.form.get("reg_password", "")
            full_name = request.form.get("reg_name", "").strip()
            phone = request.form.get("reg_phone", "").strip()
            c_type, c_val = normalize_contact_identifier(raw_contact)
            if c_val and len(raw_password) >= 4:
                try:
                    cur.execute("""
                        INSERT INTO clients (full_name, contact_type, contact_value, client_phone, password_hash, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (full_name or c_val, c_type, c_val, phone, generate_password_hash(raw_password), datetime.now().isoformat(), datetime.now().isoformat()))
                    conn.commit()
                    new_id = cur.lastrowid
                    session.permanent = True
                    session["client_logged_in"] = True
                    session["client_id"] = new_id
                    session["client_contact"] = c_val
                    session["client_name"] = full_name or c_val
                    msg = "Llogaria u krijua dhe u kyçët me sukses!"
                except Exception:
                    msg = "Ky email ose Instagram tashmë ekziston. Përdorni kyçjen!"
                    msg_type = "err"
            else:
                msg = "Plotësoni të dhënat saktë (password të paktën 4 karaktere)."
                msg_type = "err"
            active_tab = "book"

        elif post_mode == "booking":
            raw_contact = (request.form.get("client_contact") or session.get("client_contact") or "").strip()
            _, norm_contact = normalize_contact_identifier(raw_contact)
            client_name = (request.form.get("client_name") or session.get("client_name") or norm_contact).strip()
            client_phone = (request.form.get("client_phone") or "").strip()
            service_id = request.form.get("service_id", type=int)
            appt_date = request.form.get("appt_date", "").strip()
            start_time = request.form.get("start_time", "").strip()
            notes = request.form.get("notes", "").strip()

            cur.execute("SELECT * FROM services WHERE id = ?", (service_id,))
            svc = cur.fetchone()

            if not client_phone or not svc or not appt_date or not start_time:
                msg = "Plotësoni fushat e detyrueshme për rezervim."
                msg_type = "err"
            else:
                end_time = min_to_time(time_to_min(start_time) + svc["duration_min"])
                cur.execute("""
                    INSERT INTO appointments (client_name, client_phone, client_contact, service_id, appt_date, start_time, end_time, notes, status, is_new, client_notified, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Në Pritje', 1, 1, ?)
                """, (client_name, client_phone, norm_contact, service_id, appt_date, start_time, end_time, notes, datetime.now().isoformat()))
                conn.commit()
                new_id = cur.lastrowid
                
                send_push_to_admins(
                    title="🔔 Kërkesë e Re për Termin",
                    body=f"{client_name} - {svc['name']} ({appt_date} në {start_time})",
                    url="/admin",
                    appt_id=new_id
                )
                msg = "Rezervimi u dërgua me sukses! Do të njoftoheni sapo të konfirmohet."
            active_tab = "book"

    saved_contact = session.get("client_contact", "")
    is_logged_in = bool(session.get("client_logged_in") and saved_contact)
    conn.close()

    service_options = "\n".join(f'<option value="{s["id"]}">{html.escape(s["category"])}: {html.escape(s["name"])} ({s["price"]:.0f}€)</option>' for s in services)
    alert_html = f'<div class="alert alert-{"ok" if msg_type == "ok" else "err"}"><span>{html.escape(msg)}</span><button class="dismiss-btn" onclick="this.parentElement.remove()">✕</button></div>' if msg else ""

    client_auth_section = ""
    if is_logged_in:
        client_auth_section = f"""
        <div class="card-3d" style="background:#fff9fb;">
          <b>👤 Llogaria: {html.escape(saved_contact)}</b>
          <a href="/client/logout" style="display:block; margin-top:6px; color:var(--warn); font-size:0.75rem; font-weight:bold;">Dil nga llogaria</a>
        </div>
        """
    else:
        client_auth_section = """
        <div class="row-2">
          <div class="card-3d" style="margin-bottom:0;">
            <h2 style="font-size:0.85rem;">🔑 Klient i Regjistruar</h2>
            <form method="POST">
              <input type="hidden" name="post_mode" value="client_auth">
              <label>Email / Instagram</label>
              <input type="text" name="auth_contact" placeholder="sara@email.com" required>
              <label>Password</label>
              <input type="password" name="auth_password" placeholder="Password" required>
              <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:8px; font-size:0.75rem;">Kyçu</button>
            </form>
          </div>
          <div class="card-3d" style="margin-bottom:0;">
            <h2 style="font-size:0.85rem;">✨ Klient i Ri (Regjistrim)</h2>
            <form method="POST">
              <input type="hidden" name="post_mode" value="client_register">
              <label>Email ose Instagram</label>
              <input type="text" name="reg_contact" placeholder="sara@email.com" required>
              <label>Krijo Password</label>
              <input type="password" name="reg_password" placeholder="Min 4 karaktere" required>
              <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:8px; font-size:0.75rem;">Regjistrohu</button>
            </form>
          </div>
        </div>
        """

    inner = f"""
      <div id="clientLiveToastBanner" class="live-toast-banner">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <b style="font-size:0.95rem;" id="clientLiveToastTitle">🔔 NJOFTIM — GRANDE STUDIO</b>
          <button type="button" onclick="document.getElementById('clientLiveToastBanner').style.display='none'" style="background:rgba(255,255,255,0.22); border:none; color:#fff; font-weight:bold; border-radius:8px; padding:4px 9px; cursor:pointer;">✕</button>
        </div>
        <div id="clientLiveToastBody" style="font-size:0.82rem; margin-top:6px; line-height:1.4;"></div>
      </div>

      <header>
        <div class="header-inner">
          <div>
            <h1 style="margin:0; font-size:0.95rem;">GRANDE STUDIO</h1>
            <p style="margin:0; font-size:0.65rem;">Nails • Lashes • Brows</p>
          </div>
          <div>{ig_nav_html}</div>
          <div><a href="/admin" class="btn-3d btn-ghost-3d">🔒 Admin</a></div>
        </div>
      </header>
      <div class="container">
        {alert_html}
        <div class="combo-banner">
          {banner_art_html}
          <div style="position:relative; z-index:3;">
            <h3 style="margin:0 0 4px; font-size:0.95rem;">Rezervo Online</h3>
            <p style="margin:0; font-size:0.75rem; max-width:60%;">Zgjidhni shërbimin dhe orarin tuaj me lehtësi.</p>
          </div>
        </div>
        <div class="tabs-bar">
          <button class="tab-btn {'active' if active_tab == 'book' else ''}" onclick="switchTab('book')">📅 Rezervo</button>
          <button class="tab-btn {'active' if active_tab == 'install' else ''}" onclick="switchTab('install')">📱 Instalo</button>
          <button class="tab-btn {'active' if active_tab == 'info' else ''}" onclick="switchTab('info')">🕰️ Info</button>
        </div>

        <div id="tab-book" class="tab-panel {'active' if active_tab == 'book' else ''}">
          {client_auth_section}
          <div class="card-3d" style="margin-top:12px;">
            <h2>Formular Rezervimi</h2>
            <form method="POST" id="bookingForm">
              <input type="hidden" name="post_mode" value="booking">
              <label>Shërbimi</label>
              <select name="service_id" id="service_id" required>{service_options}</select>
              <label>Data</label>
              <input type="date" name="appt_date" id="appt_date" value="{today_str}" min="{today_str}" required>
              <label>Orari i Lirë</label>
              <div id="slotsContainer" class="slots">Duke ngarkuar oraret...</div>
              <input type="hidden" name="start_time" id="start_time" required>
              <div class="row-2">
                <div><label>Emri dhe Mbiemri</label><input type="text" name="client_name" placeholder="Emri juaj" required></div>
                <div><label>Telefoni</label><input type="tel" name="client_phone" placeholder="+383 44 ..." required></div>
              </div>
              <label>Detaje opsionale</label>
              <textarea name="notes" rows="2" placeholder="Shënime..."></textarea>
              <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">Dërgo Rezervimin</button>
            </form>
          </div>
        </div>

        <div id="tab-install" class="tab-panel {'active' if active_tab == 'install' else ''}">
          <div class="card-3d">
            <h2>Udhëzim për Instalim</h2>
            <p style="font-size:0.8rem; line-height:1.4;">Hapni faqen në <b>Safari</b> (iPhone) ose <b>Chrome/Samsung Internet</b> (Android), shtypni menynë dhe zgjidhni <b>Shto në ekranin kryesor (Add to Home Screen)</b>.</p>
          </div>
        </div>

        <div id="tab-info" class="tab-panel {'active' if active_tab == 'info' else ''}">
          <div class="card-3d">
            <h2>Orari &amp; Sterilizimi</h2>
            <p style="font-size:0.8rem; line-height:1.4;">Orari: {settings.get('work_start')} – {settings.get('work_end')}.<br>{html.escape(settings.get('hygiene_note'))}</p>
          </div>
        </div>
      </div>
      <script>
        function switchTab(t) {{
          document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase().includes(t)));
          document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + t));
        }}
        const svcEl = document.getElementById('service_id');
        const dateEl = document.getElementById('appt_date');
        const slotsEl = document.getElementById('slotsContainer');
        const startInput = document.getElementById('start_time');

        async function loadSlots() {{
          startInput.value = '';
          if (!svcEl.value) return;
          slotsEl.innerHTML = 'Duke ngarkuar...';
          const res = await fetch(`/api/slots?date=${{encodeURIComponent(dateEl.value)}}&service_id=${{encodeURIComponent(svcEl.value)}}`);
          const data = await res.json();
          slotsEl.innerHTML = '';
          if (!data.slots || data.slots.length === 0) {{
            slotsEl.innerHTML = '<span style="color:var(--warn); font-size:0.8rem;">Nuk ka orare të lira.</span>';
            return;
          }}
          data.slots.forEach(slot => {{
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'slot-3d';
            btn.textContent = slot;
            btn.onclick = () => {{
              document.querySelectorAll('.slot-3d').forEach(b => b.classList.remove('active'));
              btn.classList.add('active');
              startInput.value = slot;
            }};
            slotsEl.appendChild(btn);
          }});
        }}
        svcEl.addEventListener('change', loadSlots);
        dateEl.addEventListener('change', loadSlots);
        loadSlots();

        setInterval(async () => {{
          try {{
            const c = (PUSH_CLIENT_CONTACT || localStorage.getItem('gs_client_contact') || '').trim();
            if (!c) return;
            const res = await fetch(`/api/client/status-updates?contact=${{encodeURIComponent(c)}}`);
            if (!res.ok) return;
            const data = await res.json();
            if (data.updates && data.updates.length > 0) {{
              const u = data.updates[0];
              playAutoAlertSound();
              if (navigator.vibrate) navigator.vibrate([350, 150, 350]);
              const banner = document.getElementById('clientLiveToastBanner');
              const titleEl = document.getElementById('clientLiveToastTitle');
              const bodyEl = document.getElementById('clientLiveToastBody');
              if (u.status === 'Konfirmuar') {{
                titleEl.textContent = '✅ TERMINI U KONFIRMUA!';
                bodyEl.innerHTML = `Trajtimi: <b>${{u.service_name || 'Trajtim'}}</b><br>📅 Data: <b>${{u.appt_date}}</b> në ora <b>${{u.start_time}}</b>`;
              }} else {{
                titleEl.textContent = '❌ TERMINI U ANULUA';
                bodyEl.innerHTML = `Trajtimi: <b>${{u.service_name || 'Trajtim'}}</b><br>📅 Data: <b>${{u.appt_date}}</b> në ora <b>${{u.start_time}}</b>`;
              }}
              banner.style.display = 'block';
            }}
          }} catch (e) {{}}
        }}, 5000);
      </script>
    """
    return render_iphone_shell("Grande Studio", inner, role_tag="client_logged_in" if is_logged_in else "client", client_contact_tag=saved_contact)


@app.route("/client/logout")
def client_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    init_db()
    acc = get_admin_account()
    if acc and session.get("admin_logged_in"):
        return redirect(url_for("admin_panel"))

    err = None
    if acc is None:
        if request.method == "POST":
            email = request.form.get("reg_email", "").strip()
            pwd = request.form.get("reg_password", "")
            if email and len(pwd) >= 6:
                conn = get_db()
                cur = conn.cursor()
                cur.execute("INSERT INTO admin_account (id, email, password_hash, updated_at) VALUES (1, ?, ?, ?)",
                            (email, generate_password_hash(pwd), datetime.now().isoformat()))
                conn.commit()
                conn.close()
                session.clear()
                session["admin_logged_in"] = True
                return redirect(url_for("admin_panel"))
            else:
                err = "Plotësoni emailin dhe fjalëkalimin (min 6 karaktere)."

        inner = f"""
          <header>
            <div class="header-inner">
              <h1 style="margin:0; font-size:0.95rem;">Regjistrim Admin</h1>
              <a href="/" class="btn-3d btn-ghost-3d">← Klienti</a>
            </div>
          </header>
          <div class="container">
            {f'<div class="alert alert-err"><span>{err}</span></div>' if err else ''}
            <div class="card-3d">
              <h2>Krijo Llogarinë e Adminit</h2>
              <form method="POST">
                <label>Email</label>
                <input type="email" name="reg_email" placeholder="admin@grandestudio.com" required>
                <label>Fjalëkalimi</label>
                <input type="password" name="reg_password" required>
                <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">Krijo &amp; Hyj</button>
              </form>
            </div>
          </div>
        """
        return render_iphone_shell("Regjistrim Admin", inner, role_tag="admin")

    if request.method == "POST":
        email_in = request.form.get("email", "").strip().lower()
        pwd_in = request.form.get("password", "")
        if email_in == acc["email"].strip().lower() and check_password_hash(acc["password_hash"], pwd_in):
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        err = "Emaili ose fjalëkalimi i pasaktë."

    inner = f"""
      <header>
        <div class="header-inner">
          <h1 style="margin:0; font-size:0.95rem;">Hyrja Admin</h1>
          <a href="/" class="btn-3d btn-ghost-3d">← Klienti</a>
        </div>
      </header>
      <div class="container">
        {f'<div class="alert alert-err"><span>{err}</span></div>' if err else ''}
        <div class="card-3d">
          <h2>Kyçuni në Panelin Admin</h2>
          <form method="POST">
            <label>Email</label>
            <input type="email" name="email" required>
            <label>Fjalëkalimi</label>
            <input type="password" name="password" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">Kyçu</button>
          </form>
        </div>
      </div>
    """
    return render_iphone_shell("Hyrja Admin", inner, role_tag="admin")


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    init_db()
    acc = get_admin_account()
    if not acc:
        return redirect(url_for("admin_login"))

    conn = get_db()
    cur = conn.cursor()
    msg = None

    if request.method == "POST":
        action = request.form.get("action")
        if action == "set_status":
            appt_id = request.form.get("appt_id", type=int)
            status = request.form.get("status")
            if appt_id and status in ("Konfirmuar", "Anuluar"):
                cur.execute("SELECT * FROM appointments WHERE id = ?", (appt_id,))
                appt_row = cur.fetchone()
                cur.execute("UPDATE appointments SET status = ?, is_new = 0, client_notified = 0 WHERE id = ?", (status, appt_id))
                conn.commit()
                if appt_row:
                    send_push_to_client(
                        appt_row["client_contact"] or "",
                        title=f"✅ Statusi u përditësua: {status}",
                        body=f"Termini juaj për datën {appt_row['appt_date']} u {status.lower()}.",
                        url="/",
                        appt_id=appt_id
                    )
                msg = f"Termini u përditësua në {status}."

    cur.execute("SELECT a.*, s.name AS service_name FROM appointments a LEFT JOIN services s ON a.service_id = s.id ORDER BY a.id DESC")
    appts = cur.fetchall()
    conn.close()

    rows_html = ""
    for a in appts:
        rows_html += f"""
        <tr>
          <td><b>{html.escape(a["appt_date"])}</b><br>{html.escape(a["start_time"])}</td>
          <td><b>{html.escape(a["client_name"])}</b><br>{html.escape(a["client_phone"])}</td>
          <td>{html.escape(a["service_name"] or "-")}</td>
          <td><span class="badge {'b-ok' if a['status']=='Konfirmuar' else 'b-can'}">{html.escape(a["status"])}</span></td>
          <td>
            <form method="POST" style="display:inline;"><input type="hidden" name="action" value="set_status"><input type="hidden" name="appt_id" value="{a['id']}"><input type="hidden" name="status" value="Konfirmuar"><button class="btn-sm-3d" style="background:#d3f9d8; color:#1f7a3d;">Konfirmo</button></form>
            <form method="POST" style="display:inline;"><input type="hidden" name="action" value="set_status"><input type="hidden" name="appt_id" value="{a['id']}"><input type="hidden" name="status" value="Anuluar"><button class="btn-sm-3d" style="background:#ffe3e3; color:#b41e2d;">Anulo</button></form>
          </td>
        </tr>
        """

    alert_html = f'<div class="alert alert-ok"><span>{msg}</span><button class="dismiss-btn" onclick="this.parentElement.remove()">✕</button></div>' if msg else ""

    inner = f"""
      <div id="liveToastBanner" class="live-toast-banner">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <b style="font-size:0.95rem;">🔔 KËRKESË E RE PËR TERMIN!</b>
          <button type="button" onclick="document.getElementById('liveToastBanner').style.display='none'" style="background:rgba(255,255,255,0.22); border:none; color:#fff; font-weight:bold; border-radius:8px; padding:4px 9px; cursor:pointer;">✕</button>
        </div>
        <div id="liveToastBody" style="font-size:0.82rem; margin-top:6px; line-height:1.4;"></div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px;" id="liveToastActions"></div>
      </div>

      <header>
        <div class="header-inner">
          <div>
            <h1 style="margin:0; font-size:0.95rem;">PANELI I ADMINIT</h1>
            <p style="margin:0; font-size:0.65rem;">{html.escape(acc['email'])}</p>
          </div>
          <div style="display:flex; gap:4px;">
            <a href="/" class="btn-3d btn-ghost-3d">← Klienti</a>
            <a href="/admin/logout" class="btn-3d btn-ghost-3d" style="background:rgba(180,30,45,0.4);">Dil</a>
          </div>
        </div>
      </header>
      <div class="container">
        {alert_html}
        <div class="card-3d">
          <h2>Menaxhimi i Termineve</h2>
          <table>
            <thead><tr><th>Data/Ora</th><th>Klienti</th><th>Shërbimi</th><th>Statusi</th><th>Veprime</th></tr></thead>
            <tbody>{rows_html if rows_html else '<tr><td colspan="5">Nuk ka termine.</td></tr>'}</tbody>
          </table>
        </div>
      </div>
      <script>
        let lastMaxId = 0;
        async function checkNewAdminBookings() {{
          try {{
            const res = await fetch('/api/admin/new-bookings');
            if (!res.ok) return;
            const data = await res.json();
            if (data.bookings && data.bookings.length > 0) {{
              const n = data.bookings[0];
              if (n.id > lastMaxId) {{
                lastMaxId = n.id;
                playAutoAlertSound();
                if (navigator.vibrate) navigator.vibrate([400, 150, 400]);
                const toast = document.getElementById('liveToastBanner');
                document.getElementById('liveToastBody').innerHTML = `<b>${{n.client_name}}</b> (${{n.client_phone}})<br>💅 ${{n.service_name}} • ${{n.appt_date}} në <b>${{n.start_time}}</b>`;
                document.getElementById('liveToastActions').innerHTML = `
                  <form method="POST" style="margin:0;"><input type="hidden" name="action" value="set_status"><input type="hidden" name="appt_id" value="${{n.id}}"><input type="hidden" name="status" value="Konfirmuar"><button class="btn-3d btn-confirm-3d" style="width:100%;">✅ Konfirmo</button></form>
                  <form method="POST" style="margin:0;"><input type="hidden" name="action" value="set_status"><input type="hidden" name="appt_id" value="${{n.id}}"><input type="hidden" name="status" value="Anuluar"><button class="btn-3d btn-cancel-3d" style="width:100%;">❌ Anulo</button></form>
                `;
                toast.style.display = 'block';
              }}
            }}
          }} catch(e) {{}}
        }}
        setInterval(checkNewAdminBookings, 5000);
      </script>
    """
    return render_iphone_shell("Admin — Grande Studio", inner, role_tag="admin")


if __name__ == "__main__":
    init_db()
    prepare_clean_nails_asset()
    prepare_ig_avatar_asset()
    prepare_app_icons()
    get_vapid_keys()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)