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

【ピックアップ基準】（最優先1件のみ選ぶこと）
優先度高: 読者が「明日から使ってみたい！」と思えるニュース
- 身近なサービスへのAI導入
- 有名なAIツールの「無料」または「簡単な」新機能
- 面倒な作業が劇的に楽になる事例
- すぐに使えるAIツールの活用テクニック・コツ (例: ChatGPTの良い回答を引き出すプロンプト術)
優先度中: 社会的な影響が大きい話題など、未来を感じられるニュース
優先度低(避ける): 専門的すぎる技術論文、過度に扇情的な内容

【出力フォーマット】 (Markdown形式)
最初の行に { "title": "...", "url": "..." } という形式のJSONを配置し、その次の行から以下のMarkdownを出力してください。

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
"""

    prompt = f"以下の今日のニュース/記事リストから、基準に最も合致するものを1つ選び、指定のフォーマットで解説記事を生成してください。\n\n【今日の記事リスト】\n{articles_text}"

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
        )
    )
    return response.text

def parse_generated_content(content):
    lines = content.strip().split('\n')
    meta_json_str = lines[0]
    
    if not meta_json_str.strip().startswith('{'):
        match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
        if match:
             meta_json_str = match.group(1)
             markdown_body = content.replace(f"```json\n{meta_json_str}\n```", "").strip()
        else:
             meta_json_str = '{"title": "AI News", "url": ""}'
             markdown_body = content
    else:
        markdown_body = '\n'.join(lines[1:]).strip()
        
    try:
        metadata = json.loads(meta_json_str)
    except json.JSONDecodeError:
        metadata = {"title": "本日のAIピックアップニュース", "url": ""}
        
    return metadata, markdown_body

def create_notion_page(title, markdown_content):
    logging.info("Notionへの投稿を開始します...")
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

    # 必須プロパティのみに削り、タイトルプロパティ名を動的に対応するための安全なペイロード
    # （Notionの初期DBに確実にある title プロパティである「名前」だけを送る）
    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名前": {  # 新規作成したDBのデフォルトのタイトル列の名前は「名前」
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
        metadata, markdown_body = parse_generated_content(raw_output)
        create_notion_page(metadata.get('title', '無題'), markdown_body)
    except Exception as e:
        logging.error(f"実行中にエラーが発生しました: {e}")
        raise

if __name__ == "__main__":
    main()
