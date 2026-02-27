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
# ニュース収集用のRSSフィード一覧
RSS_FEEDS = [
    "https://rss.itmedia.co.jp/rss/2.0/ait.xml", # ITmedia
    "https://ledge.ai/feed/", # Ledge.ai
    "https://ainow.ai/feed/", # AINOW
    "https://zenn.dev/topics/chatgpt/feed", # Zenn ChatGPT
    "https://zenn.dev/topics/ai/feed", # Zenn AI
    "https://qiita.com/tags/chatgpt/feed", # Qiita ChatGPT
    "https://qiita.com/tags/ai/feed" # Qiita AI
]

# 環境変数
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

def fetch_rss_feeds(urls):
    """複数のRSSフィードから記事を取得する"""
    logging.info("RSSフィードからの記事取得を開始します...")
    articles = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]: # 各フィード最大5件取得
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
    """Gemini APIを使用して記事を1件選び、解説Markdownを生成する"""
    logging.info("Gemini APIを使用して記事の選定と生成を開始します...")
    
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 環境変数が設定されていません。")

    # 記事リストをテキスト化
    articles_text = ""
    for idx, article in enumerate(articles):
        articles_text += f"[{idx+1}] タイトル: {article['title']}\nURL: {article['link']}\n概要: {article['summary']}\n\n"

    # システムプロンプトの設定（ユーザーの要件に基づく）
    system_instruction = """あなたは、AIスクール「AI+」の受講生向けに、最新のAIニュースを配信する編集アシスタントです。AIに詳しくない受講生の学習意欲を高め、AIを身近に感じてもらうことが目的です。
専門用語を避け、親しみやすく、丁寧な解説を心がけてください。

【ピックアップ基準】（最優先1件のみ選ぶこと）
優先度高: 読者が「明日から使ってみたい！」と思えるニュース
- 身近なサービスへのAI導入 (例: LINE、GoogleマップへのAI搭載)
- 有名なAIツールの「無料」または「簡単な」新機能
- 面倒な作業が劇的に楽になる事例
- すぐに使えるAIツールの活用テクニック・コツ (例: ChatGPTの良い回答を引き出すプロンプト術)
優先度中: 社会的な影響が大きい話題など、未来を感じられるニュース
優先度低(避ける): 専門的すぎる技術論文、過度に扇情的な内容

【禁止事項】
- ピックアップ基準に合わないニュースは選ばない。
- 以下の出力フォーマット以外の独自の構成は作らない。
- 長文の連続を避け、必ず箇条書きや短い文章で構成する。
- 専門用語は使わない。使う場合は注釈や簡単な言葉での言い換えを必ず添える。
- 画像は生成しない。テキストのみを出力する。

【出力フォーマット】 (Markdown形式)
必ず選んだ記事の「タイトル」と「リンクURL」をJSONなどのメタデータとして抽出可能にするため、最初の行に { "title": "...", "url": "..." } という形式のJSONを配置し、その次の行から以下のMarkdownを出力してください。

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
（読者が「どうすれば使えるのか」を具体的にイメージできるように、手順や方法を箇条書きで書く）

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
    """Geminiの出力からJSONメタデータとMarkdown本文を分離する"""
    lines = content.strip().split('\n')
    meta_json_str = lines[0]
    
    # 最初の行がJSONっぽくない場合のフォールバック修正
    if not meta_json_str.strip().startswith('{'):
        # JSONブロックを探す
        match = re.search(r'```json\n(.*?)\n```', content, re.DOTALL)
        if match:
             meta_json_str = match.group(1)
             # JSONブロックを本文から削除
             markdown_body = content.replace(f"```json\n{meta_json_str}\n```", "").strip()
        else:
            # 諦めてダミーを入れる
             meta_json_str = '{"title": "AI News", "url": ""}'
             markdown_body = content
    else:
        markdown_body = '\n'.join(lines[1:]).strip()
        
    try:
        metadata = json.loads(meta_json_str)
    except json.JSONDecodeError:
        logging.warning("出力からタイトルのJSONパースに失敗しました。")
        metadata = {"title": "本日のAIピックアップニュース", "url": ""}
        
    return metadata, markdown_body

def create_notion_page(title, markdown_content):
    """生成した記事をNotionデータベースにページとして投稿する"""
    logging.info("Notionへの投稿を開始します...")
    
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logging.warning("Notion APIキーまたはデータベースIDが設定されていません。Notion投稿をスキップします。")
        return False
        
    url = "https://api.notion.com/v1/pages"
    
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # テキストが長すぎる場合Notionの制限(2000文字)に引っかかるため、チャンク分けが必要ですが、
    # 今回は簡易的に 扱いやすい Text Block に分割して送信する関数
    def create_text_blocks(text):
        blocks = []
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            if not para.strip(): continue
            if para.startswith('## '):
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"type": "text", "text": {"content": para.replace('## ', '')[:2000]}}]}
                })
            elif para.startswith('---'):
                blocks.append({
                    "object": "block",
                    "type": "divider",
                    "divider": {}
                })
            else:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": para[:2000]}}]}
                })
        return blocks

    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "名前": {
                "title": [
                    {
                        "text": {
                            "content": f"【デイリーAIニュース】{title}"
                        }
                    }
                ]
            },
            "ステータス": {
                "status": {
                    "name": "レビュー待ち"
                }
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
        # 1. ニュースの収集
        articles = fetch_rss_feeds(RSS_FEEDS)
        if not articles:
            logging.warning("ニュース記事が1件も取得できませんでした。")
            return

        # 2. 記事の選定と生成
        raw_output = generate_news_article(articles)
        
        # 3. 出力のパース
        metadata, markdown_body = parse_generated_content(raw_output)
        
        # デバッグ用にファイルにも保存
        today = datetime.now().strftime('%Y%m%d')
        filename = f"news_{today}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(markdown_body)
        logging.info(f"ローカルファイル {filename} を作成しました。")

        # 4. Notionへの投稿
        create_notion_page(metadata.get('title', '無題'), markdown_body)
        
    except Exception as e:
        logging.error(f"実行中にエラーが発生しました: {e}")
        raise

if __name__ == "__main__":
    main()
