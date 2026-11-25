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
IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.path.exists("/var/task"))
RUNTIME_DIR = "/tmp" if IS_SERVERLESS else BASE_DIR
STATIC_DIR = os.path.join(RUNTIME_DIR, "static") if IS_SERVERLESS else os.path.join(BASE_DIR, "static")
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

DB_PATH = os.path.join(RUNTIME_DIR, "grande_studio.db")
VAPID_KEYS_PATH = os.path.join(RUNTIME_DIR, "vapid_keys.json")

# Orari i punës së sallonit (09:00 - 18:30, hap çdo 30 minuta)
WORK_START = "09:00"
WORK_END = "18:30"
SLOT_STEP_MIN = 30

# Konfigurimi opsional SMTP për dërgim real emaili
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "noreply@grandestudio.local")

# Mbrojtje kundër tentativave të shpejta të gabuara të kyçjes (brute-force)
FAILED_LOGIN_TRACKER: dict[str, list[float]] = {}
MAX_FAILED_ATTEMPTS = 5
LOCK_WINDOW_SECONDS = 300


def get_vapid_keys() -> dict:
    """Gjeneron ose ngarkon çelësat VAPID për Web Push Notifications në iPhone & Android."""
    env_priv = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    env_pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    if env_priv and env_pub:
        return {"private_key": env_priv.replace("\\n", "\n"), "public_key": env_pub}

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


def is_login_locked(client_ip: str) -> tuple[bool, int]:
    now = time.time()
    attempts = [t for t in FAILED_LOGIN_TRACKER.get(client_ip, []) if now - t < LOCK_WINDOW_SECONDS]
    FAILED_LOGIN_TRACKER[client_ip] = attempts
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        remaining = int(LOCK_WINDOW_SECONDS - (now - attempts[0]))
        return True, max(1, remaining)
    return False, 0


def record_failed_login(client_ip: str) -> None:
    now = time.time()
    attempts = [t for t in FAILED_LOGIN_TRACKER.get(client_ip, []) if now - t < LOCK_WINDOW_SECONDS]
    attempts.append(now)
    FAILED_LOGIN_TRACKER[client_ip] = attempts


def clear_failed_login(client_ip: str) -> None:
    FAILED_LOGIN_TRACKER.pop(client_ip, None)


def prepare_clean_nails_asset() -> str | None:
    clean_path = os.path.join(STATIC_DIR, "thonjet_clean.png")
    orig_static = os.path.join(BASE_DIR, "static")
    candidates = [
        os.path.join(BASE_DIR, "thonjët.jpg"),
        os.path.join(BASE_DIR, "Thonjët.jpg"),
        os.path.join(BASE_DIR, "thonjet.jpg"),
        os.path.join(BASE_DIR, "Thonjet.jpg"),
        os.path.join(BASE_DIR, "thonjët.png"),
        os.path.join(BASE_DIR, "thonjet.png"),
        os.path.join(orig_static, "thonjët.jpg"),
        os.path.join(orig_static, "Thonjët.jpg"),
        os.path.join(orig_static, "thonjet.jpg"),
    ]

    src_file = next((p for p in candidates if os.path.exists(p)), None)
    if not src_file:
        if os.path.exists(clean_path):
            return "/static/thonjet_clean.png"
        return None

    latest_m = max(os.path.getmtime(src_file), SCRIPT_MTIME)
    if os.path.exists(clean_path) and os.path.getmtime(clean_path) >= latest_m:
        return "/static/thonjet_clean.png"

    if HAS_PIL:
        try:
            img = Image.open(src_file).convert("RGBA")
            w, h = img.size
            pixels = list(img.getdata())

            grande_pink = (242, 118, 224)
            pure_white = (255, 255, 255)
            subtext_y_start = int(h * 0.72)

            new_pixels = []
            for idx, (r, g, b, a) in enumerate(pixels):
                y = idx // w

                if r >= 238 and g >= 238 and b >= 238:
                    new_pixels.append((255, 255, 255, 0))
                    continue

                min_c = min(r, g, b)
                if min_c >= 218:
                    alpha = int(max(0, min(255, (238 - min_c) * (255 / 20))))
                    new_pixels.append((255, 255, 255, alpha))
                    continue

                luma = 0.299 * r + 0.587 * g + 0.114 * b
                strength = max(0.0, min(1.0, (238.0 - luma) / 145.0))
                alpha = int(max(45, min(255, strength * 255)))

                if y >= subtext_y_start and luma < 215:
                    new_pixels.append((pure_white[0], pure_white[1], pure_white[2], max(210, alpha)))
                else:
                    is_pinkish = (b > r + 10) or (r > 155 and b > 125 and g < 185)
                    if is_pinkish:
                        nr = min(255, int(r * 0.45 + grande_pink[0] * 0.65))
                        ng = min(255, int(g * 0.45 + grande_pink[1] * 0.65))
                        nb = min(255, int(b * 0.45 + grande_pink[2] * 0.65))
                    else:
                        nr = int(grande_pink[0] * (0.65 + 0.35 * strength))
                        ng = int(grande_pink[1] * (0.65 + 0.35 * strength))
                        nb = int(grande_pink[2] * (0.65 + 0.35 * strength))
                    new_pixels.append((min(255, nr), min(255, ng), min(255, nb), max(190, alpha)))

            img.putdata(new_pixels)
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)
            img = img.filter(ImageFilter.SHARPEN)
            img.save(clean_path, "PNG")
            return "/static/thonjet_clean.png"
        except Exception:
            pass

    fallback_static = os.path.join(STATIC_DIR, "thonjet_source.jpg")
    with open(src_file, "rb") as f_in, open(fallback_static, "wb") as f_out:
        f_out.write(f_in.read())
    return "/static/thonjet_source.jpg"


