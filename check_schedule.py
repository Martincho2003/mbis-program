import os
import sys
import hashlib
import requests
import smtplib
from email.message import EmailMessage
from pathlib import Path

# Поддръжка на кирилица в конзолата при Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCHEDULES_API_URL = "https://tu-sofia.bg/api/schedules"
FALLBACK_PDF_URL = "https://tu-sofia.bg/api/documents/download?path=schedules%2F33NKmaZZD4fIaxF6UXfHxqwCfvh5hvhsWgNrI4m2.pdf&locale=bg"

HASH_FILE = Path("schedule_hash.txt")
URL_FILE = Path("schedule_url.txt")
PDF_FILE = Path("latest_schedule.pdf")
HTML_FILE = Path("index.html")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_latest_schedule_url() -> str:
    """
    Динамично извлича актуалния линк към разписа за специалност
    'Мениджмънт и бизнес информационни системи' директно от API-то на сайта на ТУ-София,
    което захранва бутоните на официалната страница /bg/weekly-schedule.
    """
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

def send_email_notification(new_hash: str, pdf_url: str, pdf_bytes: bytes):
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

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2563EB;">Здравей! Открита е промяна в разписа на ТУ-София.</h2>
        <p>Автоматичната проверка откри обновяване на разписа за специалност <b>МБИС (Магистър)</b> на сайта на ТУ-София.</p>
        <p>📥 <b>Директен линк от сайта:</b> <a href="{pdf_url}">{pdf_url}</a></p>
        <p>🔑 <b>Контролен хеш на новия файл (SHA-256):</b> <code>{new_hash[:16]}...</code></p>
        {pages_link_html}
        <p>В прикачения файл ще намериш новия <b>PDF разпис</b> от университета, както и обновения интерактивен <b>index.html</b>.</p>
        <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
        <small style="color: #888;">Това съобщение е изпратено автоматично от твоя GitHub Actions робот.</small>
      </body>
    </html>
    """
    msg.set_content(f"Открита е промяна в разписа на ТУ-София за специалност МБИС.\nЛинк: {pdf_url}\nВиж прикачените файлове.")
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

    # Проверка дали има промяна или в хеша, или в самия URL
    is_changed = (new_hash != old_hash) or (old_url and current_url != old_url)

    # Принудително тестване (ако е зададен FORCE_RUN=true)
    force_run = os.environ.get("FORCE_RUN", "").lower() == "true"

    if not is_changed and not force_run:
        print("✅ Няма промяна в програмата (линкът и съдържанието съвпадат с предния ден).")
        return

    print("⚠️ Открита е промяна в разписа!")

    # Записваме новия хеш, URL и PDF
    HASH_FILE.write_text(new_hash, encoding="utf-8")
    URL_FILE.write_text(current_url, encoding="utf-8")
    with open(PDF_FILE, "wb") as f:
        f.write(content)

    # Маркираме за GitHub Actions че има промяна
    gh_env = os.environ.get("GITHUB_ENV")
    if gh_env:
        with open(gh_env, "a", encoding="utf-8") as f:
            f.write("SCHEDULE_CHANGED=true\n")

    # Изпращане на имейл известие
    send_email_notification(new_hash, current_url, content)

if __name__ == "__main__":
    main()
