import os
import json
import logging
import feedparser
import requests
from datetime import datetime
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 設定 ---
# ニュース系（最新動向・発表）
NEWS_FEEDS = [
    "https://rss.itmedia.co.jp/rss/2.0/ait.xml",        # ITmedia AI+
    "https://ledge.ai/feed/",                             # Ledge.ai
    "https://ainow.ai/feed/",                             # AINOW
    "https://www.watch.impress.co.jp/data/rss/1.0/ipw/feed.rdf", # Impress Watch
    "https://feeds.feedburner.com/moongift",               # テック系ニュース
    "https://gigazine.net/news/rss_2.0/",                  # GIGAZINE
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", # The Verge AI
    "https://www.wired.com/feed/tag/ai/latest/rss",        # WIRED AI
]

# Tips・活用テクニック系
TIPS_FEEDS = [
    "https://zenn.dev/topics/chatgpt/feed",
    "https://zenn.dev/topics/ai/feed",
    "https://qiita.com/tags/chatgpt/feed",
    "https://qiita.com/tags/ai/feed"
]

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")


def get_posted_titles_from_notion():
    logging.info("Notionから過去の投稿履歴を確認します...")
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return set()

    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    data = {"page_size": 30}
    posted_titles = set()
    
    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            results = response.json().get("results", [])
            for page in results:
                props = page.get("properties", {})
                for prop_name, prop_data in props.items():
                    if prop_data.get("type") == "title":
                        title_arr = prop_data.get("title", [])
                        if title_arr:
                            raw_title = title_arr[0].get("plain_text", "")
                            clean_title = raw_title.replace("【デイリーAIニュース】", "").strip()
                            posted_titles.add(clean_title)
            logging.info(f"Notionから {len(posted_titles)} 件の過去タイトルを取得しました。")
        else:
            logging.warning("Notionからの履歴取得に失敗しました。")
    except Exception as e:
        logging.error(f"Notionからの履歴取得エラー: {e}")
    return posted_titles


def fetch_rss_feeds(urls, exclude_titles, tag=""):
    articles = []
    seen_urls = set()
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                link = entry.link
                title = entry.title
                is_excluded = False
                for ex_title in exclude_titles:
                    if (title in ex_title) or (ex_title in title):
                        is_excluded = True
                        break
                if not is_excluded and link not in seen_urls:
                    seen_urls.add(link)
                    articles.append({
                        "title": title,
                        "link": link,
                        "published": getattr(entry, "published", getattr(entry, "updated", "")),
                        "summary": getattr(entry, "summary", "")[:200],
                        "category": tag
                    })
        except Exception as e:
            logging.error(f"フィード取得エラー ({url}): {e}")
    return articles


