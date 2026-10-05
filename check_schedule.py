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


PDF_URL = "https://tu-sofia.bg/api/documents/download?path=schedules%2F33NKmaZZD4fIaxF6UXfHxqwCfvh5hvhsWgNrI4m2.pdf&locale=bg"
HASH_FILE = Path("schedule_hash.txt")
PDF_FILE = Path("latest_schedule.pdf")
HTML_FILE = Path("index.html")

def get_remote_pdf():
    print(f"Изтегляне на разписа от ТУ-София...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    resp = requests.get(PDF_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content

def calculate_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def send_email_notification(new_hash: str, pdf_bytes: bytes):
    email_to = os.environ.get("EMAIL_TO")
    email_user = os.environ.get("EMAIL_USER")
    email_pass = os.environ.get("EMAIL_PASS")
    gh_pages_url = os.environ.get("GITHUB_PAGES_URL", "")

    if not (email_to and email_user and email_pass):
        print("[INFO] Имейл известието е пропуснато (не са конфигурирани EMAIL_TO, EMAIL_USER или EMAIL_PASS).")
        return

    print(f"Изпращане на имейл до {email_to}...")
    msg = EmailMessage()
    msg["Subject"] = "🔔 [ТУ-София] Открита е промяна в разписа за МБИС!"
    msg["From"] = email_user
    msg["To"] = email_to

    pages_link_html = f'<p>🌐 <b>Онлайн програма (GitHub Pages):</b> <a href="{gh_pages_url}">{gh_pages_url}</a></p>' if gh_pages_url else ""

    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <h2 style="color: #2563EB;">Здравей! Открита е промяна в разписа на ТУ-София.</h2>
        <p>Автоматичната проверка откри нов вариант на разписа за специалност <b>МБИС (Магистър)</b> в сайта на ТУ-София.</p>
        <p><b>Нов контролен хеш (SHA-256):</b> <code>{new_hash[:16]}...</code></p>
        {pages_link_html}
        <p>В прикачения файл ще намериш актуализирания <b>PDF разпис</b> от университета, както и обновения интерактивен <b>index.html</b>.</p>
        <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
        <small style="color: #888;">Това съобщение е изпратено автоматично от твоя GitHub Actions робот.</small>
      </body>
    </html>
    """
    msg.set_content("Открита е промяна в разписа на ТУ-София за специалност МБИС. Виж прикачените файлове.")
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
    try:
        content = get_remote_pdf()
    except Exception as e:
        print(f"[ГРЕШКА] Неуспешно изтегляне на PDF от ТУ-София: {e}")
        sys.exit(1)

    new_hash = calculate_hash(content)
    old_hash = ""
    if HASH_FILE.exists():
        old_hash = HASH_FILE.read_text(encoding="utf-8").strip()

    print(f"Стар хеш: {old_hash[:16] if old_hash else 'няма'}...")
    print(f"Нов хеш:  {new_hash[:16]}...")

    # Принудително тестване (ако е зададен FORCE_RUN=true)
    force_run = os.environ.get("FORCE_RUN", "").lower() == "true"

    if new_hash == old_hash and not force_run:
        print("✅ Няма промяна в програмата спрямо предния ден.")
        return

    print("⚠️ Открита е промяна в разписа!")
    
    # Записваме новия хеш и PDF
    HASH_FILE.write_text(new_hash, encoding="utf-8")
    with open(PDF_FILE, "wb") as f:
        f.write(content)

    # Маркираме за GitHub Actions че има промяна
    gh_env = os.environ.get("GITHUB_ENV")
    if gh_env:
        with open(gh_env, "a", encoding="utf-8") as f:
            f.write("SCHEDULE_CHANGED=true\n")

    # Изпращане на имейл известие
    send_email_notification(new_hash, content)

if __name__ == "__main__":
    main()
