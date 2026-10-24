import os
import sys
import hashlib
import json
import re
import requests
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

# Поддръжка на кирилица в конзолата при Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCHEDULES_API_URL = "https://tu-sofia.bg/api/schedules"
FALLBACK_PDF_URL = "https://tu-sofia.bg/api/documents/download?path=schedules%2Fn4HCmE1Khpl5zNVvmwzMdwkgiN1wJaG7mvYOHuro.pdf&locale=bg"

BASE_DIR = Path(__file__).resolve().parent
HASH_FILE = BASE_DIR / "schedule_hash.txt"
URL_FILE = BASE_DIR / "schedule_url.txt"
PDF_FILE = BASE_DIR / "latest_schedule.pdf"
HTML_FILE = BASE_DIR / "index.html"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

WEEKS_START = [
    "2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26", "2026-11-02",
    "2026-11-09", "2026-11-16", "2026-11-23", "2026-11-30", "2026-12-07",
    "2026-12-14", "2027-01-04", "2027-01-11"
]
DAYS_BG = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]

def resolve_room(room_raw: str, day_bg: str) -> str:
    r = room_raw.strip()
    if r == "3401/3501*":
        return "3401" if day_bg == "Пн" else "3501"
    if r == "3429/4130A**":
        return "4130А" if day_bg == "Вт" else "3429"
    if r == "4130Б/3507***":
        return "3507" if day_bg == "Ср" else "4130Б"
    if r in ("2101В/4130А****", "2101В/4130А-събота"):
        return "4130А" if day_bg == "Сб" else "2101В"
    if r == "3609/ 3507*****":
        return "3507" if day_bg == "Ср" else "3609"
    if r == "3509/3507":
        return "3507" if day_bg in ("Ср", "Сб") else "3509"
    return r

def resolve_groups(grp_raw: str) -> list:
    g = grp_raw.strip()
    if g == "201/202/203":
        return ["201А", "201Б", "201В", "202А", "202Б", "203"]
    if g == "201":
        return ["201А", "201Б", "201В"]
    if g == "202":
        return ["202А", "202Б"]
    if g == "202A":
        return ["202А"]
    return [g]

