import os
import re
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
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                articles.append({
                    "title": entry.title,
                    "link": entry.link,
                    "published": getattr(entry, "published", getattr(entry, "updated", "")),
                    "summary": getattr(entry, "summary", "")[:200]
                })
        except Exception as e:
            logging.error(f"フィード取得エラー ({url}): {e}")
    logging.info(f"合計 {len(articles)} 件の記事を取得しました。")
    return articles

def generate_news_article(articles):
    logging.info("Gemini APIを使用して記事の選定と生成を開始します...")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 環境変数が設定されていません。")

    articles_text = ""
    for idx, article in enumerate(articles):
        articles_text += f"[{idx+1}] タイトル: {article['title']}\nURL: {article['link']}\n概要: {article['summary']}\n\n"

    system_instruction = """あなたは、AIスクール「AI+」の受講生向けに、最新のAIニュースを配信する編集アシスタントです。AIに詳しくない受講生の学習意欲を高め、AIを身近に感じてもらうことが目的です。
専門用語を避け、親しみやすく、丁寧な解説を心がけてください。

【ピックアップ基準】（最も重要な3件を選ぶこと）
優先度高: 読者が「明日から使ってみたい！」と思えるニュース
- 身近なサービスへのAI導入
- 有名なAIツールの「無料」または「簡単な」新機能
- 面倒な作業が劇的に楽になる事例
- すぐに使えるAIツールの活用テクニック・コツ (例: ChatGPTの良い回答を引き出すプロンプト術)
優先度中: 社会的な影響が大きい話題など、未来を感じられるニュース
優先度低(避ける): 専門的すぎる技術論文、過度に扇情的な内容

【出力フォーマット要求】
1. 必ず、基準に最も合致する異なるニュースを「3件」選んでください。
2. それぞれのニュースについて、必ず「タイトル(title)」と「参照元URL(url)」と「解説本文のMarkdown(markdown)」を持つJSONオブジェクトを作成してください。
3. 最終的な出力は、それら3件を含むJSONの配列（リスト）形式のみとしてください。他の挨拶や余計な文字列は一切含めないでください。

【Markdownフォーマット（各記事の markdown プロパティの書式）】
## 💡 概要
（ニュースの要点を2〜3行でまとめる。読者が「自分に関係ありそう」と思えるように書く）
---
## 🧐 詳しい解説
**一言でいうと：** （このニュースで一番伝えたいことを一行で書く）
- **（ポイント1のタイトル）**
  （ポイント1の具体的な内容を箇条書きで説明）
- **（ポイント2のタイトル）**
  （ポイント2の具体的な内容を箇条書きで説明）
---
## ⚒️ 具体的な使い方
1. **（ステップ1）**
   （具体的なアクションを説明）
2. **（ステップ2）**
   （具体的なアクションを説明）
---
## 🔗 リンク
* **参考記事:** [（元のニュース記事のタイトル）]（（元のニュース記事のURL））

【厳密な出力形式】
以下のようなJSON配列で出力すること。
[
  {
    "title": "記事1のタイトル",
    "url": "記事1のURL",
    "markdown": "記事1のマークダウン本文\\n## 💡 概要\\n..."
  },
  {
    ...記事2...
  },
  {
    ...記事3...
  }
]
"""

    prompt = f"以下の今日のニュース/記事リストから、基準に最も合致するものを3つ選び、指定のJSON配列形式で出力してください。\n\n【今日の記事リスト】\n{articles_text}"

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
            # 必ずJSONで返すように指定
            response_mime_type="application/json",
        )
    )
    return response.text

def parse_generated_content(content):
    """3件分の配列JSONをパースして返す"""
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

        # 3件分のデータがJSON配列で返ってくる
        raw_output = generate_news_article(articles)
        generated_articles = parse_generated_content(raw_output)
        
        if not generated_articles:
            logging.error("記事の生成に失敗しました。")
            return
            
        logging.info(f"{len(generated_articles)}件の記事が生成されました。Notionへ順番に投稿します。")
        
        # 3件をそれぞれNotionに投稿するループ
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