def generate_news_article(news_articles, tips_articles):
    logging.info("Gemini APIを使用して記事の選定と生成を開始します...")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 環境変数が設定されていません。")

    news_text = ""
    for idx, a in enumerate(news_articles):
        news_text += f"[ニュース候補{idx+1}] タイトル: {a['title']}\nURL: {a['link']}\n概要: {a['summary']}\n\n"

    tips_text = ""
    for idx, a in enumerate(tips_articles):
        tips_text += f"[Tips候補{idx+1}] タイトル: {a['title']}\nURL: {a['link']}\n概要: {a['summary']}\n\n"

    system_instruction = """あなたは最新のAIニュースを配信する編集アシスタントです。
AIに詳しくない受講生にAIを身近に感じてもらうため、専門用語を避け、親しみやすい解説を心がけてください。

【最重要ルール：記事の選び方】
以下の構成で、合計3件の記事を選んでください。
★ 記事1・記事2 → 必ず【ニュース記事リスト】から選ぶこと（新サービス発表、AI導入事例、大型提携、新機能リリースなどの「最新ニュース」）
★ 記事3 → 必ず【Tips・テクニック記事リスト】から選ぶこと（プロンプト術、効率化テクニック、活用ノウハウなどの「実践Tips」）
※ 3件すべて異なるトピックにすること（重複禁止）。
※ 3件すべて最後まで省略せずに書き切ること（「概要」だけで止めるのは禁止）。

【ピックアップ基準】
優先度高: 読者が「明日から使ってみたい！」と思えるニュース
- 身近なサービスへのAI導入
- 有名なAIツールの「無料」または簡単な新機能
- 面倒な作業が劇的に楽になる事例
- すぐに使えるAIツールのプロンプト術

【Markdown構成（必ずこの通りにすること）】
## 💡 概要
（ニュースの要点を2〜3行でまとめる。自分に関係ありそうと思わせる文章）
---
## 🧐 詳しい解説
**一言でいうと：** （一番伝えたいことを1行で）
- **（ポイント1のタイトル）**
  （ポイント1の具体的な内容を説明）
- **（ポイント2のタイトル）**
  （ポイント2の具体的な内容を説明）
---
## ⚒️ 具体的な使い方
1. **（ステップ1）**
   （具体的なアクションを説明）
2. **（ステップ2）**
   （具体的なアクションを説明）
---
## 🔗 リンク
* **参考記事:** [（元のニュース記事のタイトル）]（（元のニュース記事のURL））
"""

    prompt = f"""以下の2種類のリストから、ルールに従って合計3件選び、それぞれの解説をMarkdownで生成してください。

【ニュース記事リスト（ここから2件選ぶこと）】
{news_text}

【Tips・テクニック記事リスト（ここから1件選ぶこと）】
{tips_text}"""

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    response_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "url": {"type": "string"},
                "markdown": {"type": "string", "description": "必ず『💡 概要』『🧐 詳しい解説』『⚒️ 具体的な使い方』『🔗 リンク』の全構成を含めた完全なMarkdown文章を書くこと"}
            },
            "required": ["title", "url", "markdown"]
        }
    }

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
            response_mime_type="application/json",
            response_schema=response_schema
        )
    )
    return response.text

def parse_generated_content(content):
    try:
        articles_data = json.loads(content)
        if isinstance(articles_data, list):
            return articles_data
        return []
    except json.JSONDecodeError as e:
        logging.error(f"JSONパースエラー: {e}")
        return []

def create_notion_page(title, markdown_content):
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return False
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    def create_text_blocks(text):
        blocks = []
        for para in text.split('\n\n'):
            if not para.strip(): continue
            if para.startswith('## '):
                blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": para.replace('## ', '')[:2000]}}]}})
            elif para.startswith('---'):
                blocks.append({"object": "block", "type": "divider", "divider": {}})
            else:
                blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[:2000]}}]}})
        return blocks

    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "title": {"title": [{"text": {"content": f"【デイリーAIニュース】{title}"}}]}
        },
        "children": create_text_blocks(markdown_content)
    }

    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        logging.info(f"投稿成功: {title[:15]}...")
        return True
    else:
        logging.error(f"Notion投稿エラー: {response.status_code}")
        return False

def main():
    try:
        # 0. 過去の投稿タイトルを取得
        posted_titles = get_posted_titles_from_notion()

        # 1. ニュース系とTips系を分けて収集
        news_articles = fetch_rss_feeds(NEWS_FEEDS, posted_titles, tag="news")
        tips_articles = fetch_rss_feeds(TIPS_FEEDS, posted_titles, tag="tips")
        
        logging.info(f"ニュース系: {len(news_articles)}件 / Tips系: {len(tips_articles)}件")
        
        if not news_articles and not tips_articles:
            logging.info("新しい記事候補がありません。すべて配信済みか、RSSの更新がありません。")
            return

        # 2. AIで記事を選定・生成（ニュース2件 + Tips1件）
        raw_output = generate_news_article(news_articles, tips_articles)
        generated_articles = parse_generated_content(raw_output)
        
        if not generated_articles:
            logging.error("記事の生成に失敗しました。")
            return
            
        logging.info(f"{len(generated_articles)}件の記事が生成されました。Notionへ順番に投稿します。")
        
        # 3. Notionへ投稿
        for item in generated_articles:
            title = item.get("title", "無題")
            markdown_content = item.get("markdown", "")
            if markdown_content:
                create_notion_page(title, markdown_content)
                
    except Exception as e:
        logging.error(f"実行中にエラーが発生しました: {e}")
        raise

if __name__ == "__main__":
    main()