def parse_schedule_pdf(pdf_bytes: bytes):
    import pymupdf
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if not doc or len(doc) == 0:
        return None, None
    tables = doc[0].find_tables()
    if not tables or not tables.tables:
        return None, None

    rows = tables[0].extract()
    sessions = []
    undated = []
    curr_disc = ""

    for r in rows[1:]:
        if r[1] and r[1].strip():
            curr_disc = r[1].strip()
        kind = r[2].strip() if r[2] else "Л"
        teacher = r[3].strip() if r[3] else ""
        grp_raw = r[4].strip() if r[4] else ""
        room_raw = r[5].strip() if r[5] else ""
        start_weekday = r[6].strip() if r[6] else "16:30"
        start_sat = r[7].strip() if r[7] else "8:30"
        groups = resolve_groups(grp_raw)

        has_any_week = False
        for w_idx in range(13):
            col_idx = 8 + w_idx
            if col_idx >= len(r):
                continue
            val = r[col_idx]
            if not val or not val.strip():
                continue
            has_any_week = True
            w_num = w_idx + 1
            w_start_dt = datetime.strptime(WEEKS_START[w_idx], "%Y-%m-%d")

            lines = [l.strip() for l in val.split("\n") if l.strip()]
            for line in lines:
                m = re.match(r'^(.*?)\s+(\d+)\s*ч\.?$', line)
                if not m:
                    continue
                day_spec = m.group(1).strip()
                hours = int(m.group(2))

                target_days = []
                if day_spec == "Пн-Сб":
                    target_days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]
                elif day_spec == "Вт-Пт":
                    target_days = ["Вт", "Ср", "Чт", "Пт"]
                elif day_spec == "Ср-Пт":
                    target_days = ["Ср", "Чт", "Пт"]
                elif day_spec in DAYS_BG:
                    target_days = [day_spec]
                else:
                    parts = [p.strip() for p in day_spec.split(",") if p.strip()]
                    for p in parts:
                        if p == "Ср-Пт":
                            target_days.extend(["Ср", "Чт", "Пт"])
                        elif p in DAYS_BG:
                            target_days.append(p)

                for d_name in target_days:
                    day_offset = DAYS_BG.index(d_name)
                    session_date = (w_start_dt + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                    start_time = start_sat if d_name == "Сб" else start_weekday
                    room = resolve_room(room_raw, d_name)

                    sessions.append({
                        "w": w_num,
                        "date": session_date,
                        "day": d_name,
                        "disc": curr_disc,
                        "kind": kind,
                        "teacher": teacher,
                        "groups": groups,
                        "room": room,
                        "start": start_time,
                        "h": hours
                    })

        if not has_any_week and curr_disc:
            undated.append({
                "disc": curr_disc,
                "kind": kind,
                "teacher": teacher,
                "groups": groups,
                "room": room_raw
            })

    return sessions, undated

def diff_sessions(old_sessions: list, new_sessions: list) -> list:
    def session_key(s):
        return (s.get('date'), s.get('start'), s.get('disc'), s.get('kind'), tuple(sorted(s.get('groups', []))))

    old_map = {session_key(s): s for s in old_sessions}
    new_map = {session_key(s): s for s in new_sessions}

    added_keys = set(new_map.keys()) - set(old_map.keys())
    removed_keys = set(old_map.keys()) - set(new_map.keys())
    common_keys = set(old_map.keys()) & set(new_map.keys())

    changes = []

    # Промени в зала, часове или преподавател при съвпадащи часове
    for k in sorted(common_keys):
        old_s = old_map[k]
        new_s = new_map[k]
        mods = []
        if old_s.get('room') != new_s.get('room'):
            mods.append(f"зала {old_s.get('room')} ➔ {new_s.get('room')}")
        if old_s.get('h') != new_s.get('h'):
            mods.append(f"{old_s.get('h')} ч. ➔ {new_s.get('h')} ч.")
        if old_s.get('teacher') != new_s.get('teacher'):
            mods.append(f"преподавател {old_s.get('teacher')} ➔ {new_s.get('teacher')}")
        if mods:
            d, t, disc, kind, grps = k
            grps_str = ", ".join(grps)
            changes.append(f"📝 <b>{d} ({old_s.get('day')}): {disc} ({kind})</b> [{grps_str}]: " + ", ".join(mods))

    # Добавени занятия
    for k in sorted(added_keys):
        s = new_map[k]
        grps_str = ", ".join(s.get('groups', []))
        changes.append(f"➕ <b>Добавено: {s.get('date')} ({s.get('day')})</b> — {s.get('disc')} ({s.get('kind')}), {s.get('h')} ч., от {s.get('start')} в зала {s.get('room')} [{grps_str}]")

    # Премахнати / отпаднали занятия
    for k in sorted(removed_keys):
        s = old_map[k]
        grps_str = ", ".join(s.get('groups', []))
        changes.append(f"➖ <b>Отпаднало: {s.get('date')} ({s.get('day')})</b> — {s.get('disc')} ({s.get('kind')}), {s.get('h')} ч., от {s.get('start')} в зала {s.get('room')} [{grps_str}]")

    return changes

def update_index_html_from_pdf(pdf_bytes: bytes) -> tuple[bool, list]:
    if not HTML_FILE.exists():
        return False, []
    try:
        sessions, undated = parse_schedule_pdf(pdf_bytes)
        if not sessions:
            print("[ВНИМАНИЕ] Не бяха извлечени занятия от PDF таблицата.")
            return False, []

        html = HTML_FILE.read_text(encoding="utf-8")
        old_sessions = []
        m = re.search(r'const DATA=(\{.*?\});', html)
        if m:
            try:
                old_sessions = json.loads(m.group(1)).get('sessions', [])
            except Exception:
                pass

        changes = diff_sessions(old_sessions, sessions)

        new_data = {
            "sessions": sessions,
            "undated": undated,
            "weeks": WEEKS_START
        }
        data_str = json.dumps(new_data, ensure_ascii=False)

        html_updated = re.sub(r'const DATA=\{.*?\};', f'const DATA={data_str};', html)
        html_updated = re.sub(
            r'const GROUPS=\[.*?\];',
            'const GROUPS=["201А","201Б","201В","202А","202Б","203"];',
            html_updated
        )
        HTML_FILE.write_text(html_updated, encoding="utf-8")
        print(f"✅ index.html беше успешно обновен с {len(sessions)} занятия от новия разпис!")
        return True, changes
    except Exception as e:
        print(f"⚠️ Грешка при автоматично обновяване на index.html: {e}")
        return False, []

def get_latest_schedule_url() -> str:
    print("Проверка на сайта на ТУ-София за актуален линк към разписа на МБИС...")
    params = {
        "type": "lectures",
        "degree_type": "master",
        "faculty": "faculty-of-economics",
        "per_page": "50"
    }
    try:
        resp = requests.get(SCHEDULES_API_URL, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("data", [])
        for item in items:
            major_slug = item.get("major", {}).get("slug", "").lower()
            major_title = item.get("major", {}).get("title", "").lower()
            if "menidzhmant-i-biznes-informatsionni-sistemi" in major_slug or "бизнес информационни системи" in major_title:
                file_path = item.get("file_path")
                if file_path:
                    print(f"✅ Открит актуален линк от страницата: {file_path}")
                    return file_path
        print("⚠️ Специалността МБИС не беше намерена в списъка на факултета, използва се резервен линк.")
    except Exception as e:
        print(f"⚠️ Грешка при динамично извличане на линка от ТУ-София ({e}), използва се резервен линк.")

    return FALLBACK_PDF_URL

def download_pdf(url: str) -> bytes:
    print(f"Изтегляне на разписа от: {url} ...")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.content

def calculate_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def send_email_notification(new_hash: str, pdf_url: str, pdf_bytes: bytes, changes: list = None):
    email_to = os.environ.get("EMAIL_TO")
    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")
    gh_pages_url = os.environ.get("GITHUB_PAGES_URL", "")

    if not (email_to and email_user and email_pass):
        print("[INFO] Имейл известието е пропуснато (не са конфигурирани EMAIL_TO, EMAIL_USER или EMAIL_PASS).")
        return

    print(f"Изпращане на имейл известие до {email_to}...")
    msg = EmailMessage()
    msg["Subject"] = "🔔 [ТУ-София] Открита е промяна в разписа за МБИС!"
    msg["From"] = email_user
    msg["To"] = email_to

    pages_link_html = f'<p>🌐 <b>Интерактивна онлайн програма (GitHub Pages):</b> <a href="{gh_pages_url}">{gh_pages_url}</a></p>' if gh_pages_url else ""

    if changes:
        changes_items = "".join(f"<li style='margin-bottom: 6px;'>{c}</li>" for c in changes)
        diff_html = f"""
        <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px 18px; margin: 18px 0;">
          <h3 style="margin-top: 0; margin-bottom: 10px; color: #1e293b; font-size: 16px;">📋 Настъпили промени в разписа:</h3>
          <ul style="padding-left: 20px; margin: 0; line-height: 1.5; font-size: 14px;">
            {changes_items}
          </ul>
        </div>
        """
        plain_changes = "\n".join(f"- {re.sub('<[^<]+?>', '', c)}" for c in changes)
        plain_text = f"Открита е промяна в разписа на ТУ-София за специалност МБИС.\nЛинк: {pdf_url}\n\nПромени:\n{plain_changes}\n\nВиж прикачените файлове."
    else:
        diff_html = """
        <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px 18px; margin: 18px 0;">
          <p style="margin: 0; color: #475569;">PDF разписът е обновен от университета (промяна в текстови бележки или формат).</p>
        </div>
        """
        plain_text = f"Открита е промяна в разписа на ТУ-София за специалност МБИС.\nЛинк: {pdf_url}\n\nВиж прикачените файлове."

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2563EB;">Здравей! Открита е промяна в разписа на ТУ-София.</h2>
        <p>Автоматичната проверка откри обновяване на разписа за специалност <b>МБИС (Магистър)</b> на сайта на ТУ-София.</p>
        <p>📥 <b>Директен линк от сайта:</b> <a href="{pdf_url}">{pdf_url}</a></p>
        <p>🔑 <b>Контролен хеш на новия файл (SHA-256):</b> <code>{new_hash[:16]}...</code></p>
        {diff_html}
        {pages_link_html}
        <p>В прикачения файл ще намериш новия <b>PDF разпис</b> от университета, както и обновения интерактивен <b>index.html</b>.</p>
        <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
        <small style="color: #888;">Това съобщение е изпратено автоматично от твоя GitHub Actions робот.</small>
      </body>
    </html>
    """
    msg.set_content(plain_text)
    msg.add_alternative(html_content, subtype="html")

    # Прикачване на новия PDF
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename="razpis-tu-sofia.pdf")

    # Прикачване на index.html ако съществува
    if HTML_FILE.exists():
        with open(HTML_FILE, "rb") as f:
            msg.add_attachment(f.read(), maintype="text", subtype="html", filename="mbis-programa.html")

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(email_user, email_pass)
            server.send_message(msg)
        print("[OK] Имейлът беше изпратен успешно!")
    except Exception as e:
        print(f"[ГРЕШКА] Неуспешно изпращане на имейл: {e}")

def main():
    current_url = get_latest_schedule_url()

    try:
        content = download_pdf(current_url)
    except Exception as e:
        print(f"[ГРЕШКА] Неуспешно изтегляне на PDF от ТУ-София: {e}")
        sys.exit(1)

    new_hash = calculate_hash(content)

    old_hash = ""
    if HASH_FILE.exists():
        old_hash = HASH_FILE.read_text(encoding="utf-8").strip()

    old_url = ""
    if URL_FILE.exists():
        old_url = URL_FILE.read_text(encoding="utf-8").strip()

    print(f"Стар хеш: {old_hash[:16] if old_hash else 'няма'}...")
    print(f"Нов хеш:  {new_hash[:16]}...")
    if old_url:
        print(f"Стар линк: {old_url}")
    print(f"Нов линк:  {current_url}")

    is_changed = (new_hash != old_hash) or (old_url and current_url != old_url)
    force_run = os.environ.get("FORCE_RUN", "").lower() == "true"

    if not is_changed and not force_run:
        print("✅ Няма промяна в програмата (линкът и съдържанието съвпадат с предния ден).")
        return

    print("⚠️ Открита е промяна в разписа!")

    # 1. Записваме новия хеш, URL и PDF
    HASH_FILE.write_text(new_hash, encoding="utf-8")
    URL_FILE.write_text(current_url, encoding="utf-8")
    with open(PDF_FILE, "wb") as f:
        f.write(content)

    # 2. Автоматично регенерираме index.html с новите данни и извличаме разликите
    success, changes = update_index_html_from_pdf(content)

    # 3. Маркираме за GitHub Actions че има промяна
    gh_env = os.environ.get("GITHUB_ENV")
    if gh_env:
        with open(gh_env, "a", encoding="utf-8") as f:
            f.write("SCHEDULE_CHANGED=true\n")

    # 4. Изпращане на имейл известие с точния списък на промените
    send_email_notification(new_hash, current_url, content, changes)

if __name__ == "__main__":
    main()
