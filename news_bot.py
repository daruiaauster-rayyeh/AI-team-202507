import os
import time
import feedparser
import requests
from datetime import datetime, timedelta
from google import genai
from google.genai import types
from google.genai.errors import APIError

# 🔑 從環境變數讀取金鑰與 Webhook
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GAS_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxriqjT0jcAZsqx20XVvWH_Sf8QV4vQwSueoh7M0gghT6HrSw6Aps2tFqbKTxWKn6o25Q/exec"

# 初始化 Google GenAI Client
client = genai.Client(api_key=GOOGLE_API_KEY)

# 要過濾的人事任命關鍵字
FILTER_KEYWORDS = [
    "appointment", "appointed", "personnel", "hiring", "staff change"
]


def is_personnel_article(entry) -> bool:
    """
    判斷文章標題或摘要中是否包含人事任命相關關鍵字
    """
    title_lower = entry.title.lower()
    summary_lower = getattr(entry, 'summary', '').lower()
    for kw in FILTER_KEYWORDS:
        if kw in title_lower or kw in summary_lower:
            return True
    return False


def summarize_with_gemini(text: str) -> str:
    """
    使用 Gemini API 生成摘要與影響評估
    """
    prompt = (
        "你是一個在再保險領域深耕多年的核保人員，擁有Swiss Re/Hannover Re/Munich Re工作經驗，"
        "請將以下內容摘要成 3-5 句並以繁體中文輸出，"
        "再分析對台灣保險業/再保險的影響（30 字以內）。"
        "請忽略與人事任命相關的內容：\n\n" + text
    )

    for attempt in range(3):  # 最多嘗試三次
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=512
                ),
            )
            text = (resp.text or "").strip()
            return text

        except APIError as e:
            # 若為暫時性錯誤（配額、伺服器問題）則重試
            if getattr(e, "code", None) in (429, 500, 503) and attempt < 2:
                wait_time = 2 * (attempt + 1)
                print(f"⚠️ API 錯誤 {e.code}，{wait_time} 秒後重試...")
                time.sleep(wait_time)
                continue
            print(f"❌ API 錯誤：{e}")
            break

    return ""


def fetch_and_summarize():
    """
    抓取 RSS，篩選出昨天發佈且非人事任命的文章，並生成摘要與影響評估。
    """
    feeds = [
        "https://www.reinsurancene.ws/feed/",
        "https://www.artemis.bm/feed/",
        "https://www.insurancebusinessmag.com/reinsurance/rss/",
    ]

    parts = []
    yesterday = (datetime.now() - timedelta(days=1)).date()
    seen_links = set()  # 去重用

    for url in feeds:
        print(f"🔍 抓取 RSS：{url}")
        feed = feedparser.parse(url)

        for entry in feed.entries:
            pub_parsed = getattr(entry, 'published_parsed', None)
            if not pub_parsed:
                continue

            pub_date = datetime.fromtimestamp(time.mktime(pub_parsed)).date()
            if pub_date != yesterday:
                continue

            # 過濾與人事任命相關的文章
            if is_personnel_article(entry):
                continue

            # 避免重複處理
            if entry.link in seen_links:
                continue
            seen_links.add(entry.link)

            print(f"📰 正在摘要：{entry.title}")
            text = summarize_with_gemini(getattr(entry, 'summary', ''))

            if not text:
                continue

            # 拆「摘要」與「影響」
            if "影響：" in text:
                summary, impact = text.split("影響：", 1)
                summary = summary.replace("摘要：", "").strip()
                impact = impact.strip()
            else:
                summary, impact = text.strip(), "無影響評估"

            parts.append(
                f"📰 {entry.title}\n"
                f"🔗 {entry.link}\n"
                f"📖 摘要：{summary}\n"
                f"⚖️ 影響：{impact}"
            )

    print(f"✅ 找到 {len(parts)} 篇昨天({yesterday})的新聞（已排除人事任命）")
    return "\n\n".join(parts)


def send_via_gas(to: str, subject: str, body: str):
    """
    透過 Google Apps Script Webhook 寄信
    """
    payload = {"to": to, "subject": subject, "body": body}
    try:
        r = requests.post(GAS_WEBHOOK_URL, json=payload)
        if r.status_code == 200 and r.json().get('status') == 'OK':
            print("✅ 寄信成功！")
        else:
            print("❌ 寄信失敗：", r.status_code, r.text)
    except Exception as e:
        print(f"❌ 寄信過程發生錯誤：{e}")


if __name__ == "__main__":
    body = fetch_and_summarize()
    yesterday = (datetime.now() - timedelta(days=1)).date()

    if not body.strip():
        print(f"ℹ️ {yesterday} 沒有任何新聞，跳過寄信。")
    else:
        print("📢 摘要內容：\n", body)
        today_str = datetime.now().strftime("%Y-%m-%d")
        subject = f"AI Reinsurance News Daily Update - {today_str}"
        send_via_gas("rayyeh@centralre.com", subject, body)
