import os
import time
import feedparser
import requests
from google import genai
from google.genai import types
from google.genai.errors import APIError
from datetime import datetime, timedelta

# 🔑 從環境變數讀取金鑰
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GAS_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxriqjT0jcAZsqx20XVvWH_Sf8QV4vQwSueoh7M0gghT6HrSw6Aps2tFqbKTxWKn6o25Q/exec"

# 建立新 SDK 的 Client（會自動讀 GEMINI_API_KEY/GOOGLE_API_KEY）
client = genai.Client(api_key=GOOGLE_API_KEY)

# 定義要過濾的關鍵字列表（英文）
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


def fetch_and_summarize():
    """
    抓取 RSS 並篩選出昨天發佈且非人事任命的文章，呼叫 Gemini 生成摘要與影響評估。
    若遇到 API 配額耗盡，則停止後續摘要生成並回傳目前結果。
    """
    feeds = [
        "https://www.reinsurancene.ws/feed/",
        "https://www.artemis.bm/feed/",
        "https://www.insurancebusinessmag.com/reinsurance/rss/" 
    ]
    parts = []
    yesterday = (datetime.now() - timedelta(days=1)).date()

    for url in feeds:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            # 僅處理昨天發佈
            pub_parsed = getattr(entry, 'published_parsed', None)
            if not pub_parsed:
                continue
            pub_date = datetime.fromtimestamp(time.mktime(pub_parsed)).date()
            if pub_date != yesterday:
                continue

            # 過濾與人事任命相關的文章
            if is_personnel_article(entry):
                continue

            # 呼叫 Gemini API 生成摘要與影響評估（加入忽略人事任命提示）
            prompt = (
                "你是一個在再保險領域深耕多年的核保人員，擁有Swiss Re/Hannover Re/Munich Re工作經驗，"
                "請將以下內容摘要成 3-5 句並以繁體中文輸出，"
                "再分析對台灣保險業/再保險的影響（30 字以內）。"
                "請忽略與人事任命相關的內容：\n\n" + entry.summary
            )
            try:
                resp = client.models.generate_content(
                   model="gemini-2.5-flash",
                   contents=prompt,
                   # 可選：統一回傳格式、控制輸出長度等
                    config=types.GenerateContentConfig(
                        max_output_tokens=512
                    ),
                )
               text = (resp.text or "").strip()
           except APIError as e:
                # 針對配額/暫時性錯誤做簡單退避重試（示意）
              if getattr(e, "code", None) in (429, 500, 503):
                   time.sleep(2)  # 可改成指數退避
                 continue
                print("❌ API 配額已用盡，停止摘要生成。")
                break
            except Exception as e:
                print(f"❌ 生成過程發生錯誤：{e}")
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

    print(f"Debug: 找到 {len(parts)} 篇昨天({yesterday})的新聞（已排除人事任命）")
    return "\n\n".join(parts)


def send_via_gas(to: str, subject: str, body: str):
    """
    透過 Apps Script Webhook 寄信，to: 收件人, subject: 主旨, body: 內容
    """
    payload = {"to": to, "subject": subject, "body": body}
    r = requests.post(GAS_WEBHOOK_URL, json=payload)
    if r.status_code == 200 and r.json().get('status') == 'OK':
        print("✅ 寄信成功！")
    else:
        print("❌ 寄信失敗：", r.status_code, r.text)


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