def prepare_ig_avatar_asset() -> str | None:
    clean_path = os.path.join(STATIC_DIR, "ig_avatar_clean.png")
    orig_static = os.path.join(BASE_DIR, "static")
    candidates = [
        os.path.join(BASE_DIR, "896023468_18110131619343947_7234878166828640368_n.jpg"),
        os.path.join(BASE_DIR, "ig_profile.jpg"),
        os.path.join(BASE_DIR, "ig_profile.png"),
        os.path.join(BASE_DIR, "instagram.jpg"),
        os.path.join(BASE_DIR, "instagram.png"),
        os.path.join(orig_static, "896023468_18110131619343947_7234878166828640368_n.jpg"),
        os.path.join(orig_static, "ig_profile.jpg"),
    ]

    src_file = next((p for p in candidates if os.path.exists(p)), None)
    if not src_file:
        if os.path.exists(clean_path):
            return "/static/ig_avatar_clean.png"
        return None

    latest_m = max(os.path.getmtime(src_file), SCRIPT_MTIME)
    if os.path.exists(clean_path) and os.path.getmtime(clean_path) >= latest_m:
        return "/static/ig_avatar_clean.png"

    if HAS_PIL:
        try:
            img = Image.open(src_file).convert("RGBA")
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            size = 200
            square = img.crop((left, top, left + side, top + side)).resize((size, size), Image.Resampling.LANCZOS)

            mask = Image.new("L", (size, size), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((2, 2, size - 2, size - 2), fill=255)

            out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            out.paste(square, (0, 0), mask)
            out.save(clean_path, "PNG")
            return "/static/ig_avatar_clean.png"
        except Exception:
            pass

    fallback_static = os.path.join(STATIC_DIR, "ig_avatar_source.jpg")
    with open(src_file, "rb") as f_in, open(fallback_static, "wb") as f_out:
        f_out.write(f_in.read())
    return "/static/ig_avatar_source.jpg"


def prepare_app_icons() -> None:
    """Gjeneron ikonat 192x192 dhe 512x512 për instalimin PWA në iPhone dhe Android."""
    icon_192 = os.path.join(STATIC_DIR, "icon-192.png")
    icon_512 = os.path.join(STATIC_DIR, "icon-512.png")
    if os.path.exists(icon_192) and os.path.exists(icon_512):
        return

    if HAS_PIL:
        try:
            ig_clean = os.path.join(STATIC_DIR, "ig_avatar_clean.png")
            nails_clean = os.path.join(STATIC_DIR, "thonjet_clean.png")
            for size, out_path in [(192, icon_192), (512, icon_512)]:
                img = Image.new("RGBA", (size, size), (114, 9, 29, 255))
                draw = ImageDraw.Draw(img)
                pad = int(size * 0.06)
                draw.ellipse(
                    (pad, pad, size - pad, size - pad),
                    fill=(69, 3, 15, 255),
                    outline=(242, 118, 224, 255),
                    width=max(3, size // 64),
                )

                if os.path.exists(ig_clean):
                    av = Image.open(ig_clean).convert("RGBA")
                    av_size = int(size * 0.76)
                    av = av.resize((av_size, av_size), Image.Resampling.LANCZOS)
                    pos = (size - av_size) // 2
                    img.paste(av, (pos, pos), av)
                elif os.path.exists(nails_clean):
                    nl = Image.open(nails_clean).convert("RGBA")
                    nl.thumbnail((int(size * 0.82), int(size * 0.82)), Image.Resampling.LANCZOS)
                    pos_x = (size - nl.width) // 2
                    pos_y = (size - nl.height) // 2
                    img.paste(nl, (pos_x, pos_y), nl)
                else:
                    cx, cy = size // 2, size // 2
                    r = int(size * 0.18)
                    draw.text((cx - r, cy - r // 2), "GS", fill=(255, 230, 240, 255))

                img.save(out_path, "PNG")
            return
        except Exception:
            pass

    import zlib
    import struct

    def make_solid_png(w: int, h: int, rgba: tuple[int, int, int, int]) -> bytes:
        raw_row = b"\x00" + bytes(rgba) * w
        raw_data = raw_row * h
        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        ihdr = struct.pack("!IIBBBBB", w, h, 8, 6, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw_data)) + chunk(b"IEND", b"")

    for sz, pth in [(192, icon_192), (512, icon_512)]:
        if not os.path.exists(pth):
            with open(pth, "wb") as f:
                f.write(make_solid_png(sz, sz, (114, 9, 29, 255)))


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not IS_SERVERLESS:
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

    cur.execute("PRAGMA table_info(push_subscriptions)")
    push_cols = [row["name"] for row in cur.fetchall()]
    if "role" not in push_cols:
        cur.execute("ALTER TABLE push_subscriptions ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
    if "client_contact" not in push_cols:
        cur.execute("ALTER TABLE push_subscriptions ADD COLUMN client_contact TEXT NOT NULL DEFAULT ''")

    cur.execute("SELECT COUNT(*) AS cnt FROM salon_settings")
    if cur.fetchone()["cnt"] == 0:
        cur.execute(
            """
            INSERT INTO salon_settings (id, work_start, work_end, slot_step_min, hygiene_note, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
            """,
            (
                WORK_START,
                WORK_END,
                SLOT_STEP_MIN,
                "Veglat e thonjve, qerpikëve dhe vetullave sterilizohen pas çdo klienteje.",
                datetime.now().isoformat(),
            ),
        )

    cur.execute("SELECT COUNT(*) AS cnt FROM services")
    if cur.fetchone()["cnt"] == 0:
        default_services = [
            ("Thonj", "Manikyr me Xhel (Gel Polish)", 60, 15.0, "Pastrim kutikulash, formësim dhe xhel me shkëlqim vishnje."),
            ("Thonj", "Zgjatje me Tipsa / Xhel", 90, 25.0, "Zgjatje natyrale ose të gjata me përforcim xheli."),
            ("Thonj", "Pedikyr Estetik + Xhel", 75, 22.0, "Trajtim i plotë i shputave dhe ngjyrë xheli."),
            ("Qerpikë", "Lash Lift & Tint (Ngritje Qerpikësh)", 60, 25.0, "Ngritje dhe ngjyrosje për pamje të hapur e natyrale."),
            ("Qerpikë", "Zgjatje Qerpikësh Classic / Volume", 120, 35.0, "Fije të lehta dhe të buta të përshtatura për formën e syrit."),
            ("Vetulla", "Laminim Vetullash + Formësim", 45, 20.0, "Rregullim, ushqim dhe formësim i saktë i vetullave."),
            ("Vetulla", "Dizajn, Depilim & Ngjyrosje Vetullash", 30, 12.0, "Pastrim dhe nuancim harmonik sipas fizionomisë."),
            ("Kombinim", "Paketa Grande: Manikyr Xhel + Laminim Vetullash", 105, 32.0, "Kombinim ideal për thonj të kuruar dhe vetulla të rregulluara."),
            ("Kombinim", "Kombo Qerpikë (Lash Lift) + Vetulla (Dizajn & Tint)", 90, 33.0, "Pamje e freskët e syve pa nevojë për grim të përditshëm."),
            ("Kombinim", "Total Glow: Thonj Xhel + Qerpikë + Vetulla", 150, 52.0, "Trajtim i plotë i kombinuar për thonj, qerpikë dhe vetulla."),
        ]
        cur.executemany(
            "INSERT INTO services (category, name, duration_min, price, description) VALUES (?, ?, ?, ?, ?)",
            default_services,
        )

    conn.commit()
    conn.close()


def normalize_contact_identifier(raw_val: str) -> tuple[str, str]:
    """Kthen (contact_type, normalized_value) ku contact_type është 'email' ose 'instagram'."""
    val = (raw_val or "").strip()
    if "@" in val and "." in val.split("@")[-1]:
        return "email", val.lower()
    ig_clean = val.lstrip("@").strip().lower()
    return "instagram", f"@{ig_clean}" if ig_clean else ""


def send_client_password_reset_notification(client_row: sqlite3.Row, reset_url: str) -> tuple[str, str]:
    c_type = client_row["contact_type"]
    c_val = client_row["contact_value"]
    msg_text = (
        f"Përshëndetje,\n\n"
        f"Admini i Grande Studio ka aktivizuar rivendosjen e fjalëkalimit për llogarinë tuaj ({c_val}).\n"
        f"Hapni aplikacionin Grande Studio ose klikoni linkun më poshtë për të shënuar passwordin e ri:\n"
        f"{reset_url}\n"
    )

    if c_type == "email":
        if SMTP_HOST and SMTP_USER and SMTP_PASS:
            try:
                msg = EmailMessage()
                msg["Subject"] = "Grande Studio — Shëno passwordin e ri"
                msg["From"] = SMTP_FROM
                msg["To"] = c_val
                msg.set_content(msg_text)
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                    server.starttls()
                    server.login(SMTP_USER, SMTP_PASS)
                    server.send_message(msg)
            except Exception as exc:
                print(f"[CLIENT EMAIL RESET] Gabim SMTP: {exc}")
        print(f"\n[GRANDE STUDIO - CLIENT EMAIL RESET] Për: {c_val} | Linku: {reset_url}\n")
        return f"Email ({c_val})", f"mailto:{c_val}?subject=Grande%20Studio%20-%20Sheno%20passwordin%20e%20ri&body={msg_text.replace(chr(10), '%0A')}"

    ig_handle = c_val.lstrip("@")
    ig_dm_url = f"https://www.instagram.com/{ig_handle}/"
    print(f"\n[GRANDE STUDIO - CLIENT INSTAGRAM RESET] Për: @{ig_handle} | Linku: {reset_url}\n")
    return f"Instagram (@{ig_handle})", ig_dm_url


def get_salon_settings() -> dict:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM salon_settings WHERE id = 1")
    row = cur.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "work_start": WORK_START,
        "work_end": WORK_END,
        "slot_step_min": SLOT_STEP_MIN,
        "hygiene_note": "Veglat e thonjve, qerpikëve dhe vetullave sterilizohen pas çdo klienteje.",
    }


def dispatch_web_push(subs: list[sqlite3.Row], payload_dict: dict) -> None:
    """Dërgon Web Push Notifications me prioritet të lartë (urgency: high) për zë, vibrim dhe banner në Lock Screen."""
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

    actions = []
    if appt_id:
        actions = [
            {"action": "confirm_appt", "title": "✅ Konfirmo"},
            {"action": "cancel_appt", "title": "❌ Anulo"},
        ]

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
        cur.execute(
            "SELECT id, subscription_json FROM push_subscriptions WHERE role = 'client' AND (LOWER(client_contact) = ? OR client_contact = '')",
            (clean_contact,),
        )
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


def generate_temp_password(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def send_reset_email(to_email: str, new_password: str) -> tuple[bool, str]:
    if SMTP_HOST and SMTP_USER and SMTP_PASS:
        try:
            msg = EmailMessage()
            msg["Subject"] = "Grande Studio — Reset Fjalëkalimi Admin"
            msg["From"] = SMTP_FROM
            msg["To"] = to_email
            msg.set_content(
                f"Përshëndetje,\n\n"
                f"Është kryer resetimi i fjalëkalimit për panelin e Adminit te Grande Studio.\n"
                f"Fjalëkalimi juaj i ri është: {new_password}\n\n"
                f"Pas kyçjes, rekomandohet ta ndryshoni nga seksioni i fjalëkalimit në panelin Admin.\n"
            )
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            return True, f"Fjalëkalimi i ri u dërgua në emailin {to_email}."
        except Exception as exc:
            return False, f"Gabim gjatë dërgimit të email-it: {exc}"

    print(f"\n[GRANDE STUDIO - EMAIL RESET] Për: {to_email} | Fjalëkalimi i ri: {new_password}\n")
    return True, f"Fjalëkalimi i ri u gjenerua dhe u dërgua te {to_email} (verifikoni emailin ose terminalin lokal)."


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
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def get_available_slots(appt_date: str, duration_min: int):
    settings = get_salon_settings()
    w_start = settings.get("work_start") or WORK_START
    w_end = settings.get("work_end") or WORK_END
    step_min = int(settings.get("slot_step_min") or SLOT_STEP_MIN)

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT start_time, end_time FROM appointments
        WHERE appt_date = ? AND status IN ('Konfirmuar', 'Në Pritje', 'Bllokuar')
        """,
        (appt_date,),
    )
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

    if ig_avatar_url:
        ig_top_html = f'<img src="{ig_avatar_url}" alt="Instagram Grande Studio" class="ig-banner-avatar">'
    else:
        ig_top_html = """
        <div class="ig-banner-avatar ig-fallback-badge" aria-hidden="true">IG</div>
        """

    if nails_url:
        nails_bottom_html = f'<img src="{nails_url}" alt="Grande Studio Nails" class="nails-banner-bottom">'
    else:
        nails_bottom_html = """
        <svg class="nails-banner-bottom" viewBox="0 0 210 68" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
          <text x="8" y="34" fill="#f276e0" font-size="16" font-weight="900" letter-spacing="0.5">GRANDE STUDIO</text>
          <text x="8" y="52" fill="#ffffff" font-size="9.5" font-weight="800" letter-spacing="1.1">NAILS - BEAUTY - CARE</text>
        </svg>
        """

    return f"{ig_top_html}\n{nails_bottom_html}"


def get_ig_nav_button_html() -> str:
    return f"""
    <a href="{IG_WEB_URL}"
       data-ig-app="{IG_APP_URL}"
       data-ig-web="{IG_WEB_URL}"
       onclick="openInstagramProfile(event)"
       class="btn-3d btn-ig-3d"
       title="Hap Instagramin @{IG_USERNAME}">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="2" y="2" width="20" height="20" rx="5" ry="5"></rect>
        <path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"></path>
        <line x1="17.5" y1="6.5" x2="17.51" y2="6.5"></line>
      </svg>
      <span>IG</span>
    </a>
    """


BASE_STYLE = """
<style>
  :root {
    --vishnje-dark: #45030f;
    --vishnje: #72091d;
    --vishnje-mid: #96112b;
    --vishnje-bright: #b81d3d;
    --vishnje-soft: #f9ecef;
    --bg: #fdf6f7;
    --card: #ffffff;
    --text: #261217;
    --muted: #6e4e57;
    --border: #edd3da;
    --ok: #1f7a3d;
    --warn: #b41e2d;
  }

  * {
    box-sizing: border-box;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
  }

  body {
    margin: 0;
    min-height: 100vh;
    color: var(--text);
    background-color: #26030a;
    background-image:
      radial-gradient(circle at 18% 20%, rgba(184, 29, 61, 0.42), transparent 45%),
      radial-gradient(circle at 82% 80%, rgba(150, 17, 43, 0.48), transparent 50%),
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='320' viewBox='0 0 320 320'%3E%3Cg fill='none' stroke='rgba(255,215,225,0.09)' stroke-width='1.5'%3E%3Cpath d='M35 85 C75 60, 125 60, 165 85 C125 105, 75 105, 35 85 Z'/%3E%3Cpath d='M45 73 L33 58 M72 65 L66 47 M100 63 L100 44 M128 65 L134 47 M155 73 L167 58'/%3E%3Cpath d='M30 46 C72 24, 128 24, 170 46' stroke-width='2.4'/%3E%3Crect x='215' y='165' width='18' height='75' rx='9'/%3E%3Crect x='240' y='150' width='18' height='90' rx='9'/%3E%3Crect x='265' y='160' width='18' height='80' rx='9'/%3E%3Cpath d='M217 166 C217 153, 231 153, 231 166 Z' fill='rgba(255,190,206,0.12)'/%3E%3Cpath d='M242 151 C242 137, 256 137, 256 151 Z' fill='rgba(255,190,206,0.14)'/%3E%3Cpath d='M267 161 C267 148, 281 148, 281 161 Z' fill='rgba(255,190,206,0.12)'/%3E%3C/g%3E%3C/svg%3E");
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 18px 12px;
  }

  .studio-stage-label {
    color: #ffdce4;
    font-size: 0.82rem;
    letter-spacing: 0.5px;
    margin-bottom: 10px;
    text-align: center;
    opacity: 0.9;
  }

  .iphone-chassis {
    width: 412px;
    max-width: 100%;
    height: 852px;
    max-height: calc(100vh - 64px);
    background: #141112;
    border-radius: 52px;
    padding: 13px;
    box-shadow:
      0 0 0 2px #5e2430,
      0 0 0 5px #24090f,
      0 28px 65px rgba(0, 0, 0, 0.65);
    position: relative;
    display: flex;
    flex-direction: column;
  }

  .iphone-chassis::before {
    content: "";
    position: absolute;
    left: -4px;
    top: 150px;
    width: 3px;
    height: 46px;
    background: #6b2937;
    border-radius: 3px 0 0 3px;
    box-shadow: 0 62px 0 #6b2937;
  }

  .iphone-chassis::after {
    content: "";
    position: absolute;
    right: -4px;
    top: 185px;
    width: 3px;
    height: 74px;
    background: #6b2937;
    border-radius: 0 3px 3px 0;
  }

  .iphone-screen {
    flex: 1;
    border-radius: 40px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    position: relative;
    background-color: #fdf5f7;
    background-image:
      radial-gradient(circle at top right, rgba(184, 29, 61, 0.11), transparent 48%),
      radial-gradient(circle at bottom left, rgba(114, 9, 29, 0.09), transparent 52%),
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260' viewBox='0 0 260 260'%3E%3Cg fill='none' stroke='rgba(114,9,29,0.055)' stroke-width='1.4'%3E%3Cpath d='M25 65 C55 45, 95 45, 125 65 C95 82, 55 82, 25 65 Z'/%3E%3Cpath d='M35 55 L26 43 M56 49 L51 35 M75 47 L75 32 M94 49 L99 35 M115 55 L124 43'/%3E%3Cpath d='M22 34 C55 16, 95 16, 128 34' stroke-width='2'/%3E%3Crect x='172' y='145' width='14' height='58' rx='7'/%3E%3Crect x='192' y='133' width='14' height='70' rx='7'/%3E%3Crect x='212' y='142' width='14' height='61' rx='7'/%3E%3Cpath d='M174 146 C174 136, 184 136, 184 146 Z' fill='rgba(184,29,61,0.07)'/%3E%3Cpath d='M194 134 C194 123, 204 123, 204 134 Z' fill='rgba(184,29,61,0.09)'/%3E%3Cpath d='M214 143 C214 133, 224 133, 224 143 Z' fill='rgba(184,29,61,0.07)'/%3E%3C/g%3E%3C/svg%3E");
  }

  .ios-status-bar {
    background: linear-gradient(180deg, #4f0413, #630719);
    color: #fff;
    padding: 10px 22px 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.76rem;
    font-weight: 700;
    flex-shrink: 0;
  }

  .dynamic-island {
    width: 96px;
    height: 24px;
    background: #090708;
    border-radius: 999px;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding-right: 9px;
  }

  .dynamic-island-cam {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: radial-gradient(circle, #283b63 25%, #0d1322 70%);
  }

  .app-scroll-area {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    -webkit-overflow-scrolling: touch;
  }

  .ios-home-indicator {
    height: 20px;
    background: rgba(253, 245, 247, 0.92);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .ios-home-indicator span {
    width: 124px;
    height: 4px;
    background: #3b1922;
    border-radius: 999px;
    opacity: 0.45;
  }

  @media (max-width: 520px), (display-mode: standalone) {
    body { padding: 0; background: var(--bg); display: block; }
    .studio-stage-label, .ios-status-bar, .ios-home-indicator { display: none !important; }
    .iphone-chassis { width: 100%; height: auto; min-height: 100vh; max-height: none; border-radius: 0; padding: 0; background: transparent; box-shadow: none; }
    .iphone-chassis::before, .iphone-chassis::after { display: none; }
    .iphone-screen { border-radius: 0; overflow: visible; min-height: 100vh; }
    .app-scroll-area { overflow: visible; }
    header { padding-top: calc(12px + env(safe-area-inset-top)); }
    .container { padding-bottom: calc(28px + env(safe-area-inset-bottom)); }
  }

  header {
    background: linear-gradient(145deg, var(--vishnje-bright), var(--vishnje) 55%, var(--vishnje-dark));
    color: #fff;
    padding: 12px 12px;
    position: sticky;
    top: 0;
    z-index: 30;
    box-shadow: 0 5px 0 var(--vishnje-dark), 0 10px 20px rgba(74, 4, 17, 0.25);
    border-bottom: 1px solid rgba(255, 255, 255, 0.18);
  }

  .header-inner {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
  }

  .header-mid {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .brand-title {
    margin: 0;
    font-size: 0.94rem;
    letter-spacing: 0.4px;
    font-weight: 800;
    white-space: nowrap;
    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.35);
  }

  .brand-sub {
    margin: 2px 0 0;
    font-size: 0.65rem;
    color: #ffd6df;
    white-space: nowrap;
  }

  .btn-3d {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
    border: none;
    border-radius: 11px;
    font-weight: 700;
    cursor: pointer;
    text-decoration: none;
    transition: transform 0.12s ease, box-shadow 0.12s ease, filter 0.15s ease;
    user-select: none;
  }

  .btn-3d:hover { filter: brightness(1.04); }
  .btn-3d:active { transform: translateY(3px); }

  .btn-vishnje-3d {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    padding: 10px 14px;
    font-size: 0.88rem;
    box-shadow: 0 4px 0 var(--vishnje-dark), 0 7px 14px rgba(114, 9, 29, 0.26);
  }

  .btn-vishnje-3d:active {
    box-shadow: 0 2px 0 var(--vishnje-dark);
  }

  .btn-confirm-3d {
    background: linear-gradient(180deg, #16a34a, #15803d);
    color: #fff;
    padding: 8px 10px;
    font-size: 0.76rem;
    box-shadow: 0 3px 0 #14532d, 0 5px 10px rgba(21, 128, 61, 0.25);
  }

  .btn-confirm-3d:active {
    box-shadow: 0 1px 0 #14532d;
  }

  .btn-cancel-3d {
    background: linear-gradient(180deg, #e11d48, #be123c);
    color: #fff;
    padding: 8px 10px;
    font-size: 0.76rem;
    box-shadow: 0 3px 0 #881337, 0 5px 10px rgba(190, 18, 60, 0.25);
  }

  .btn-cancel-3d:active {
    box-shadow: 0 1px 0 #881337;
  }

  .btn-ghost-3d {
    background: rgba(255, 255, 255, 0.15);
    color: #fff;
    padding: 6px 8px;
    font-size: 0.7rem;
    border: 1px solid rgba(255, 255, 255, 0.28);
    box-shadow: 0 3px 0 rgba(45, 2, 10, 0.65);
    white-space: nowrap;
  }

  .btn-ghost-3d:active {
    box-shadow: 0 1px 0 rgba(45, 2, 10, 0.65);
  }

  .btn-ig-3d {
    background: linear-gradient(135deg, #f58529, #dd2a7b 55%, #8134af);
    color: #fff;
    padding: 6px 9px;
    font-size: 0.72rem;
    border: 1px solid rgba(255, 255, 255, 0.42);
    box-shadow: 0 3px 0 rgba(45, 2, 10, 0.7);
    white-space: nowrap;
  }

  .btn-ig-3d:active {
    box-shadow: 0 1px 0 rgba(45, 2, 10, 0.7);
  }

  .container {
    padding: 13px 12px 28px;
  }

  .combo-banner {
    background: linear-gradient(135deg, #47020e 0%, #7a0a1f 55%, #a81632 100%);
    color: #fff;
    border-radius: 17px;
    padding: 13px 13px 16px;
    margin-bottom: 13px;
    position: relative;
    overflow: hidden;
    min-height: 184px;
    box-shadow: 0 5px 0 var(--vishnje-dark), 0 12px 22px rgba(114, 9, 29, 0.22);
  }

  .ig-banner-avatar {
    position: absolute;
    right: 26px;
    top: 8px;
    width: 72px;
    height: 72px;
    border-radius: 50%;
    object-fit: cover;
    border: 2px solid rgba(255, 255, 255, 0.88);
    box-shadow: 0 4px 12px rgba(15, 0, 4, 0.82);
    pointer-events: none;
    z-index: 2;
  }

  .ig-fallback-badge {
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #f58529, #dd2a7b, #8134af);
    color: #fff;
    font-weight: 900;
    font-size: 1rem;
  }

  .nails-banner-bottom {
    position: absolute;
    right: 2px;
    bottom: 18px;
    width: 158px;
    max-width: 44%;
    height: auto;
    pointer-events: none;
    opacity: 1;
    filter: drop-shadow(0 2px 6px rgba(18, 0, 4, 0.9));
    z-index: 1;
  }

  .combo-banner-content {
    position: relative;
    z-index: 3;
    max-width: 100%;
  }

  .combo-banner h3 {
    margin: 0 0 4px;
    font-size: 0.94rem;
    font-weight: 800;
    color: #ffffff;
    text-shadow: 0 2px 6px rgba(25, 1, 5, 0.92);
  }

  .combo-banner p {
    margin: 0 0 10px;
    font-size: 0.76rem;
    color: #ffffff;
    line-height: 1.38;
    max-width: 56%;
    text-shadow: 0 1px 5px rgba(25, 1, 5, 0.92);
  }

  .pillar-pills {
    position: relative;
    z-index: 4;
    display: grid;
    grid-template-columns: repeat(2, max-content);
    gap: 5px 6px;
    width: fit-content;
  }

  .pillar-pill {
    position: relative;
    z-index: 4;
    background: rgba(42, 2, 10, 0.86);
    border: 1px solid rgba(255, 255, 255, 0.55);
    padding: 4px 8px;
    border-radius: 999px;
    font-size: 0.69rem;
    font-weight: 700;
    color: #ffffff;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.42);
    white-space: nowrap;
  }

  .tabs-bar {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin-bottom: 13px;
  }

  .tab-btn {
    background: linear-gradient(180deg, #ffffff, #f8e8ec);
    color: var(--vishnje);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 9px 6px;
    font-size: 0.74rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 4px 0 #dec0c8;
    transition: transform 0.12s ease, box-shadow 0.12s ease;
    text-align: center;
  }

  .tab-btn:active { transform: translateY(3px); box-shadow: 0 1px 0 #dec0c8; }
  .tab-btn.active {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    border-color: var(--vishnje-dark);
    box-shadow: 0 4px 0 var(--vishnje-dark);
  }

  .tab-panel { display: none; }
  .tab-panel.active { display: block; animation: fadeIn 0.2s ease; }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }

  .card-3d {
    background: rgba(255, 255, 255, 0.96);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 14px;
    margin-bottom: 13px;
    box-shadow: 0 5px 0 #e6cbd2, 0 10px 20px rgba(74, 4, 17, 0.07);
  }

  h2 {
    margin: 0 0 9px;
    font-size: 0.98rem;
    color: var(--vishnje);
  }

  .cat-filter-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .cat-chip-3d {
    background: linear-gradient(180deg, #fff, #f9eaee);
    color: var(--vishnje);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 5px 9px;
    font-size: 0.74rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 3px 0 #dfc2ca;
  }

  .cat-chip-3d:active { transform: translateY(2px); box-shadow: 0 1px 0 #dfc2ca; }
  .cat-chip-3d.active {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    border-color: var(--vishnje-dark);
    box-shadow: 0 3px 0 var(--vishnje-dark);
  }

  label {
    display: block;
    font-size: 0.8rem;
    font-weight: 700;
    margin: 10px 0 4px;
    color: var(--vishnje-dark);
  }

  input, select, textarea {
    width: 100%;
    padding: 9px 10px;
    border: 1.5px solid #e2c2ca;
    border-radius: 10px;
    font-size: 0.86rem;
    background: #fffdfd;
    color: var(--text);
  }

  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: var(--vishnje-bright);
    box-shadow: 0 0 0 3px rgba(184, 29, 61, 0.14);
  }

  .slots { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .slot-3d {
    padding: 7px 10px;
    border-radius: 9px;
    border: 1px solid #dfb9c3;
    background: linear-gradient(180deg, #ffffff, #fdf0f3);
    color: var(--vishnje);
    font-size: 0.8rem;
    font-weight: 700;
    cursor: pointer;
    box-shadow: 0 3px 0 #d6aeb8;
  }

  .slot-3d:active { transform: translateY(2px); box-shadow: 0 1px 0 #d6aeb8; }
  .slot-3d.active {
    background: linear-gradient(180deg, var(--vishnje-bright), var(--vishnje));
    color: #fff;
    border-color: var(--vishnje-dark);
    box-shadow: 0 3px 0 var(--vishnje-dark);
  }

  .svc-grid { display: grid; grid-template-columns: 1fr; gap: 10px; margin-top: 9px; }
  .svc-card-3d {
    background: linear-gradient(180deg, #ffffff, #fff7f9);
    border: 1px solid var(--border);
    border-radius: 13px;
    padding: 12px;
    box-shadow: 0 4px 0 #e5c8d0;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 7px;
  }

  .svc-tag {
    display: inline-block;
    background: var(--vishnje-soft);
    color: var(--vishnje);
    border: 1px solid #e6c2cb;
    font-size: 0.68rem;
    font-weight: 800;
    padding: 2px 7px;
    border-radius: 999px;
    margin-bottom: 4px;
  }

  .svc-title { font-weight: 800; font-size: 0.88rem; color: var(--text); }
  .svc-desc { font-size: 0.78rem; color: var(--muted); line-height: 1.35; margin: 3px 0; }

  .alert {
    padding: 10px 12px;
    border-radius: 12px;
    margin-bottom: 11px;
    font-size: 0.82rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .alert-ok { background: #ebfaef; color: var(--ok); border: 1px solid #a8e4ba; }
  .alert-err { background: #fff0f2; color: var(--warn); border: 1px solid #f7bcc4; }

  .dismiss-btn {
    background: rgba(0, 0, 0, 0.08);
    color: inherit;
    border: none;
    border-radius: 8px;
    padding: 4px 8px;
    font-size: 0.72rem;
    font-weight: 800;
    cursor: pointer;
    flex-shrink: 0;
  }

  @keyframes pulseNew {
    0% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0.55); }
    70% { box-shadow: 0 0 0 8px rgba(225, 29, 72, 0); }
    100% { box-shadow: 0 0 0 0 rgba(225, 29, 72, 0); }
  }

  .new-booking-card {
    background: linear-gradient(135deg, #fff1f2 0%, #ffe4e6 100%);
    border: 2px solid #e11d48;
    border-radius: 14px;
    padding: 12px;
    margin-bottom: 10px;
    animation: pulseNew 2s infinite;
  }

  .badge-new-live {
    background: #e11d48;
    color: #fff;
    font-size: 0.68rem;
    font-weight: 900;
    padding: 3px 8px;
    border-radius: 999px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    display: inline-block;
  }

  .live-toast-banner {
    position: fixed;
    top: 14px;
    left: 50%;
    transform: translateX(-50%);
    width: 92%;
    max-width: 390px;
    background: linear-gradient(135deg, #9f1239, #e11d48);
    color: #fff;
    padding: 14px;
    border-radius: 16px;
    z-index: 9999;
    box-shadow: 0 15px 35px rgba(159, 18, 57, 0.55);
    border: 2px solid #fda4af;
    display: none;
  }

  .install-prompt-box {
    background: linear-gradient(135deg, #fff5f7, #ffe4ea);
    border: 1.5px solid #f43f5e;
    border-radius: 14px;
    padding: 10px 12px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  table { width: 100%; border-collapse: collapse; font-size: 0.76rem; }
  th, td { padding: 7px 5px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
  th { color: var(--vishnje); font-size: 0.7rem; text-transform: uppercase; }

  .row-is-new { background-color: #fff1f2 !important; }

  .badge { padding: 2px 7px; border-radius: 999px; font-size: 0.68rem; font-weight: 800; }
  .b-ok { background: #d3f9d8; color: #1f7a3d; }
  .b-wait { background: #fff3bf; color: #9c6f00; }
  .b-blk { background: #ffe3e3; color: #b41e2d; }
  .b-can { background: #f1f3f5; color: #495057; }

  .btn-sm-3d {
    padding: 6px 9px;
    border-radius: 8px;
    border: none;
    cursor: pointer;
    font-size: 0.74rem;
    font-weight: 700;
    box-shadow: 0 2px 0 rgba(0,0,0,0.18);
    margin-bottom: 3px;
  }

  .btn-sm-3d:active { transform: translateY(2px); box-shadow: none; }
  .btn-del { background: #ffe3e3; color: #b41e2d; }
  .btn-seen { background: #dcfce7; color: #166534; }

  .edit-svc-box {
    border: 1px solid var(--border);
    background: #fff9fb;
    border-radius: 12px;
    padding: 10px;
    margin-bottom: 10px;
    box-shadow: 0 3px 0 #ead0d7;
  }

  .client-list-item {
    border: 1.5px solid var(--border);
    background: #fff9fb;
    border-radius: 12px;
    padding: 10px 12px;
    margin-bottom: 8px;
    cursor: pointer;
    transition: border-color 0.15s ease, background 0.15s ease;
  }

  .client-list-item.selected {
    border-color: var(--vishnje-bright);
    background: #fff0f4;
    box-shadow: 0 3px 10px rgba(184, 29, 61, 0.14);
  }

  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(20, 4, 8, 0.78);
    z-index: 9998;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
  }

  .modal-card {
    background: #ffffff;
    color: #261217;
    border-radius: 18px;
    padding: 16px;
    max-width: 365px;
    width: 100%;
    max-height: 82vh;
    overflow-y: auto;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.45);
    border: 1.5px solid #edd3da;
  }

  .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  @media (max-width: 420px) { .row-2 { grid-template-columns: 1fr; } }
</style>
"""


def render_iphone_shell(
    title: str,
    inner_html: str,
    description: str = "Grande Studio — Rezervime online për Thonj, Qerpikë dhe Vetulla. Zgjidhni trajtimin dhe orarin tuaj me një klikim.",
    role_tag: str = "client",
    client_contact_tag: str = "",
) -> str:
    now_clock = datetime.now().strftime("%H:%M")
    base_url = request.url_root.rstrip("/") if request else ""
    canonical_url = f"{base_url}/" if base_url else "/"
    icon_url = f"{base_url}/static/icon-512.png" if base_url else "/static/icon-512.png"

    schema_json = json.dumps({
        "@context": "https://schema.org",
        "@type": "BeautySalon",
        "name": "Grande Studio",
        "description": description,
        "url": canonical_url,
        "image": icon_url,
        "sameAs": [IG_WEB_URL],
        "priceRange": "$$"
    }, ensure_ascii=False)

    push_role = "admin" if role_tag == "admin" else "client"
    safe_client_contact = (client_contact_tag or "").replace("'", "").strip().lower()

    return f"""
    <!doctype html>
    <html lang="sq">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
      <title>{title}</title>
      <meta name="description" content="{description}">
      <meta name="keywords" content="Grande Studio, thonj, qerpikë, vetulla, manikyr me xhel, lash lift, laminim vetullash, rezervim online, sallon bukurie">
      <meta name="robots" content="index, follow, max-image-preview:large">
      <link rel="canonical" href="{canonical_url}">

      <!-- Open Graph / Social & Google Preview -->
      <meta property="og:type" content="website">
      <meta property="og:site_name" content="Grande Studio">
      <meta property="og:title" content="{title}">
      <meta property="og:description" content="{description}">
      <meta property="og:url" content="{canonical_url}">
      <meta property="og:image" content="{icon_url}">

      <!-- PWA & Mobile App Support (iPhone & Samsung) -->
      <meta name="theme-color" content="#72091d">
      <meta name="mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
      <meta name="apple-mobile-web-app-title" content="Grande Studio">
      <link rel="apple-touch-icon" href="/static/icon-192.png">
      <link rel="icon" type="image/png" href="/static/icon-192.png">
      <link rel="manifest" href="/manifest.json">

      <!-- Structured Data për Google Search -->
      <script type="application/ld+json">{schema_json}</script>
      {BASE_STYLE}
    </head>
    <body>
      <div class="studio-stage-label">📱 Grande Studio — Thonj • Qerpikë • Vetulla</div>
      <div class="iphone-chassis">
        <div class="iphone-screen">
          <div class="ios-status-bar">
            <span>{now_clock}</span>
            <div class="dynamic-island"><span class="dynamic-island-cam"></span></div>
            <span>5G 100%</span>
          </div>
          <div class="app-scroll-area" id="iphoneScrollArea">
            {inner_html}
          </div>
          <div class="ios-home-indicator"><span></span></div>
        </div>
      </div>
      <script>
        const CURRENT_ROLE = '{role_tag}';
        const PUSH_SUB_ROLE = '{push_role}';
        const PUSH_CLIENT_CONTACT = '{safe_client_contact}';

        try {{
          if (CURRENT_ROLE === 'admin') {{
            localStorage.setItem('grande_studio_last_role', 'admin');
          }} else if (CURRENT_ROLE === 'client_logged_in') {{
            localStorage.setItem('grande_studio_last_role', 'client');
          }}
          if (PUSH_CLIENT_CONTACT) {{
            localStorage.setItem('gs_client_contact', PUSH_CLIENT_CONTACT);
          }}
        }} catch (e) {{}}

        function unlockAudioContext() {{
          try {{
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (!window.__gsAudioCtx) {{
              window.__gsAudioCtx = new AudioCtx();
            }}
            if (window.__gsAudioCtx.state === 'suspended') {{
              window.__gsAudioCtx.resume().catch(() => {{}});
            }}
          }} catch (e) {{}}
        }}

        function playAutoAlertSound() {{
          try {{
            unlockAudioContext();
            const ctx = window.__gsAudioCtx || new (window.AudioContext || window.webkitAudioContext)();
            const notes = [587.33, 880.00, 1174.66];
            notes.forEach((freq, idx) => {{
              const osc = ctx.createOscillator();
              const gain = ctx.createGain();
              osc.type = 'sine';
              osc.frequency.setValueAtTime(freq, ctx.currentTime + idx * 0.14);
              gain.gain.setValueAtTime(0.001, ctx.currentTime + idx * 0.14);
              gain.gain.exponentialRampToValueAtTime(0.45, ctx.currentTime + idx * 0.14 + 0.03);
              gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + idx * 0.14 + 0.45);
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.start(ctx.currentTime + idx * 0.14);
              osc.stop(ctx.currentTime + idx * 0.14 + 0.46);
            }});
          }} catch (e) {{}}
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

        async function autoEnableNotificationsAndPush(overrideContact) {{
          try {{
            if (!('Notification' in window)) return;
            if (Notification.permission === 'default') {{
              await Notification.requestPermission().catch(() => {{}});
            }}
            if (Notification.permission === 'granted' && 'serviceWorker' in navigator && 'PushManager' in window) {{
              const reg = await navigator.serviceWorker.ready;
              const keyRes = await fetch('/api/push/public-key');
              const keyData = await keyRes.json();
              if (keyData.publicKey) {{
                const existingSub = await reg.pushManager.getSubscription();
                const sub = existingSub || await reg.pushManager.subscribe({{
                  userVisibleOnly: true,
                  applicationServerKey: urlBase64ToUint8Array(keyData.publicKey)
                }});
                const subJson = sub.toJSON ? sub.toJSON() : JSON.parse(JSON.stringify(sub));
                const activeContact = (overrideContact || PUSH_CLIENT_CONTACT || localStorage.getItem('gs_client_contact') || '').toLowerCase();
                if (activeContact) {{
                  try {{ localStorage.setItem('gs_client_contact', activeContact); }} catch (e) {{}}
                }}
                subJson.role = PUSH_SUB_ROLE;
                subJson.client_contact = activeContact;
                await fetch('/api/push/subscribe', {{
                  method: 'POST',
                  headers: {{ 'Content-Type': 'application/json' }},
                  body: JSON.stringify(subJson)
                }}).catch(() => {{}});
              }}
            }}
          }} catch (e) {{}}
        }}

        if ('serviceWorker' in navigator) {{
          window.addEventListener('load', () => {{
            navigator.serviceWorker.register('/sw.js', {{ scope: '/' }}).then(() => {{
              autoEnableNotificationsAndPush();
            }}).catch(() => {{}});
          }});

          navigator.serviceWorker.addEventListener('message', (event) => {{
            if (event.data && event.data.type === 'GS_PUSH_RECEIVED') {{
              playAutoAlertSound();
              if (navigator.vibrate) navigator.vibrate([300, 150, 300, 150, 400]);
            }}
          }});
        }}

        window.addEventListener('DOMContentLoaded', () => {{
          autoEnableNotificationsAndPush();
        }});

        function openInstagramProfile(ev) {{
          if (ev) ev.preventDefault();
          const el = ev && ev.currentTarget ? ev.currentTarget : null;
          const appUrl = el ? el.getAttribute('data-ig-app') : '{IG_APP_URL}';
          const webUrl = el ? el.getAttribute('data-ig-web') : '{IG_WEB_URL}';
          const start = Date.now();
          window.location.href = appUrl;
          setTimeout(function() {{
            if (Date.now() - start < 1600) {{
              window.open(webUrl, '_blank', 'noopener,noreferrer') || (window.location.href = webUrl);
            }}
          }}, 650);
        }}

        document.addEventListener('pointerdown', function() {{
          unlockAudioContext();
          autoEnableNotificationsAndPush();
        }}, {{ once: true }});
      </script>
    </body>
    </html>
    """


@app.after_request
def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.route("/favicon.ico")
def favicon():
    prepare_app_icons()
    return send_from_directory(STATIC_DIR, "icon-192.png", mimetype="image/png")


@app.route("/robots.txt")
def robots_txt():
    base_url = request.url_root.rstrip("/")
    txt = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /api/\n"
        f"Sitemap: {base_url}/sitemap.xml\n"
    )
    return Response(txt, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    base_url = request.url_root.rstrip("/")
    today = datetime.now().strftime("%Y-%m-%d")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base_url}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return Response(xml, mimetype="application/xml")


@app.route("/static/<path:filename>")
def static_files(filename):
    if os.path.exists(os.path.join(STATIC_DIR, filename)):
        return send_from_directory(STATIC_DIR, filename)
    return send_from_directory(os.path.join(BASE_DIR, "static"), filename)


@app.route("/manifest.json")
def manifest():
    prepare_app_icons()
    icons = [
        {
            "src": "/static/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any maskable"
        },
        {
            "src": "/static/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any maskable"
        }
    ]

    resp = jsonify({
        "id": "/",
        "name": "Grande Studio — Thonj, Qerpikë & Vetulla",
        "short_name": "Grande Studio",
        "description": "Rezervime online për Thonj, Qerpikë dhe Vetulla te Grande Studio.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#fdf6f7",
        "theme_color": "#72091d",
        "icons": icons,
    })
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/sw.js")
def service_worker():
    js = """
    const CACHE_NAME = 'grande-studio-v7';
    const CORE_ASSETS = [
      '/',
      '/manifest.json',
      '/static/icon-192.png',
      '/static/icon-512.png'
    ];

    self.addEventListener('install', event => {
      event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(CORE_ASSETS).catch(() => {}))
      );
      self.skipWaiting();
    });

    self.addEventListener('activate', event => {
      event.waitUntil(
        caches.keys().then(keys =>
          Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
      );
      self.clients.claim();
    });

    self.addEventListener('pushsubscriptionchange', event => {
      event.waitUntil(
        fetch('/api/push/public-key')
          .then(r => r.json())
          .then(keyData => {
            if (!keyData.publicKey) return null;
            const padding = '='.repeat((4 - keyData.publicKey.length % 4) % 4);
            const b64 = (keyData.publicKey + padding).replace(/-/g, '+').replace(/_/g, '/');
            const raw = atob(b64);
            const arr = new Uint8Array(raw.length);
            for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
            return self.registration.pushManager.subscribe({
              userVisibleOnly: true,
              applicationServerKey: arr
            });
          })
          .then(newSub => {
            if (!newSub) return null;
            return fetch('/api/push/subscribe', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(newSub.toJSON ? newSub.toJSON() : newSub)
            });
          })
          .catch(() => {})
      );
    });

    self.addEventListener('fetch', event => {
      if (event.request.method !== 'GET') return;
      const url = new URL(event.request.url);
      if (url.origin !== self.location.origin) return;

      if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin')) {
        event.respondWith(fetch(event.request));
        return;
      }

      event.respondWith(
        fetch(event.request)
          .then(resp => {
            if (resp && resp.status === 200) {
              const copy = resp.clone();
              caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
            }
            return resp;
          })
          .catch(() => caches.match(event.request).then(cached => cached || caches.match('/')))
      );
    });

    self.addEventListener('push', event => {
      let data = {
        title: '🔔 Rezervim i Ri — Grande Studio',
        body: 'Një klient ka bërë një rezervim të ri!',
        url: '/admin',
        appt_id: null,
        tag: 'grande-studio-booking',
        actions: []
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
        vibrate: [400, 150, 400, 150, 500],
        silent: false,
        tag: data.tag || 'grande-studio-booking',
        renotify: true,
        requireInteraction: true,
        actions: Array.isArray(data.actions) ? data.actions : [],
        data: {
          url: data.url || '/admin',
          appt_id: data.appt_id || null
        }
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
      const action = event.action;
      const notifData = event.notification.data || {};
      const apptId = notifData.appt_id;
      const targetUrl = notifData.url || '/admin';
      event.notification.close();

      if ((action === 'confirm_appt' || action === 'cancel_appt') && apptId) {
        const newStatus = action === 'confirm_appt' ? 'Konfirmuar' : 'Anuluar';
        event.waitUntil(
          fetch('/api/push/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ appt_id: apptId, status: newStatus })
          }).then(() => {
            return self.registration.showNotification(
              newStatus === 'Konfirmuar' ? '✅ Termini u Konfirmua' : '❌ Termini u Anulua',
              {
                body: `Statusi i terminit #${apptId} u përditësua në: ${newStatus}.`,
                icon: '/static/icon-192.png',
                badge: '/static/icon-192.png',
                vibrate: [200, 100, 200],
                tag: 'grande-studio-action-feedback'
              }
            );
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
    resp = Response(js, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/push/public-key")
def api_push_public_key():
    keys = get_vapid_keys()
    return jsonify({"publicKey": keys.get("public_key", ""), "supported": HAS_WEBPUSH})


@app.route("/api/push/subscribe", methods=["POST"])
def api_push_subscribe():
    data = request.get_json(silent=True)
    if not data or not data.get("endpoint"):
        return jsonify({"ok": False, "error": "Abonim i pavlefshëm"}), 400

    role = "admin" if (session.get("admin_logged_in") or data.get("role") == "admin") else "client"
    client_contact = (session.get("client_contact") or data.get("client_contact") or "").strip().lower()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO push_subscriptions (endpoint, subscription_json, role, client_contact, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            subscription_json = excluded.subscription_json,
            role = excluded.role,
            client_contact = CASE WHEN excluded.client_contact != '' THEN excluded.client_contact ELSE push_subscriptions.client_contact END
        """,
        (data["endpoint"], json.dumps(data), role, client_contact, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "role": role})


@app.route("/api/push/action", methods=["POST"])
def api_push_action():
    """Ekzekuton konfirmimin ose anulimin direkt nga butonat e njoftimit në Lock Screen ose nga paneli."""
    data = request.get_json(silent=True) or {}
    appt_id = data.get("appt_id")
    new_status = data.get("status")

    if not appt_id or new_status not in ("Konfirmuar", "Anuluar"):
        return jsonify({"ok": False, "error": "Të dhëna të pavlefshme"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.*, s.name AS service_name
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE a.id = ?
        """,
        (appt_id,),
    )
    appt = cur.fetchone()
    if not appt:
        conn.close()
        return jsonify({"ok": False, "error": "Termini nuk u gjet"}), 404

    cur.execute(
        "UPDATE appointments SET status = ?, is_new = 0, client_notified = 0 WHERE id = ?",
        (new_status, appt_id),
    )
    conn.commit()
    conn.close()

    svc_title = appt["service_name"] or "Trajtim"
    if new_status == "Konfirmuar":
        send_push_to_client(
            appt["client_contact"] or "",
            title="✅ Termini u Konfirmua — Grande Studio",
            body=f"Termini juaj për {svc_title} më {appt['appt_date']} në ora {appt['start_time']} u konfirmua!",
            url="/",
            appt_id=int(appt_id),
        )
    else:
        send_push_to_client(
            appt["client_contact"] or "",
            title="❌ Termini u Anulua — Grande Studio",
            body=f"Termini juaj për {svc_title} më {appt['appt_date']} në ora {appt['start_time']} u anulua.",
            url="/",
            appt_id=int(appt_id),
        )

    return jsonify({"ok": True, "status": new_status})


@app.route("/api/client/status-updates")
def api_client_status_updates():
    """Kontrollon nëse ka përditësim statusi për klientin (Konfirmuar / Anuluar) për zë, vibrim dhe banner."""
    contact = (session.get("client_contact") or request.args.get("contact") or "").strip().lower()
    if not contact:
        return jsonify({"updates": []})

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.id, a.appt_date, a.start_time, a.status, s.name AS service_name
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE LOWER(a.client_contact) = ? AND a.client_notified = 0 AND a.status IN ('Konfirmuar', 'Anuluar')
        ORDER BY a.id DESC
        """,
        (contact,),
    )
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
    cur.execute(
        """
        SELECT a.id, a.client_name, a.client_phone, a.appt_date, a.start_time, a.end_time, a.notes, a.status, a.created_at,
               s.name AS service_name, s.category
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE a.is_new = 1 AND a.status IN ('Në Pritje', 'Konfirmuar')
        ORDER BY a.id DESC
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"count": len(rows), "bookings": rows})


@app.route("/api/admin/auto-save-settings", methods=["POST"])
@admin_required
def api_admin_auto_save_settings():
    data = request.get_json(silent=True) or {}
    w_start = (data.get("work_start") or WORK_START).strip()
    w_end = (data.get("work_end") or WORK_END).strip()
    try:
        step_min = int(data.get("slot_step_min") or SLOT_STEP_MIN)
    except Exception:
        step_min = SLOT_STEP_MIN
    hygiene_note = (data.get("hygiene_note") or "").strip()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE salon_settings
        SET work_start = ?, work_end = ?, slot_step_min = ?, hygiene_note = ?, updated_at = ?
        WHERE id = 1
        """,
        (w_start, w_end, max(15, step_min), hygiene_note, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "saved_at": datetime.now().strftime("%H:%M:%S")})


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

    slots = get_available_slots(appt_date, row["duration_min"])
    return jsonify({"slots": slots, "duration_min": row["duration_min"]})


@app.route("/", methods=["GET", "POST"])
def index():
    init_db()
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_panel"))

    prepare_clean_nails_asset()
    prepare_ig_avatar_asset()
    prepare_app_icons()
    banner_art_html = get_banner_artwork_html()
    ig_nav_html = get_ig_nav_button_html()
    settings = get_salon_settings()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM services ORDER BY CASE category WHEN 'Kombinim' THEN 1 WHEN 'Thonj' THEN 2 WHEN 'Qerpikë' THEN 3 ELSE 4 END, id"
    )
    services = cur.fetchall()

    msg = None
    msg_type = "ok"
    active_tab = request.form.get("active_tab") or request.args.get("tab") or "book"
    if active_tab not in ("book", "install", "info"):
        active_tab = "book"

    reset_token_param = (request.args.get("reset_token") or "").strip()
    pending_reset_client = None

    if reset_token_param:
        cur.execute("SELECT * FROM clients WHERE reset_pending = 1 AND reset_token = ?", (reset_token_param,))
        pending_reset_client = cur.fetchone()
    elif session.get("client_id"):
        cur.execute("SELECT * FROM clients WHERE id = ? AND reset_pending = 1", (session["client_id"],))
        pending_reset_client = cur.fetchone()

    today_str = datetime.now().strftime("%Y-%m-%d")

    if request.method == "POST":
        post_mode = request.form.get("post_mode", "booking")

        if post_mode == "client_auth":
            raw_contact = request.form.get("auth_contact", "").strip()
            raw_password = request.form.get("auth_password", "")
            full_name_in = request.form.get("auth_full_name", "").strip()
            phone_in = request.form.get("auth_phone", "").strip()
            c_type, c_val = normalize_contact_identifier(raw_contact)

            if not c_val or len(raw_password) < 4:
                msg = "Ju lutem shkruani Email-in ose Instagram-in dhe një password (të paktën 4 karaktere)."
                msg_type = "err"
            else:
                cur.execute("SELECT * FROM clients WHERE contact_value = ?", (c_val,))
                existing_client = cur.fetchone()
                if existing_client:
                    if existing_client["reset_pending"] == 1:
                        pending_reset_client = existing_client
                        msg = "Admini ka dërguar rivendosje të passwordit. Ju lutem shënoni passwordin e ri."
                        msg_type = "ok"
                    elif check_password_hash(existing_client["password_hash"], raw_password):
                        session.permanent = True
                        session["client_logged_in"] = True
                        session["client_id"] = existing_client["id"]
                        session["client_contact"] = existing_client["contact_value"]
                        session["client_contact_type"] = existing_client["contact_type"]
                        session["client_name"] = existing_client["full_name"] or existing_client["contact_value"]
                        session["client_phone"] = existing_client["client_phone"] or ""
                        msg = f"Mirësevini, {existing_client['contact_value']}! Jeni të kyçur në llogarinë tuaj."
                        msg_type = "ok"
                    else:
                        msg = "Passwordi është i pasaktë. Nëse e keni harruar, kontaktoni adminin për 'Refresko Password'."
                        msg_type = "err"
                else:
                    now_iso = datetime.now().isoformat()
                    display_name = full_name_in or c_val
                    cur.execute(
                        """
                        INSERT INTO clients (full_name, contact_type, contact_value, client_phone, password_hash, reset_pending, reset_token, reset_sent_via, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 0, '', '', ?, ?)
                        """,
                        (display_name, c_type, c_val, phone_in, generate_password_hash(raw_password), now_iso, now_iso),
                    )
                    new_client_id = cur.lastrowid
                    conn.commit()
                    session.permanent = True
                    session["client_logged_in"] = True
                    session["client_id"] = new_client_id
                    session["client_contact"] = c_val
                    session["client_contact_type"] = c_type
                    session["client_name"] = display_name
                    session["client_phone"] = phone_in
                    msg = f"Llogaria u regjistrua dhe u kyçët me {c_val}!"
                    msg_type = "ok"
            active_tab = "book"

        elif post_mode == "client_set_new_password":
            target_client_id = request.form.get("reset_client_id", type=int)
            target_token = request.form.get("reset_token", "").strip()
            new_pwd = request.form.get("new_client_password", "")
            conf_pwd = request.form.get("confirm_client_password", "")

            cur.execute("SELECT * FROM clients WHERE id = ? AND reset_pending = 1", (target_client_id,))
            target_cli = cur.fetchone()
            if not target_cli or (target_cli["reset_token"] and target_token != target_cli["reset_token"]):
                msg = "Kërkesa për ndërrimin e passwordit nuk është e vlefshme."
                msg_type = "err"
            elif len(new_pwd) < 4:
                pending_reset_client = target_cli
                msg = "Passwordi i ri duhet të ketë të paktën 4 karaktere."
                msg_type = "err"
            elif new_pwd != conf_pwd:
                pending_reset_client = target_cli
                msg = "Passwordet nuk përputhen. Shënoni sërish passwordin e ri."
                msg_type = "err"
            else:
                cur.execute(
                    """
                    UPDATE clients
                    SET password_hash = ?, reset_pending = 0, reset_token = '', updated_at = ?
                    WHERE id = ?
                    """,
                    (generate_password_hash(new_pwd), datetime.now().isoformat(), target_cli["id"]),
                )
                conn.commit()
                pending_reset_client = None
                session.permanent = True
                session["client_logged_in"] = True
                session["client_id"] = target_cli["id"]
                session["client_contact"] = target_cli["contact_value"]
                session["client_contact_type"] = target_cli["contact_type"]
                session["client_name"] = target_cli["full_name"] or target_cli["contact_value"]
                session["client_phone"] = target_cli["client_phone"] or ""
                msg = f"Passwordi i ri u ruajt dhe jeni kyçur automatikisht si {target_cli['contact_value']}!"
                msg_type = "ok"
            active_tab = "book"

        else:
            raw_contact_book = (request.form.get("client_contact") or session.get("client_contact") or "").strip()
            _, norm_contact_book = normalize_contact_identifier(raw_contact_book)
            client_name = (request.form.get("client_name") or session.get("client_name") or norm_contact_book).strip()
            client_phone = (request.form.get("client_phone") or session.get("client_phone") or "").strip()
            service_id = request.form.get("service_id", type=int)
            appt_date = request.form.get("appt_date", "").strip()
            start_time = request.form.get("start_time", "").strip()
            notes = request.form.get("notes", "").strip()

            cur.execute("SELECT * FROM services WHERE id = ?", (service_id,))
            svc = cur.fetchone()

            if not (norm_contact_book or client_name) or not client_phone or not svc or not appt_date or not start_time:
                msg = "Ju lutem plotësoni të gjitha fushat e detyrueshme dhe zgjidhni një orar."
                msg_type = "err"
                active_tab = "book"
            else:
                valid_slots = get_available_slots(appt_date, svc["duration_min"])
                if start_time not in valid_slots:
                    msg = "Ky orar nuk është më i lirë ose nuk mjafton për kohëzgjatjen e shërbimit."
                    msg_type = "err"
                    active_tab = "book"
                else:
                    end_time = min_to_time(time_to_min(start_time) + svc["duration_min"])
                    display_client_label = f"{client_name} ({norm_contact_book})" if norm_contact_book and norm_contact_book not in client_name else client_name
                    cur.execute(
                        """
                        INSERT INTO appointments
                        (client_name, client_phone, client_contact, service_id, appt_date, start_time, end_time, notes, status, is_new, client_notified, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Në Pritje', 1, 1, ?)
                        """,
                        (
                            display_client_label,
                            client_phone,
                            norm_contact_book,
                            service_id,
                            appt_date,
                            start_time,
                            end_time,
                            notes,
                            datetime.now().isoformat(),
                        ),
                    )
                    new_appt_id = cur.lastrowid
                    if norm_contact_book:
                        session["client_contact"] = norm_contact_book
                    if session.get("client_id"):
                        cur.execute(
                            "UPDATE clients SET full_name = ?, client_phone = ?, updated_at = ? WHERE id = ?",
                            (client_name, client_phone, datetime.now().isoformat(), session["client_id"]),
                        )
                    conn.commit()

                    send_push_to_admins(
                        title=f"🔔 Kërkesë për Termin: {display_client_label}",
                        body=f"{svc['name']} • Data: {appt_date} në {start_time} • Tel: {client_phone}",
                        url="/admin",
                        appt_id=new_appt_id,
                    )

                    msg = f"Kërkesa për termin u dërgua për {display_client_label} më {appt_date} në ora {start_time} ({svc['name']}). Do të njoftoheni sapo admini ta konfirmojë!"
                    msg_type = "ok"
                    active_tab = "book"

    saved_client_contact = session.get("client_contact", "")
    saved_client_name = session.get("client_name", "")
    saved_client_phone = session.get("client_phone", "")
    is_client_logged_in = bool(session.get("client_logged_in") and saved_client_contact)
    role_tag = "client_logged_in" if is_client_logged_in else "client"

    client_appts = []
    if saved_client_contact:
        cur.execute(
            """
            SELECT a.*, s.name AS service_name, s.price
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id
            WHERE LOWER(a.client_contact) = ?
            ORDER BY a.appt_date DESC, a.start_time DESC
            LIMIT 5
            """,
            (saved_client_contact.lower(),),
        )
        client_appts = cur.fetchall()

    conn.close()

    service_options = "\n".join(
        f'<option value="{s["id"]}" data-cat="{html.escape(s["category"], quote=True)}" data-dur="{s["duration_min"]}">[{html.escape(s["category"])}] {html.escape(s["name"])} — {s["duration_min"]} min ({s["price"]:.0f}€)</option>'
        for s in services
    )

    alert_html = ""
    if msg:
        cls = "alert-ok" if msg_type == "ok" else "alert-err"
        alert_html = f"""
        <div class="alert {cls}" id="clientAlertBox">
          <span>{html.escape(msg)}</span>
          <button type="button" class="dismiss-btn" onclick="this.parentElement.remove()">Hiq ✕</button>
        </div>
        """

    client_appts_html = ""
    if client_appts:
        items = ""
        for ca in client_appts:
            st = ca["status"]
            b_cls = "b-ok" if st == "Konfirmuar" else ("b-can" if st == "Anuluar" else "b-wait")
            items += f"""
            <div style="display:flex; justify-content:space-between; align-items:center; padding:7px 0; border-bottom:1px solid var(--border); font-size:0.78rem;">
              <div>
                <b>{html.escape(ca['service_name'] or 'Trajtim')}</b><br>
                <span style="color:var(--muted);">📅 {html.escape(ca['appt_date'])} • ⏰ {html.escape(ca['start_time'])}</span>
              </div>
              <span class="badge {b_cls}">{html.escape(st)}</span>
            </div>
            """
        client_appts_html = f"""
        <div class="card-3d" style="padding:11px 12px;">
          <h2 style="font-size:0.9rem; margin-bottom:6px;">📋 Terminet e Mia</h2>
          {items}
        </div>
        """

    new_password_modal_html = ""
    if pending_reset_client:
        new_password_modal_html = f"""
        <div class="modal-overlay" id="clientNewPasswordModal">
          <div class="modal-card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
              <b style="color:#72091d; font-size:1rem;">🔐 Shëno passwordin e ri</b>
            </div>
            <p style="margin:0 0 10px; font-size:0.78rem; color:var(--muted); line-height:1.45;">
              Llogaria: <b>{html.escape(pending_reset_client['contact_value'])}</b><br>
              Shënoni passwordin e ri për të hyrë në llogarinë tuaj.
            </p>
            <form method="POST">
              <input type="hidden" name="post_mode" value="client_set_new_password">
              <input type="hidden" name="reset_client_id" value="{pending_reset_client['id']}">
              <input type="hidden" name="reset_token" value="{html.escape(pending_reset_client['reset_token'], quote=True)}">
              <label for="new_client_password">Shëno passwordin e ri</label>
              <input type="password" name="new_client_password" id="new_client_password" placeholder="Shkruani passwordin e ri..." required autofocus>
              <label for="confirm_client_password">Konfirmo passwordin e ri</label>
              <input type="password" name="confirm_client_password" id="confirm_client_password" placeholder="Përsëritni passwordin e ri..." required>
              <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
                Ruaj Passwordin e Ri &amp; Kyçu
              </button>
            </form>
          </div>
        </div>
        """

    client_login_card = ""
    if is_client_logged_in:
        client_login_card = f"""
        <div class="card-3d" style="padding:10px 12px; background:#fff9fb;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div style="font-size:0.78rem; color:var(--vishnje-dark);">
              <b>👤 Klienti i kyçur:</b> {html.escape(saved_client_contact)}
            </div>
            <a href="{url_for('client_logout')}" style="font-size:0.72rem; color:var(--warn); font-weight:700; text-decoration:none;">Dil</a>
          </div>
          <div style="font-size:0.7rem; color:var(--muted); margin-top:2px;">
            Njoftimet me zë, vibrim dhe banner janë aktive për statusin e terminit tuaj.
          </div>
        </div>
        """
    else:
        client_login_card = f"""
        <div class="card-3d" style="padding:12px;">
          <h2>👤 Kyçja / Regjistrimi i Klientit</h2>
          <p style="margin:0 0 8px; font-size:0.76rem; color:var(--muted);">
            Kyçuni me <b>Email</b> ose <b>Instagram</b> dhe <b>Password</b>. Pas kyçjes, merrni njoftime automatike kur termini konfirmohet.
          </p>
          <form method="POST">
            <input type="hidden" name="post_mode" value="client_auth">
            <input type="hidden" name="active_tab" value="book">

            <label for="auth_contact">Email ose Instagram</label>
            <input type="text" name="auth_contact" id="auth_contact" placeholder="p.sh. sara@email.com ose @sara.beauty" required>

            <label for="auth_password">Password</label>
            <input type="password" name="auth_password" id="auth_password" placeholder="Shkruani passwordin..." required>

            <div class="row-2">
              <div>
                <label for="auth_full_name">Emri (opsionale)</label>
                <input type="text" name="auth_full_name" id="auth_full_name" placeholder="p.sh. Sara Berisha">
              </div>
              <div>
                <label for="auth_phone">Telefoni (opsionale)</label>
                <input type="tel" name="auth_phone" id="auth_phone" placeholder="+383 44 ...">
              </div>
            </div>

            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:11px; padding:9px 12px; font-size:0.84rem;">
              Kyçu / Regjistrohu
            </button>
          </form>
        </div>
        """

    w_start = settings.get("work_start") or WORK_START
    w_end = settings.get("work_end") or WORK_END
    hygiene_note = settings.get("hygiene_note") or "Veglat e thonjve, qerpikëve dhe vetullave sterilizohen pas çdo klienteje."

    inner = f"""
      {new_password_modal_html}

      <div id="clientLiveToastBanner" class="live-toast-banner">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <b style="font-size:0.95rem;" id="clientLiveToastTitle">🔔 NJOFTIM — GRANDE STUDIO</b>
          <button type="button" onclick="document.getElementById('clientLiveToastBanner').style.display='none'" style="background:rgba(255,255,255,0.22); border:none; color:#fff; font-weight:900; font-size:0.8rem; border-radius:8px; padding:4px 9px; cursor:pointer;">Hiq ✕</button>
        </div>
        <div id="clientLiveToastBody" style="font-size:0.82rem; margin-top:6px; line-height:1.4;"></div>
      </div>

      <div id="installGuideModal" style="display:none; position:fixed; inset:0; background:rgba(20,4,8,0.72); z-index:9998; align-items:center; justify-content:center; padding:16px;">
        <div style="background:#fff; color:#261217; border-radius:18px; padding:16px; max-width:350px; width:100%; box-shadow:0 16px 40px rgba(0,0,0,0.45); border:1.5px solid #edd3da;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <b style="color:#72091d; font-size:0.95rem;" id="installModalTitle">📲 Instalo Grande Studio</b>
            <button type="button" onclick="closeInstallModal()" style="background:none; border:none; font-size:1.1rem; font-weight:800; color:#72091d; cursor:pointer;">✕</button>
          </div>
          <div id="installModalBody" style="font-size:0.8rem; line-height:1.55; background:#fdf5f7; padding:10px; border-radius:12px; border:1px solid #edd3da;"></div>
          <button type="button" onclick="closeInstallModal()" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px; padding:9px;">
            E kuptova
          </button>
        </div>
      </div>

      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">Thonj • Qerpikë • Vetulla</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('admin_login')}" class="btn-3d btn-ghost-3d">🔒 Admin</a>
            <a href="{url_for('admin_reset_password')}" class="btn-3d btn-ghost-3d">🔑 Reset password</a>
          </div>
        </div>
      </header>

      <div class="container">
        {alert_html}

        <div class="install-prompt-box" id="pwaInstallBanner" style="display:flex;">
          <div style="font-size:0.76rem; color:var(--vishnje-dark); line-height:1.3;">
            <b>📲 Instalo Grande Studio</b><br>
            Shtoje në ekranin kryesor (Wi-Fi ose 5G)!
          </div>
          <button type="button" id="pwaInstallBtn" class="btn-3d btn-vishnje-3d" style="padding:7px 11px; font-size:0.75rem; white-space:nowrap;">
            Instalo Tani
          </button>
        </div>

        <div class="combo-banner">
          {banner_art_html}
          <div class="combo-banner-content">
            <h3>✨ Thonj, Qerpikë &amp; Vetulla</h3>
            <p>Trajtime individuale ose paketa të kombinuara në një termin të vetëm te <b>Grande Studio</b>.</p>
            <div class="pillar-pills">
              <span class="pillar-pill">💅 Thonj</span>
              <span class="pillar-pill">👁️ Qerpikë</span>
              <span class="pillar-pill">🪄 Vetulla</span>
              <span class="pillar-pill">💎 Kombinim</span>
            </div>
          </div>
        </div>

        <div class="tabs-bar" role="tablist">
          <button type="button" class="tab-btn {'active' if active_tab == 'book' else ''}" data-tab="book" onclick="switchTab('book')">
            📅 Rezervo Termin
          </button>
          <button type="button" class="tab-btn {'active' if active_tab == 'install' else ''}" data-tab="install" onclick="switchTab('install')">
            📱 iPhone &amp; Samsung
          </button>
          <button type="button" class="tab-btn {'active' if active_tab == 'info' else ''}" data-tab="info" onclick="switchTab('info')">
            🕰️ Orari &amp; Info
          </button>
        </div>

        <div id="tab-book" class="tab-panel {'active' if active_tab == 'book' else ''}">
          {client_login_card}
          {client_appts_html}

          <div class="card-3d">
            <h2>Rezervo Terminin Tënd</h2>

            <label>Filtro sipas Zonës</label>
            <div class="cat-filter-row" id="categoryFilterRow">
              <button type="button" class="cat-chip-3d active" data-cat="ALL" onclick="filterServicesByCat('ALL')">Të gjitha</button>
              <button type="button" class="cat-chip-3d" data-cat="Kombinim" onclick="filterServicesByCat('Kombinim')">Kombinim</button>
              <button type="button" class="cat-chip-3d" data-cat="Thonj" onclick="filterServicesByCat('Thonj')">Thonj</button>
              <button type="button" class="cat-chip-3d" data-cat="Qerpikë" onclick="filterServicesByCat('Qerpikë')">Qerpikë</button>
              <button type="button" class="cat-chip-3d" data-cat="Vetulla" onclick="filterServicesByCat('Vetulla')">Vetulla</button>
            </div>

            <form method="POST" id="bookingForm">
              <input type="hidden" name="post_mode" value="booking">
              <input type="hidden" name="active_tab" value="book">

              <label for="service_id">1. Zgjidh Shërbimin / Paketën</label>
              <select name="service_id" id="service_id" required>
                {service_options}
              </select>

              <label for="appt_date">2. Zgjidh Datën</label>
              <input type="date" name="appt_date" id="appt_date" value="{today_str}" min="{today_str}" required>

              <label>3. Zgjidh Orarin e Lirë</label>
              <div id="slotsContainer" class="slots">Duke ngarkuar oraret...</div>
              <input type="hidden" name="start_time" id="start_time" required>

              <div class="row-2">
                <div>
                  <label for="client_contact">Email ose Instagram</label>
                  <input type="text" name="client_contact" id="client_contact" value="{html.escape(saved_client_contact, quote=True)}" placeholder="p.sh. sara@email.com ose @sara.beauty" required>
                </div>
                <div>
                  <label for="client_phone">Numri i Telefonit</label>
                  <input type="tel" name="client_phone" id="client_phone" value="{html.escape(saved_client_phone, quote=True)}" placeholder="p.sh. +383 44 ..." required>
                </div>
              </div>

              <label for="notes">Detaje për Thonj, Qerpikë ose Vetulla</label>
              <textarea name="notes" id="notes" rows="2" placeholder="p.sh. thonj mandorle vishnje, lash lift, laminim vetullash..."></textarea>

              <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">
                ✨ Dërgo Kërkesën për Termin
              </button>
            </form>
          </div>
        </div>

        <div id="tab-install" class="tab-panel {'active' if active_tab == 'install' else ''}">
          <div class="card-3d">
            <h2>Instalimi në iPhone &amp; Samsung</h2>
            <div class="svc-grid">
              <div class="svc-card-3d">
                <div>
                  <span class="svc-tag">iOS / iPhone</span>
                  <div class="svc-title">Instalimi në Safari</div>
                  <div class="svc-desc">
                    1. Hap linkun e <b>Grande Studio</b> në <b>Safari</b>.<br>
                    2. Shtyp ikonën <b>Share</b> (katrori me shigjetë).<br>
                    3. Zgjidh <b>Add to Home Screen</b> dhe shtyp <b>Add</b>.
                  </div>
                </div>
              </div>
              <div class="svc-card-3d">
                <div>
                  <span class="svc-tag">Android / Samsung</span>
                  <div class="svc-title">Instalimi në Chrome / Samsung Internet</div>
                  <div class="svc-desc">
                    1. Hap linkun në <b>Google Chrome</b> ose <b>Samsung Internet</b>.<br>
                    2. Shtyp menynë <b>⋮</b> (ose <b>≡</b>).<br>
                    3. Zgjidh <b>Install app</b> ose <b>Add to Home screen</b>.
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div id="tab-info" class="tab-panel {'active' if active_tab == 'info' else ''}">
          <div class="card-3d">
            <h2>Orari &amp; Standardet — Grande Studio</h2>
            <p style="margin:0; color:var(--muted); font-size:0.84rem; line-height:1.55;">
              • <b>Orari i rezervimeve:</b> {html.escape(w_start)} – {html.escape(w_end)} (llogaritja e kohëzgjatjes bëhet automatikisht).<br>
              • <b>Higjiena &amp; Sterilizimi:</b> {html.escape(hygiene_note)}<br>
              • <b>Instagram:</b> <a href="{IG_WEB_URL}" data-ig-app="{IG_APP_URL}" data-ig-web="{IG_WEB_URL}" onclick="openInstagramProfile(event)" style="color:var(--vishnje);font-weight:700;">@{IG_USERNAME}</a>
            </p>
          </div>
        </div>
      </div>

      <script>
        let deferredPrompt = null;
        const installBanner = document.getElementById('pwaInstallBanner');
        const installBtn = document.getElementById('pwaInstallBtn');
        const installModal = document.getElementById('installGuideModal');
        const installModalTitle = document.getElementById('installModalTitle');
        const installModalBody = document.getElementById('installModalBody');

        const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;

        if (isStandalone && installBanner) {{
          installBanner.style.display = 'none';
        }}

        window.addEventListener('beforeinstallprompt', (e) => {{
          e.preventDefault();
          deferredPrompt = e;
          if (!isStandalone && installBanner) {{
            installBanner.style.display = 'flex';
          }}
        }});

        window.addEventListener('appinstalled', () => {{
          deferredPrompt = null;
          if (installBanner) installBanner.style.display = 'none';
          closeInstallModal();
        }});

        function closeInstallModal() {{
          if (installModal) installModal.style.display = 'none';
        }}

        if (installBtn) {{
          installBtn.addEventListener('click', async () => {{
            if (deferredPrompt) {{
              deferredPrompt.prompt();
              const choice = await deferredPrompt.userChoice;
              deferredPrompt = null;
              if (choice && choice.outcome === 'accepted' && installBanner) {{
                installBanner.style.display = 'none';
              }}
              return;
            }}

            if (isIOS) {{
              installModalTitle.textContent = '📲 Instalo në iPhone (Safari)';
              installModalBody.innerHTML =
                '1. Shtypni butonin <b>Share</b> (katrori me shigjetë lart) në Safari.<br>' +
                '2. Zgjidhni <b>Add to Home Screen</b>.<br>' +
                '3. Shtypni <b>Add</b> lart djathtas.';
            }} else {{
              installModalTitle.textContent = '📲 Instalo në Samsung / Android';
              installModalBody.innerHTML =
                '1. Shtypni menynë <b>⋮</b> lart djathtas në Chrome (ose <b>≡</b> në Samsung Internet).<br>' +
                '2. Zgjidhni <b>Install app</b> ose <b>Add to Home screen</b>.<br>' +
                '3. Konfirmoni me <b>Install / Add</b>.';
            }}
            if (installModal) installModal.style.display = 'flex';
          }});
        }}

        function switchTab(tabName) {{
          try {{ localStorage.setItem('grande_studio_client_tab', tabName); }} catch (e) {{}}
          document.querySelectorAll('.tab-btn').forEach(btn => {{
            btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
          }});
          document.querySelectorAll('.tab-panel').forEach(panel => {{
            panel.classList.toggle('active', panel.id === 'tab-' + tabName);
          }});
        }}

        try {{
          const urlParams = new URLSearchParams(window.location.search);
          const forcedTab = urlParams.get('tab');
          const savedTab = localStorage.getItem('grande_studio_client_tab');
          if (forcedTab && ['book', 'install', 'info'].includes(forcedTab)) {{
            switchTab(forcedTab);
          }} else if (savedTab && ['book', 'install', 'info'].includes(savedTab)) {{
            switchTab(savedTab);
          }}
        }} catch (e) {{}}

        const svcEl = document.getElementById('service_id');
        const dateEl = document.getElementById('appt_date');
        const slotsEl = document.getElementById('slotsContainer');
        const startInput = document.getElementById('start_time');
        const clientContactInput = document.getElementById('client_contact');

        if (clientContactInput) {{
          if (clientContactInput.value) {{
            try {{ localStorage.setItem('gs_client_contact', clientContactInput.value.trim().toLowerCase()); }} catch (e) {{}}
          }}
          clientContactInput.addEventListener('blur', () => {{
            const val = clientContactInput.value.trim().toLowerCase();
            if (val) {{
              try {{ localStorage.setItem('gs_client_contact', val); }} catch (e) {{}}
              autoEnableNotificationsAndPush(val);
            }}
          }});
        }}

        const allOptions = Array.from(svcEl.options).map(opt => ({{
          value: opt.value,
          cat: opt.getAttribute('data-cat'),
          text: opt.text
        }}));

        function filterServicesByCat(cat) {{
          document.querySelectorAll('#categoryFilterRow .cat-chip-3d').forEach(b => {{
            b.classList.toggle('active', b.getAttribute('data-cat') === cat);
          }});
          const filtered = cat === 'ALL' ? allOptions : allOptions.filter(o => o.cat === cat);
          svcEl.innerHTML = '';
          filtered.forEach(o => {{
            const opt = document.createElement('option');
            opt.value = o.value;
            opt.setAttribute('data-cat', o.cat);
            opt.textContent = o.text;
            svcEl.appendChild(opt);
          }});
          loadSlots();
        }}

        async function loadSlots() {{
          startInput.value = '';
          if (!svcEl.value) {{
            slotsEl.innerHTML = '<span style="color:#b41e2d;font-size:0.82rem;">Nuk ka shërbim të zgjedhur.</span>';
            return;
          }}
          slotsEl.innerHTML = '<span style="color:#6e4e57;font-size:0.82rem;">Po kontrollohen oraret e lira...</span>';
          const res = await fetch(`/api/slots?date=${{encodeURIComponent(dateEl.value)}}&service_id=${{encodeURIComponent(svcEl.value)}}`);
          const data = await res.json();
          slotsEl.innerHTML = '';

          if (!data.slots || data.slots.length === 0) {{
            slotsEl.innerHTML = '<span style="color:#b41e2d;font-size:0.82rem;">Nuk ka orare të lira për këtë datë dhe kohëzgjatje.</span>';
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
        document.getElementById('bookingForm').addEventListener('submit', (e) => {{
          if (!startInput.value) {{
            e.preventDefault();
            alert('Ju lutem zgjidhni një orar të lirë!');
            return;
          }}
          if (clientContactInput && clientContactInput.value) {{
            const cVal = clientContactInput.value.trim().toLowerCase();
            try {{ localStorage.setItem('gs_client_contact', cVal); }} catch (err) {{}}
            autoEnableNotificationsAndPush(cVal);
          }}
        }});

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
              if (navigator.vibrate) navigator.vibrate([350, 150, 350, 150, 450]);

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

        loadSlots();
      </script>
    """
    return render_iphone_shell(
        "Grande Studio — Thonj, Qerpikë & Vetulla",
        inner,
        role_tag=role_tag,
        client_contact_tag=saved_client_contact,
    )


@app.route("/client/logout")
def client_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    init_db()
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_panel"))

    acc = get_admin_account()
    ig_nav_html = get_ig_nav_button_html()
    err = None
    client_ip = request.remote_addr or "local"

    if acc is None:
        if request.method == "POST":
            email = request.form.get("reg_email", "").strip()
            pwd = request.form.get("reg_password", "")
            pwd2 = request.form.get("reg_password_confirm", "")

            if not email or "@" not in email:
                err = "Ju lutem shkruani një adresë të vlefshme emaili."
            elif len(pwd) < 6:
                err = "Fjalëkalimi duhet të ketë të paktën 6 karaktere."
            elif pwd != pwd2:
                err = "Fjalëkalimet nuk përputhen."
            else:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO admin_account (id, email, password_hash, updated_at)
                    VALUES (1, ?, ?, ?)
                    """,
                    (email, generate_password_hash(pwd), datetime.now().isoformat()),
                )
                conn.commit()
                conn.close()
                session.clear()
                session.permanent = True
                session["admin_logged_in"] = True
                return redirect(url_for("admin_panel"))

        alert_html = f'<div class="alert alert-err"><span>{html.escape(err)}</span><button type="button" class="dismiss-btn" onclick="this.parentElement.remove()">Hiq ✕</button></div>' if err else ""
        inner = f"""
          <header>
            <div class="header-inner">
              <div>
                <h1 class="brand-title">GRANDE STUDIO</h1>
                <p class="brand-sub">Regjistrim Admin</p>
              </div>
              <div class="header-mid">
                {ig_nav_html}
              </div>
              <div class="header-actions">
                <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
              </div>
            </div>
          </header>
          <div class="container">
            {alert_html}
            <div class="card-3d">
              <h2>Regjistrimi i Pronarit (Admin)</h2>
              <p style="margin:0 0 10px; font-size:0.8rem; color:var(--muted); line-height:1.4;">
                Regjistroni emailin dhe fjalëkalimin tuaj. Emaili shërben që në rast harrese të fjalëkalimit, resetimi të dërgohet direkt në emailin tuaj.
              </p>
              <form method="POST">
                <label for="reg_email">Emaili i Pronarit</label>
                <input type="email" name="reg_email" id="reg_email" placeholder="p.sh. pronari@grandestudio.com" required>

                <label for="reg_password">Fjalëkalimi</label>
                <input type="password" name="reg_password" id="reg_password" required>

                <label for="reg_password_confirm">Konfirmo Fjalëkalimin</label>
                <input type="password" name="reg_password_confirm" id="reg_password_confirm" required>

                <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">
                  Ruaj Llogarinë &amp; Hyj
                </button>
              </form>
            </div>
          </div>
        """
        return render_iphone_shell("Regjistrim Admin — Grande Studio", inner, role_tag="admin_login")

    if request.method == "POST":
        locked, wait_sec = is_login_locked(client_ip)
        if locked:
            err = f"Shumë tentativa të dështuara. Provoni sërish pas {wait_sec} sekondash."
        else:
            email_in = request.form.get("email", "").strip().lower()
            pwd_in = request.form.get("password", "")
            if email_in == acc["email"].strip().lower() and check_password_hash(acc["password_hash"], pwd_in):
                clear_failed_login(client_ip)
                session.clear()
                session.permanent = True
                session["admin_logged_in"] = True
                return redirect(url_for("admin_panel"))
            record_failed_login(client_ip)
            err = "Emaili ose fjalëkalimi është i pasaktë."

    err_html = f'<div class="alert alert-err"><span>{html.escape(err)}</span><button type="button" class="dismiss-btn" onclick="this.parentElement.remove()">Hiq ✕</button></div>' if err else ""

    inner = f"""
      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">Hyrja e pronarit</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
            <a href="{url_for('admin_reset_password')}" class="btn-3d btn-ghost-3d">🔑 Reset password</a>
          </div>
        </div>
      </header>
      <div class="container">
        {err_html}
        <div class="card-3d">
          <h2>Kyçu në Panelin e Adminit</h2>
          <form method="POST">
            <label for="email">Emaili i Regjistruar</label>
            <input type="email" name="email" id="email" required>

            <label for="password">Fjalëkalimi</label>
            <input type="password" name="password" id="password" required>

            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">
              Kyçu
            </button>
          </form>
        </div>
      </div>
    """
    return render_iphone_shell("Hyrja Admin — Grande Studio", inner, role_tag="admin_login")


@app.route("/admin/reset-password", methods=["GET", "POST"])
def admin_reset_password():
    init_db()
    acc = get_admin_account()
    ig_nav_html = get_ig_nav_button_html()
    msg = None
    msg_type = "ok"

    if acc is None:
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        entered_email = request.form.get("reset_email", "").strip().lower()
        if entered_email != acc["email"].strip().lower():
            msg = "Ky email nuk përputhet me emailin e regjistruar të pronarit."
            msg_type = "err"
        else:
            new_pwd = generate_temp_password(12)
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE admin_account SET password_hash = ?, updated_at = ? WHERE id = 1",
                (generate_password_hash(new_pwd), datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
            session.pop("admin_logged_in", None)

            sent, info_txt = send_reset_email(acc["email"], new_pwd)
            msg = info_txt
            msg_type = "ok" if sent else "err"

    alert_html = ""
    if msg:
        cls = "alert-ok" if msg_type == "ok" else "alert-err"
        alert_html = f'<div class="alert {cls}"><span>{html.escape(msg)}</span><button type="button" class="dismiss-btn" onclick="this.parentElement.remove()">Hiq ✕</button></div>'

    inner = f"""
      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">Reset fjalëkalimi</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
            <a href="{url_for('admin_login')}" class="btn-3d btn-ghost-3d">🔒 Admin</a>
          </div>
        </div>
      </header>
      <div class="container">
        {alert_html}
        <div class="card-3d">
          <h2>Reset Password në Email</h2>
          <p style="margin:0 0 10px; font-size:0.8rem; color:var(--muted); line-height:1.4;">
            Shkruani emailin që keni regjistruar si pronar. Fjalëkalimi i ri do të gjenerohet dhe do të dërgohet në atë email.
          </p>
          <form method="POST">
            <label for="reset_email">Emaili i Regjistruar</label>
            <input type="email" name="reset_email" id="reset_email" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:14px;">
              Dërgo Fjalëkalimin e Ri në Email
            </button>
          </form>
        </div>
      </div>
    """
    return render_iphone_shell("Reset Password — Grande Studio", inner, role_tag="reset")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    init_db()
    acc = get_admin_account()
    if not acc:
        return redirect(url_for("admin_login"))

    ig_nav_html = get_ig_nav_button_html()
    settings = get_salon_settings()
    conn = get_db()
    cur = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    feedback_msg = None
    feedback_type = "ok"
    external_reset_link = ""
    external_reset_label = ""
    show_clients_modal = False

    if request.method == "POST":
        action = request.form.get("action")

        if action == "refresh_client_password":
            client_id = request.form.get("client_id", type=int)
            show_clients_modal = True
            if client_id:
                cur.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
                cli = cur.fetchone()
                if cli:
                    token = secrets.token_urlsafe(16)
                    channel_label, ext_url = send_client_password_reset_notification(
                        cli,
                        f"{request.url_root.rstrip('/')}/?reset_token={token}",
                    )
                    cur.execute(
                        """
                        UPDATE clients
                        SET reset_pending = 1, reset_token = ?, reset_sent_via = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (token, channel_label, datetime.now().isoformat(), client_id),
                    )
                    conn.commit()
                    external_reset_link = ext_url
                    external_reset_label = channel_label
                    feedback_msg = f"Njoftimi për rivendosjen e passwordit u dërgua te {channel_label}."
                    feedback_type = "ok"

        elif action == "mark_seen":
            appt_id = request.form.get("appt_id", type=int)
            if appt_id:
                cur.execute("UPDATE appointments SET is_new = 0 WHERE id = ?", (appt_id,))
                conn.commit()
                feedback_msg = "Njoftimi u hoq."
                feedback_type = "ok"

        elif action == "mark_all_seen":
            cur.execute("UPDATE appointments SET is_new = 0 WHERE is_new = 1")
            conn.commit()
            feedback_msg = "Të gjitha njoftimet u hoqën."
            feedback_type = "ok"

        elif action == "change_password":
            curr_pwd = request.form.get("current_password", "")
            new_pwd = request.form.get("new_password", "")
            conf_pwd = request.form.get("confirm_password", "")

            if not check_password_hash(acc["password_hash"], curr_pwd):
                feedback_msg = "Fjalëkalimi aktual është i pasaktë."
                feedback_type = "err"
            elif len(new_pwd) < 6:
                feedback_msg = "Fjalëkalimi i ri duhet të ketë të paktën 6 karaktere."
                feedback_type = "err"
            elif new_pwd != conf_pwd:
                feedback_msg = "Konfirmimi i fjalëkalimit të ri nuk përputhet."
                feedback_type = "err"
            else:
                cur.execute(
                    "UPDATE admin_account SET password_hash = ?, updated_at = ? WHERE id = 1",
                    (generate_password_hash(new_pwd), datetime.now().isoformat()),
                )
                conn.commit()
                acc = get_admin_account()
                feedback_msg = "Fjalëkalimi u ndryshua dhe u ruajt automatikisht."
                feedback_type = "ok"

        elif action == "update_service":
            svc_id = request.form.get("svc_id", type=int)
            s_cat = request.form.get("s_cat", "Thonj").strip()
            s_name = request.form.get("s_name", "").strip()
            raw_dur = request.form.get("s_dur", "").strip()
            raw_price = request.form.get("s_price", "").strip()
            s_desc = request.form.get("s_desc", "").strip()
            if svc_id and s_name:
                cur.execute("SELECT duration_min, price FROM services WHERE id = ?", (svc_id,))
                prev_svc = cur.fetchone()
                try:
                    s_dur = int(raw_dur) if raw_dur else (prev_svc["duration_min"] if prev_svc else 60)
                except Exception:
                    s_dur = prev_svc["duration_min"] if prev_svc else 60
                try:
                    s_price = float(raw_price) if raw_price else (prev_svc["price"] if prev_svc else 0.0)
                except Exception:
                    s_price = prev_svc["price"] if prev_svc else 0.0
                cur.execute(
                    """
                    UPDATE services
                    SET category = ?, name = ?, duration_min = ?, price = ?, description = ?
                    WHERE id = ?
                    """,
                    (s_cat, s_name, max(15, s_dur), max(0.0, s_price), s_desc, svc_id),
                )
                conn.commit()
                feedback_msg = f"Shërbimi '{s_name}' u ruajt automatikisht."
                feedback_type = "ok"

        elif action == "add_service":
            s_cat = request.form.get("s_cat", "Thonj").strip()
            s_name = request.form.get("s_name", "").strip()
            s_dur = request.form.get("s_dur", type=int) or 60
            s_price = request.form.get("s_price", type=float) or 0.0
            s_desc = request.form.get("s_desc", "").strip()
            if s_name:
                cur.execute(
                    "INSERT INTO services (category, name, duration_min, price, description) VALUES (?, ?, ?, ?, ?)",
                    (s_cat, s_name, s_dur, s_price, s_desc),
                )
                conn.commit()
                feedback_msg = f"Shërbimi i ri '{s_name}' u shtua dhe u ruajt."
                feedback_type = "ok"

        elif action == "delete_service":
            svc_id = request.form.get("svc_id", type=int)
            if svc_id:
                cur.execute("DELETE FROM services WHERE id = ?", (svc_id,))
                conn.commit()
                feedback_msg = "Shërbimi u hoq."
                feedback_type = "ok"

        elif action == "block_slot":
            b_date = request.form.get("block_date", today_str)
            b_start = request.form.get("block_start", "09:00")
            b_end = request.form.get("block_end", "10:00")
            b_reason = request.form.get("block_reason", "Bllokuar nga Grande Studio").strip() or "Bllokuar"
            if time_to_min(b_start) >= time_to_min(b_end):
                feedback_msg = "Ora e fillimit duhet të jetë më e vogël se ora e mbarimit."
                feedback_type = "err"
            else:
                cur.execute("SELECT id FROM services LIMIT 1")
                first_svc = cur.fetchone()
                svc_id = first_svc["id"] if first_svc else None
                cur.execute(
                    """
                    INSERT INTO appointments
                    (client_name, client_phone, client_contact, service_id, appt_date, start_time, end_time, notes, status, is_new, client_notified, created_at)
                    VALUES (?, '-', '', ?, ?, ?, ?, ?, 'Bllokuar', 0, 1, ?)
                    """,
                    (b_reason, svc_id, b_date, b_start, b_end, "Orar i bllokuar", datetime.now().isoformat()),
                )
                conn.commit()
                feedback_msg = "Orari u bllokua dhe u ruajt automatikisht."
                feedback_type = "ok"

        elif action == "set_status":
            appt_id = request.form.get("appt_id", type=int)
            new_status = request.form.get("status", "Konfirmuar")
            if appt_id and new_status in ("Konfirmuar", "Anuluar", "Bllokuar"):
                cur.execute(
                    """
                    SELECT a.*, s.name AS service_name
                    FROM appointments a
                    LEFT JOIN services s ON a.service_id = s.id
                    WHERE a.id = ?
                    """,
                    (appt_id,),
                )
                appt_row = cur.fetchone()
                cur.execute(
                    "UPDATE appointments SET status = ?, is_new = 0, client_notified = 0 WHERE id = ?",
                    (new_status, appt_id),
                )
                conn.commit()

                if appt_row and new_status in ("Konfirmuar", "Anuluar"):
                    svc_title = appt_row["service_name"] or "Trajtim"
                    if new_status == "Konfirmuar":
                        send_push_to_client(
                            appt_row["client_contact"] or "",
                            title="✅ Termini u Konfirmua — Grande Studio",
                            body=f"Termini juaj për {svc_title} më {appt_row['appt_date']} në ora {appt_row['start_time']} u konfirmua!",
                            url="/",
                            appt_id=appt_id,
                        )
                    else:
                        send_push_to_client(
                            appt_row["client_contact"] or "",
                            title="❌ Termini u Anulua — Grande Studio",
                            body=f"Termini juaj për {svc_title} më {appt_row['appt_date']} në ora {appt_row['start_time']} u anulua.",
                            url="/",
                            appt_id=appt_id,
                        )

                feedback_msg = f"Termini u {new_status.lower()} dhe klienti u njoftua!"
                feedback_type = "ok"

        elif action == "delete_appt":
            appt_id = request.form.get("appt_id", type=int)
            if appt_id:
                cur.execute("DELETE FROM appointments WHERE id = ?", (appt_id,))
                conn.commit()
                feedback_msg = "Termini u fshi."
                feedback_type = "ok"

    cur.execute("SELECT * FROM clients ORDER BY id DESC")
    registered_clients = cur.fetchall()

    cur.execute(
        """
        SELECT a.*, s.name AS service_name, s.category, s.price
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        WHERE a.is_new = 1 AND a.status IN ('Në Pritje', 'Konfirmuar')
        ORDER BY a.id DESC
        """
    )
    new_appts = cur.fetchall()

    cur.execute(
        """
        SELECT a.*, s.name AS service_name, s.category, s.price
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        ORDER BY a.is_new DESC, a.appt_date ASC, a.start_time ASC
        """
    )
    appts = cur.fetchall()

    cur.execute("SELECT * FROM services ORDER BY id")
    services = cur.fetchall()
    conn.close()

    registered_clients_html = ""
    if registered_clients:
        for rc in registered_clients:
            badge_channel = "📧 Email" if rc["contact_type"] == "email" else "📸 Instagram"
            pending_badge = '<span class="badge-new-live" style="font-size:0.62rem; margin-left:5px;">Në pritje të passwordit të ri</span>' if rc["reset_pending"] == 1 else ""
            phone_txt = f" • Tel: {html.escape(rc['client_phone'])}" if rc["client_phone"] else ""
            name_txt = f" ({html.escape(rc['full_name'])})" if rc["full_name"] and rc["full_name"] != rc["contact_value"] else ""
            registered_clients_html += f"""
            <div class="client-list-item" onclick="selectRegisteredClient({rc['id']}, this)">
              <div style="display:flex; justify-content:space-between; align-items:center;">
                <b style="font-size:0.84rem; color:var(--vishnje-dark);">{html.escape(rc['contact_value'])}{name_txt}</b>
                <span class="svc-tag">{badge_channel}</span>
              </div>
              <div style="font-size:0.72rem; color:var(--muted); margin-top:3px;">
                Regjistruar: {html.escape(rc['created_at'][:10])}{phone_txt} {pending_badge}
              </div>
              <form method="POST" class="refresh-pwd-form" style="display:none; margin-top:8px;" onclick="event.stopPropagation();">
                <input type="hidden" name="action" value="refresh_client_password">
                <input type="hidden" name="client_id" value="{rc['id']}">
                <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; padding:7px 10px; font-size:0.78rem;">
                  🔄 Refresko Password
                </button>
              </form>
            </div>
            """
    else:
        registered_clients_html = '<p style="font-size:0.8rem; color:var(--muted);">Nuk ka ende klientë të regjistruar.</p>'

    ext_link_banner = ""
    if external_reset_link:
        ext_link_banner = f"""
        <div style="background:#fff0f4; border:1px solid #fda4af; border-radius:10px; padding:8px 10px; margin-bottom:10px; font-size:0.76rem;">
          <b>Kanali: {html.escape(external_reset_label)}</b><br>
          <a href="{html.escape(external_reset_link, quote=True)}" target="_blank" rel="noopener noreferrer" style="color:#be123c; font-weight:800; text-decoration:underline;">
            ↗ Hap direkt {html.escape(external_reset_label)} për t'ia dërguar klientit
          </a>
        </div>
        """

    new_bookings_section = ""
    if new_appts:
        cards = ""
        for na in new_appts:
            notes_line = f"<div style='font-size:0.76rem; color:#881337; margin-top:4px;'>📝 <i>{html.escape(na['notes'])}</i></div>" if na["notes"] else ""
            cards += f"""
            <div class="new-booking-card" id="newBookingCard_{na['id']}">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                <span class="badge-new-live">🔔 NJOFTIM I RI</span>
                <span style="font-size:0.78rem; font-weight:800; color:#9f1239;">{html.escape(na['appt_date'])} • {html.escape(na['start_time'])} - {html.escape(na['end_time'])}</span>
              </div>
              <div style="font-size:0.95rem; font-weight:800; color:#4c0519;">{html.escape(na['client_name'])}</div>
              <div style="font-size:0.82rem; font-weight:700; color:#881337; margin-top:2px;">
                📞 <a href="tel:{html.escape(na['client_phone'], quote=True)}" style="color:#be123c; text-decoration:underline;">{html.escape(na['client_phone'])}</a>
              </div>
              <div style="font-size:0.8rem; color:#261217; margin-top:4px;">
                💅 <b>[{html.escape(na['category'] or '-')}] {html.escape(na['service_name'] or '-')}</b> ({na['price'] or 0:.0f}€)
              </div>
              {notes_line}
              <div style="display:grid; grid-template-columns: 1fr 1fr; gap:6px; margin-top:10px;">
                <form method="POST">
                  <input type="hidden" name="action" value="set_status">
                  <input type="hidden" name="appt_id" value="{na['id']}">
                  <input type="hidden" name="status" value="Konfirmuar">
                  <button type="submit" class="btn-3d btn-confirm-3d" style="width:100%;">
                    ✅ Konfirmo
                  </button>
                </form>
                <form method="POST">
                  <input type="hidden" name="action" value="set_status">
                  <input type="hidden" name="appt_id" value="{na['id']}">
                  <input type="hidden" name="status" value="Anuluar">
                  <button type="submit" class="btn-3d btn-cancel-3d" style="width:100%;">
                    ❌ Anulo
                  </button>
                </form>
              </div>
            </div>
            """

        new_bookings_section = f"""
        <div class="card-3d" style="border: 2px solid #e11d48; background: #fff8f9;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <h2 style="margin:0; color:#be123c;">🚨 Njoftime të Reja ({len(new_appts)})</h2>
            <form method="POST">
              <input type="hidden" name="action" value="mark_all_seen">
              <button type="submit" class="btn-sm-3d btn-seen">Hiq të gjitha ✕</button>
            </form>
          </div>
          {cards}
        </div>
        """

    rows_html = ""
    for a in appts:
        is_new = a["is_new"] == 1 and a["status"] in ("Në Pritje", "Konfirmuar")
        row_cls = "row-is-new" if is_new else ""
        new_tag = '<span class="badge-new-live" style="font-size:0.6rem; padding:1px 5px; margin-bottom:3px;">I RI</span><br>' if is_new else ""
        if a["status"] == "Konfirmuar":
            badge_cls = "b-ok"
        elif a["status"] == "Në Pritje":
            badge_cls = "b-wait"
        elif a["status"] == "Bllokuar":
            badge_cls = "b-blk"
        else:
            badge_cls = "b-can"

        confirm_btn = ""
        if a["status"] != "Konfirmuar":
            confirm_btn = f"""
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="set_status">
              <input type="hidden" name="appt_id" value="{a["id"]}">
              <input type="hidden" name="status" value="Konfirmuar">
              <button class="btn-sm-3d btn-seen" type="submit">Konfirmo</button>
            </form>
            """

        rows_html += f"""
        <tr class="{row_cls}">
          <td>{new_tag}<b>{html.escape(a["appt_date"])}</b><br>{html.escape(a["start_time"])}-{html.escape(a["end_time"])}</td>
          <td><b>{html.escape(a["client_name"])}</b><br><a href="tel:{html.escape(a["client_phone"], quote=True)}" style="color:#6e4e57; text-decoration:none;">{html.escape(a["client_phone"])}</a></td>
          <td><span class="svc-tag">{html.escape(a["category"] or "-")}</span><br><b>{html.escape(a["service_name"] or "-")}</b></td>
          <td><span class="badge {badge_cls}">{html.escape(a["status"])}</span></td>
          <td>
            {confirm_btn}
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="set_status">
              <input type="hidden" name="appt_id" value="{a["id"]}">
              <input type="hidden" name="status" value="Anuluar">
              <button class="btn-sm-3d" type="submit">Anulo</button>
            </form>
            <form method="POST" style="display:inline;">
              <input type="hidden" name="action" value="delete_appt">
              <input type="hidden" name="appt_id" value="{a["id"]}">
              <button class="btn-sm-3d btn-del" type="submit">Fshi</button>
            </form>
          </td>
        </tr>
        """

    editable_services_html = ""
    for s in services:
        sel_thonj = "selected" if s["category"] == "Thonj" else ""
        sel_qerp = "selected" if s["category"] == "Qerpikë" else ""
        sel_vet = "selected" if s["category"] == "Vetulla" else ""
        sel_komb = "selected" if s["category"] == "Kombinim" else ""
        editable_services_html += f"""
        <div class="edit-svc-box">
          <form method="POST" class="auto-save-form">
            <input type="hidden" name="action" value="update_service">
            <input type="hidden" name="svc_id" value="{s['id']}">
            <div class="row-2">
              <div>
                <label>Kategoria</label>
                <select name="s_cat">
                  <option value="Thonj" {sel_thonj}>Thonj</option>
                  <option value="Qerpikë" {sel_qerp}>Qerpikë</option>
                  <option value="Vetulla" {sel_vet}>Vetulla</option>
                  <option value="Kombinim" {sel_komb}>Kombinim</option>
                </select>
              </div>
              <div>
                <label>Çmimi (€)</label>
                <input type="number" name="s_price" value="{s['price']:.2f}" min="0" step="0.5" required>
              </div>
            </div>
            <label>Emri i Shërbimit</label>
            <input type="text" name="s_name" value="{html.escape(s['name'], quote=True)}" required>
            <label>Kohëzgjatja (minuta)</label>
            <input type="number" name="s_dur" value="{s['duration_min']}" min="15" step="15" required>
            <label>Përshkrimi</label>
            <input type="text" name="s_desc" value="{html.escape(s['description'] or '', quote=True)}">
          </form>
          <form method="POST" style="margin-top:6px;">
            <input type="hidden" name="action" value="delete_service">
            <input type="hidden" name="svc_id" value="{s['id']}">
            <button type="submit" class="btn-sm-3d btn-del" style="width:100%;">🗑️ Hiq Shërbimin</button>
          </form>
        </div>
        """

    alert_html = ""
    if feedback_msg:
        cls = "alert-ok" if feedback_type == "ok" else "alert-err"
        alert_html = f"""
        <div class="alert {cls}">
          <span>{html.escape(feedback_msg)}</span>
          <button type="button" class="dismiss-btn" onclick="this.parentElement.remove()">Hiq ✕</button>
        </div>
        """

    max_existing_id = max([a["id"] for a in appts], default=0)
    w_start = settings.get("work_start") or WORK_START
    w_end = settings.get("work_end") or WORK_END
    step_min = int(settings.get("slot_step_min") or SLOT_STEP_MIN)
    hygiene_note = settings.get("hygiene_note") or "Veglat e thonjve, qerpikëve dhe vetullave sterilizohen pas çdo klienteje."

    inner = f"""
      <div id="registeredClientsModal" class="modal-overlay" style="display:{'flex' if show_clients_modal else 'none'};">
        <div class="modal-card">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <b style="color:#72091d; font-size:0.96rem;">👥 Klientët e Regjistruar ({len(registered_clients)})</b>
            <button type="button" onclick="closeRegisteredClientsModal()" style="background:none; border:none; font-size:1.1rem; font-weight:800; color:#72091d; cursor:pointer;">✕</button>
          </div>
          <p style="margin:0 0 10px; font-size:0.75rem; color:var(--muted);">
            Prekni një klient për të shfaqur butonin <b>Refresko Password</b>.
          </p>
          {ext_link_banner}
          {registered_clients_html}
        </div>
      </div>

      <div id="liveToastBanner" class="live-toast-banner">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <b style="font-size:0.95rem;">🔔 NJOFTIM I RI — GRANDE STUDIO</b>
          <button type="button" onclick="dismissLiveToast()" style="background:rgba(255,255,255,0.22); border:none; color:#fff; font-weight:900; font-size:0.8rem; border-radius:8px; padding:4px 9px; cursor:pointer;">Hiq ✕</button>
        </div>
        <div id="liveToastBody" style="font-size:0.82rem; margin-top:6px; line-height:1.4;"></div>
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px;" id="liveToastActions"></div>
      </div>

      <header>
        <div class="header-inner">
          <div>
            <h1 class="brand-title">GRANDE STUDIO</h1>
            <p class="brand-sub">{html.escape(acc['email'])}</p>
          </div>
          <div class="header-mid">
            {ig_nav_html}
          </div>
          <div class="header-actions">
            <a href="{url_for('index')}" class="btn-3d btn-ghost-3d">← Klienti</a>
            <a href="{url_for('admin_reset_password')}" class="btn-3d btn-ghost-3d">🔑 Reset password</a>
            <a href="{url_for('admin_logout')}" class="btn-3d btn-ghost-3d">Dil</a>
          </div>
        </div>
      </header>

      <div class="container">
        {alert_html}

        <div class="card-3d" style="padding:11px 12px; background:#fff9fb;">
          <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap;">
            <div style="font-size:0.76rem; font-weight:700; color:var(--vishnje);">
              🔔 Njoftimet &amp; zëri aktivë • <span id="autoSaveIndicator">Ruajtje automatike</span>
            </div>
            <button type="button" onclick="openRegisteredClientsModal()" class="btn-3d btn-vishnje-3d" style="padding:7px 11px; font-size:0.76rem;">
              👥 Klientët e Regjistruar ({len(registered_clients)})
            </button>
          </div>
        </div>

        <div class="card-3d" id="salonSettingsBox">
          <h2>⚙️ Cilësimet e Sallonit (Ruajtje Automatike)</h2>
          <div class="row-2">
            <div>
              <label for="cfg_work_start">Hapja e Orarit</label>
              <input type="time" id="cfg_work_start" class="salon-cfg-input" value="{html.escape(w_start, quote=True)}">
            </div>
            <div>
              <label for="cfg_work_end">Mbyllja e Orarit</label>
              <input type="time" id="cfg_work_end" class="salon-cfg-input" value="{html.escape(w_end, quote=True)}">
            </div>
          </div>
          <label for="cfg_slot_step">Hapi i Orareve (minuta)</label>
          <input type="number" id="cfg_slot_step" class="salon-cfg-input" value="{step_min}" min="15" step="15">
          <label for="cfg_hygiene_note">Shënimi i Higjienës &amp; Sterilizimit</label>
          <input type="text" id="cfg_hygiene_note" class="salon-cfg-input" value="{html.escape(hygiene_note, quote=True)}">
        </div>

        {new_bookings_section}

        <div class="card-3d">
          <h2>Terminet &amp; Bllokimet</h2>
          <div style="overflow-x:auto;">
            <table>
              <thead>
                <tr>
                  <th>Data/Ora</th>
                  <th>Klienti</th>
                  <th>Shërbimi</th>
                  <th>Statusi</th>
                  <th>Veprime</th>
                </tr>
              </thead>
              <tbody>
                {rows_html if rows_html else '<tr><td colspan="5">Nuk ka termine ende.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>

        <div class="card-3d">
          <h2>➕ Shto Shërbim të Ri (Thonj, Qerpikë, Vetulla, Kombinim)</h2>
          <form method="POST">
            <input type="hidden" name="action" value="add_service">
            <div class="row-2">
              <div>
                <label>Kategoria</label>
                <select name="s_cat">
                  <option value="Thonj">Thonj</option>
                  <option value="Qerpikë">Qerpikë</option>
                  <option value="Vetulla">Vetulla</option>
                  <option value="Kombinim">Kombinim</option>
                </select>
              </div>
              <div>
                <label>Çmimi (€)</label>
                <input type="number" name="s_price" value="25" min="0" step="0.5" required>
              </div>
            </div>
            <label>Emri i shërbimit</label>
            <input type="text" name="s_name" placeholder="p.sh. Laminim Vetullash + Xhel" required>
            <label>Kohëzgjatja (minuta)</label>
            <input type="number" name="s_dur" value="60" min="15" step="15" required>
            <label>Përshkrim i shkurtër</label>
            <input type="text" name="s_desc" placeholder="p.sh. Rezultat natyral dhe afatgjatë">
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              Shto Shërbimin
            </button>
          </form>
        </div>

        <div class="card-3d">
          <h2>✏️ Ndrysho ose Hiq Shërbimet Ekzistuese</h2>
          {editable_services_html if editable_services_html else '<p style="font-size:0.82rem;color:var(--muted);">Nuk ka shërbime aktualisht. Shtoni një shërbim më lart.</p>'}
        </div>

        <div class="card-3d">
          <h2>Blloko Orar / Pushim</h2>
          <form method="POST">
            <input type="hidden" name="action" value="block_slot">
            <label>Data</label>
            <input type="date" name="block_date" value="{today_str}" required>
            <label>Arsyeja</label>
            <input type="text" name="block_reason" placeholder="p.sh. Pushim / Trajnim">
            <label>Nga ora</label>
            <input type="time" name="block_start" value="12:00" required>
            <label>Deri në orë</label>
            <input type="time" name="block_end" value="13:00" required>
            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              Blloko Orarin
            </button>
          </form>
        </div>

        <div class="card-3d">
          <h2>Ndrysho Fjalëkalimin e Adminit</h2>
          <form method="POST">
            <input type="hidden" name="action" value="change_password">
            <label for="current_password">Fjalëkalimi Aktual</label>
            <input type="password" name="current_password" id="current_password" required>

            <label for="new_password">Fjalëkalimi i Ri</label>
            <input type="password" name="new_password" id="new_password" required>

            <label for="confirm_password">Konfirmo Fjalëkalimin e Ri</label>
            <input type="password" name="confirm_password" id="confirm_password" required>

            <button type="submit" class="btn-3d btn-vishnje-3d" style="width:100%; margin-top:12px;">
              Ndrysho Fjalëkalimin
            </button>
          </form>
        </div>
      </div>

      <script>
        let lastKnownMaxId = {max_existing_id};
        const saveIndicator = document.getElementById('autoSaveIndicator');

        function openRegisteredClientsModal() {{
          const m = document.getElementById('registeredClientsModal');
          if (m) m.style.display = 'flex';
        }}

        function closeRegisteredClientsModal() {{
          const m = document.getElementById('registeredClientsModal');
          if (m) m.style.display = 'none';
        }}

        function selectRegisteredClient(clientId, cardEl) {{
          document.querySelectorAll('.client-list-item').forEach(el => {{
            el.classList.remove('selected');
            const f = el.querySelector('.refresh-pwd-form');
            if (f) f.style.display = 'none';
          }});
          if (cardEl) {{
            cardEl.classList.add('selected');
            const form = cardEl.querySelector('.refresh-pwd-form');
            if (form) form.style.display = 'block';
          }}
        }}

        function dismissLiveToast() {{
          const toast = document.getElementById('liveToastBanner');
          if (toast) toast.style.display = 'none';
        }}

        async function handleQuickApptAction(apptId, status) {{
          try {{
            const res = await fetch('/api/push/action', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ appt_id: apptId, status: status }})
            }});
            const data = await res.json();
            if (data.ok) {{
              dismissLiveToast();
              window.location.reload();
            }}
          }} catch (e) {{}}
        }}

        async function pushSalonSettingsAuto() {{
          const payload = {{
            work_start: document.getElementById('cfg_work_start').value,
            work_end: document.getElementById('cfg_work_end').value,
            slot_step_min: document.getElementById('cfg_slot_step').value,
            hygiene_note: document.getElementById('cfg_hygiene_note').value
          }};
          try {{
            const res = await fetch('/api/admin/auto-save-settings', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify(payload)
            }});
            const data = await res.json();
            if (data.ok && saveIndicator) {{
              saveIndicator.textContent = 'U ruajt automatikisht në ' + data.saved_at;
            }}
          }} catch (e) {{}}
        }}

        let cfgTimer = null;
        document.querySelectorAll('.salon-cfg-input').forEach(inp => {{
          inp.addEventListener('change', () => {{
            if (cfgTimer) clearTimeout(cfgTimer);
            cfgTimer = setTimeout(pushSalonSettingsAuto, 250);
          }});
          inp.addEventListener('input', () => {{
            if (cfgTimer) clearTimeout(cfgTimer);
            cfgTimer = setTimeout(pushSalonSettingsAuto, 550);
          }});
        }});

        const svcTimers = new Map();
        document.querySelectorAll('.auto-save-form').forEach((form, idx) => {{
          const triggerSave = () => {{
            const nameVal = (form.querySelector('input[name="s_name"]')?.value || '').trim();
            const priceVal = parseFloat(form.querySelector('input[name="s_price"]')?.value || '');
            const durVal = parseInt(form.querySelector('input[name="s_dur"]')?.value || '', 10);
            if (!nameVal || isNaN(priceVal) || isNaN(durVal)) return;
            if (svcTimers.has(idx)) clearTimeout(svcTimers.get(idx));
            const t = setTimeout(async () => {{
              try {{
                const fd = new FormData(form);
                await fetch('/admin', {{ method: 'POST', body: fd }});
                if (saveIndicator) {{
                  saveIndicator.textContent = 'Shërbimi u ruajt automatikisht';
                }}
              }} catch (e) {{}}
            }}, 550);
            svcTimers.set(idx, t);
          }};
          form.querySelectorAll('input, select').forEach(el => {{
            el.addEventListener('change', triggerSave);
            el.addEventListener('input', triggerSave);
          }});
        }});

        setInterval(async () => {{
          try {{
            const res = await fetch('/api/admin/new-bookings');
            if (!res.ok) return;
            const data = await res.json();
            if (data.bookings && data.bookings.length > 0) {{
              const newest = data.bookings[0];
              if (newest.id > lastKnownMaxId) {{
                lastKnownMaxId = newest.id;
                playAutoAlertSound();
                if (navigator.vibrate) navigator.vibrate([400, 150, 400, 150, 500]);

                const toast = document.getElementById('liveToastBanner');
                const toastBody = document.getElementById('liveToastBody');
                const toastActions = document.getElementById('liveToastActions');
                toastBody.innerHTML = `<b>${{newest.client_name}}</b> (${{newest.client_phone}})<br>💅 ${{newest.service_name}} — 📅 ${{newest.appt_date}} në <b>${{newest.start_time}}</b>`;
                toastActions.innerHTML = `
                  <button type="button" onclick="handleQuickApptAction(${{newest.id}}, 'Konfirmuar')" class="btn-3d btn-confirm-3d" style="width:100%;">✅ Konfirmo</button>
                  <button type="button" onclick="handleQuickApptAction(${{newest.id}}, 'Anuluar')" class="btn-3d btn-cancel-3d" style="width:100%;">❌ Anulo</button>
                `;
                toast.style.display = 'block';
              }}
            }}
          }} catch (e) {{}}
        }}, 5000);
      </script>
    """
    return render_iphone_shell("Paneli i Adminit — Grande Studio", inner, role_tag="admin")


if __name__ == "__main__":
    init_db()
    prepare_clean_nails_asset()
    prepare_ig_avatar_asset()
    prepare_app_icons()
    get_vapid_keys()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)