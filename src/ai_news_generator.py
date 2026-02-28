import os
import json
import logging
import feedparser
import requests
from datetime import datetime
from google import genai
from google.genai import types

# ログの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 設定 ---
RSS_FEEDS = [
    "https://rss.itmedia.co.jp/rss/2.0/ait.xml",
    "https://ledge.ai/feed/",
    "https://ainow.ai/feed/",
    "https://zenn.dev/topics/chatgpt/feed",
    "https://zenn.dev/topics/ai/feed",
    "https://qiita.com/tags/chatgpt/feed",
    "https://qiita.com/tags/ai/feed"
]

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

def fetch_rss_feeds(urls):
    logging.info("RSSフィードからの記事取得を開始します...")
    articles = []
    # 重複防止のためにURLのセットで管理
    seen_urls = set()
    
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                link = entry.link
                if link not in seen_urls:
                    seen_urls.add(link)
                    articles.append({
                        "title": entry.title,
                        "link": link,
                        "published": getattr(entry, "published", getattr(entry, "updated", "")),
                        "summary": getattr(entry, "summary", "")[:200]
                    })
        except Exception as e:
            logging.error(f"フィード取得エラー ({url}): {e}")
    logging.info(f"合計 {len(articles)} 件の重複のない記事を取得しました。")
    return articles

def generate_news_article(articles):
    logging.info("Gemini APIを使用して記事の選定と生成を開始します...")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 環境変数が設定されていません。")

    articles_text = ""
    for idx, article in enumerate(articles):
        articles_text += f"[記事番号: {idx+1}]\nタイトル: {article['title']}\nURL: {article['link']}\n概要: {article['summary']}\n\n"

    system_instruction = """あなたは最新のAIニュースを配信する編集アシスタントです。
AIに詳しくない受講生にAIを身近に感じてもらうため、専門用語を避け、親しみやすい解説を心がけてください。

【最重要ルール】
1. 必ず【今日の記事リスト】から、**それぞれ全くトピックが異なる別々の記事（重複禁止）**を「3件」選んでください。
2. 3件とも、以下の【Markdown構成】を最後まで1行も省略せずに書き切ってください（「概要」だけで書き止めることは禁止です。必ず「詳しい解説」「具体的な使い方」「リンク」全て含めてください）。

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

    prompt = f"以下のリストから、トピック・URLが重複しないように3件の異なる記事を選び、それぞれの解説をMarkdownで生成してください。\n\n【今日の記事リスト】\n{articles_text}"

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # JSONの出力プロパティを細かく指定し、途中での書き漏らしをシステム制御で防ぐ
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
        else:
            logging.warning("出力が配列ではありませんでした。")
            return []
    except json.JSONDecodeError as e:
        logging.error(f"JSONパースエラー: {e}\n出力内容: {content}")
        return []

def create_notion_page(title, markdown_content):
    logging.info(f"Notionへの投稿を開始します: {title}")
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logging.warning("Notion APIまたはDB IDが未設定。投稿スキップ。")
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
            "title": {  
                "title": [
                    {
                        "text": {
                            "content": f"【デイリーAIニュース】{title}"
                        }
                    }
                ]
            }
        },
        "children": create_text_blocks(markdown_content)
    }

    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        logging.info("Notionへの投稿が成功しました！")
        return True
    else:
        logging.error(f"Notion投稿エラー: {response.status_code} - {response.text}")
        return False

def main():
    try:
        articles = fetch_rss_feeds(RSS_FEEDS)
        if not articles: return

        raw_output = generate_news_article(articles)
        generated_articles = parse_generated_content(raw_output)
        
        if not generated_articles:
            logging.error("記事の生成に失敗しました。")
            return
            
        logging.info(f"{len(generated_articles)}件の記事が生成されました。Notionへ順番に投稿します。")
        
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
