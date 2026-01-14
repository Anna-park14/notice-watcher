import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
import os
import json
import time
import urllib.parse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import pymsteams
def normalize_title(title: str) -> str:
    return " ".join(title.split()).strip()

# Teams Webhook URL
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL")
if not TEAMS_WEBHOOK_URL:
    print("ERROR: TEAMS_WEBHOOK_URL not set in environment")
    raise SystemExit(1)
    
# ===== 설정 로드 =====
CONFIG_FILE = "config.json"
PERSIST_FILE = "sent_titles.json"

# 키워드: 환경변수 KEYWORDS 또는 기본값 (쉼표로 구분). OR 연산.
raw_keywords = os.environ.get("KEYWORDS", "바이오,헬스,임상,의료,의료기기,헬스케어,반려동물난치성")
KEYWORDS = [k.strip() for k in raw_keywords.split(",") if k.strip()]

# 이메일 (환경변수에서)
EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
    print("ERROR: EMAIL_ADDRESS or EMAIL_PASSWORD not set in environment")
    raise SystemExit(1)

# ===== 유틸: persistence =====
def load_sent():
    if os.path.exists(PERSIST_FILE):
        try:
            with open(PERSIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_sent(data):
    with open(PERSIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

sent_store = load_sent()

# ===== config load =====
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)
sites = config.get("sites", [])

# ===== 도우미: URL에서 고유 ID 추출 =====
def extract_unique_id(href):
    parsed = urllib.parse.urlparse(href)
    qs = urllib.parse.parse_qs(parsed.query)

    if "roRndUid" in qs and qs["roRndUid"]:
        return qs["roRndUid"][0]

    for key in ("pblancId","id","noticeId","seq","article_seq","idx"):
        if key in qs and qs[key]:
            return qs[key][0]

    return href

# ===== Teams 메시지 전송 =====
def send_teams_message(msg):
    teams_message = pymsteams.connectorcard(TEAMS_WEBHOOK_URL)
    teams_message.text(msg)
    teams_message.send()

# ===== 사이트별 검사 =====
def fetch_site_notices(site):
    name = site.get("name", "unknown")
    template = site.get("list_url_template")
    prefix = site.get("link_prefix", "")
    selector = site.get("item_selector", "a[title]")
    max_pages = min(site.get("pages_to_check", 1), 10)

    new_notices = []
    seen_within_run = set()
    seen_titles_in_site = set()

    use_selenium = "기업마당" in name

    for page in range(1, max_pages + 1):
        url = template.format(page=page)
        print(f"[{name}] Fetching URL: {url}")
        
        try:
            if use_selenium:
                options = Options()
                options.add_argument("--headless=new")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                driver.get(url)
                time.sleep(2)
                html = driver.page_source
                driver.quit()
                soup = BeautifulSoup(html, "html.parser")
            else:
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

            items = soup.select(selector)
           
            unique_items = list({a.get("href"): a for a in items if a.get("href")}.values())
    
            for a in unique_items:
                title = a.get_text(strip=True)
                href = a.get("href", "")
    
                print("Found link:", href, title)
    
                if not title or not href:
                    continue
    
                # ✅ 같은 사이트 내 동일 제목 중복 제거 (공지사항 방지)
                if title in seen_titles_in_site:
                    continue
                seen_titles_in_site.add(title)
    
                full_link = href if href.startswith("http") else urllib.parse.urljoin(prefix, href)

                # ✅ KHIDI는 제목을 UID로 사용 (URL 변경 방지)
                if name == "KHIDI":
                    uid = normalize_title(title)
                else:
                    uid = extract_unique_id(href)

    
                # ✅ 실행 중 중복 제거
                if (name, uid) in seen_within_run:
                    continue
                seen_within_run.add((name, uid))

    
                # ✅ 키워드 필터 + 누적 중복 제거
                if any(k.lower() in title.lower() for k in KEYWORDS):
                    seen = sent_store.get(name, [])
                    if uid not in seen:
                        print(f"[{name}] Adding notice: {title} ({full_link})")
                        new_notices.append((name, uid, title, full_link))
                            time.sleep(0.2)    

        except Exception as e:
            print(f"[{name}] error fetching page {page}: {e}")

    return new_notices

# ===== 전체 수집 =====
all_new = {}
for site in sites:
    site_name = site.get("name", "unknown")
    found = fetch_site_notices(site)
    if found:
        all_new.setdefault(site_name, []).extend(found)

# ===== 알림 송신 =====
if not any(all_new.values()):
    print("ℹ️ 새로운 공고 없음")
else:
    lines = []
    for site_name, notices in all_new.items():
        print(f"Preparing email body for {site_name} with {len(notices)} notices")
        lines.append(f"{site_name}")
        idx = 1
        for (_, uid, title, link) in notices:
            print(f" - {title}")
            lines.append(f"{idx}) {title}\n {link}")
            idx += 1
        lines.append("")

    body = "새로운 공고가 등록되었습니다.\n\n" + "\n".join(lines)
    subject = "[공고 알림] 새로운 공고 요약"

    try:
        send_teams_message(body)  # Teams 메시지 전송

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = EMAIL_ADDRESS

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        print("✅ 통합 이메일 발송 완료. 항목 수:")
        for site_name, notices in all_new.items():
            print(f" - {site_name}: {len(notices)}")

        for site_name, notices in all_new.items():
            seen = set(sent_store.get(site_name, []))
            for (_, uid, title, link) in notices:
                seen.add(uid)
            sent_store[site_name] = list(seen)

        save_sent(sent_store)

    except Exception as e:
        print("❗ 이메일 발송 중 오류 발생:", e)
