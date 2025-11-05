# watch_notice.py
import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
import os
import json

# ===== 사용자 설정 =====
KEYWORD = "2025"
URL = "https://www.bizinfo.go.kr/web/lay1/bbs/S1T122C128/AS/74/list.do?schEndAt=N"
PERSIST_FILE = "sent_titles.json"

# ===== GitHub Secrets 에서 불러오기 =====
SENDER_EMAIL = os.environ.get("EMAIL_ADDRESS")
RECEIVER_EMAIL = os.environ.get("EMAIL_ADDRESS")
APP_PASSWORD = os.environ.get("EMAIL_PASSWORD")

# ===== sent_titles 중복 방지용 =====
def load_sent_titles():
    if os.path.exists(PERSIST_FILE):
        try:
            with open(PERSIST_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent_titles(titles):
    with open(PERSIST_FILE, "w", encoding="utf-8") as f:
        json.dump(list(titles), f, ensure_ascii=False, indent=2)

sent_titles = load_sent_titles()

# ===== 이메일 발송 =====
def send_email(title, link):
    body = f"새로운 공고가 등록되었습니다.\n\n제목: {title}\n링크: {link}"
    msg = MIMEText(body)
    msg['Subject'] = f"[공고 알림] {title}"
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(SENDER_EMAIL, APP_PASSWORD)
        smtp.send_message(msg)
    print("✅ 이메일 발송 완료:", title)

# ===== 공고 감지 =====
def check_notice():
    try:
        resp = requests.get(URL, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        notices = soup.select("td.tit > a")
        new_found = False

        for n in notices:
            title = n.get_text(strip=True)
            href = n.get("href", "")
            link = "https://www.bizinfo.go.kr" + href if href.startswith("/") else href

            if KEYWORD in title and title not in sent_titles:
                print("🔎 발견:", title)
                send_email(title, link)
                sent_titles.add(title)
                new_found = True

        if new_found:
            save_sent_titles(sent_titles)
        else:
            print("ℹ️ 새로운 공고 없음")

    except Exception as e:
        print("❗ 오류 발생:", e)

# ===== GitHub Actions 실행 시 1회만 실행됨 =====
if __name__ == "__main__":
    print("🚀 공고 모니터링 실행 중... 키워드:", KEYWORD)
    check_notice()
