# watch_notice.py

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

# ===== 설정 로드 =====
CONFIG_FILE = "config.json"
PERSIST_FILE = "sent_titles.json"

# 키워드: 환경변수 KEYWORDS 또는 기본값 (쉼표로 구분). OR 연산.
raw_keywords = os.environ.get("KEYWORDS", "바이오,헬스,임상")
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

sent_store = load_sent()  # 구조: { "사이트이름": ["id1","id2", ...], ... }

# ===== config load =====
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)
sites = config.get("sites", [])

# ===== 도우미: URL에서 고유 ID 추출 =====
def extract_unique_id(href):
    parsed = urllib.parse.urlparse(href)
    qs = urllib.parse.parse_qs(parsed.query)
    
    # NTIS 공고 고유값
    if "roRndUid" in qs and qs["roRndUid"]:
        return qs["roRndUid"][0]
    
    # 다른 사이트들 공통 처리
    for key in ("pblancId","id","noticeId","seq","article_seq","idx"):
        if key in qs and qs[key]:
            return qs[key][0]
    
    return href  # fallback

# ===== 사이트별 검사 =====
def fetch_site_notices(site):
    name = site.get("name", "unknown")
    template = site.get("list_url_template")
    prefix = site.get("link_prefix", "")
    selector = site.get("item_selector", "a[title]")
    max_pages = min(site.get("pages_to_check", 1), 10)

    new_notices = []
    seen_within_run = set()  # ✅ 새로 추가: 이번 실행 내 중복 제거

    use_selenium = "기업마당" in name
     
    for page in range(1, max_pages+1):
        url = template.format(page=page)
        print(f"[{name}] Fetching URL: {url}")  # 로그 출력 추가
        try:
            if use_selenium:
                options = Options()
                options.add_argument("--headless=new")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
                driver.get(url)
                time.sleep(2)  # 페이지 로딩 대기
                html = driver.page_source
                driver.quit()
                soup = BeautifulSoup(html, "html.parser")
            else:
                resp = requests.get(url, timeout=20)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

            items = soup.select(selector)

            unique_items = list({a.get("href"): a for a in items}.values())
            
            for a in unique_items:
                print("Found link:", a.get("href"), a.get_text(strip=True))

                title = a.get_text(strip=True)
                href = a.get("href", "")
                if not href:
                    continue

                full_link = href if href.startswith("http") else urllib.parse.urljoin(prefix, href)
                uid = extract_unique_id(href)

                if (title, full_link) in seen_within_run:
                    continue
                seen_within_run.add((title, full_link))

                if any(k.lower() in title.lower() for k in KEYWORDS):
                    seen = sent_store.get(name, [])
                    if uid not in seen:
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

# ===== 필터/정리: 이미 보낸 공고 제외는 위에서 됨. 이제 이메일로 묶어서 보낼지 판단 =====
if not any(all_new.values()):
    print("ℹ️ 새로운 공고 없음")
else:
    # 이메일 본문 작성: 사이트별 그룹화
    lines = []
    for site_name, notices in all_new.items():
        lines.append(f"{site_name}")
        idx = 1
        for (_, uid, title, link) in notices:
            lines.append(f"{idx}) {title}\n {link}")
            idx += 1
        lines.append("")  # 빈줄

    body = "새로운 공고가 등록되었습니다.\n\n" + "\n".join(lines)
    subject = "[공고 알림] 새로운 공고 요약"

    # 이메일 전송
    try:
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

        # 발송 성공 시 sent_store 갱신
        for site_name, notices in all_new.items():
            seen = set(sent_store.get(site_name, []))
            for (_, uid, title, link) in notices:
                seen.add(uid)
            sent_store[site_name] = list(seen)

        save_sent(sent_store)

    except Exception as e:
        print("❗ 이메일 발송 중 오류 발생:", e)
