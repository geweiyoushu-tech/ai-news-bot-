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
    "https://rss.itmedia.co.jp/rss/2.0/ait.xml",
    "https://ledge.ai/feed/",
    "https://ainow.ai/feed/",
    "https://www.watch.impress.co.jp/data/rss/1.0/ipw/feed.rdf",
    "https://feeds.feedburner.com/moongift",
    "https://gigazine.net/news/rss_2.0/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.wired.com/feed/tag/ai/latest/rss",
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
    data = {"page_size": 100}
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
            for entry in feed.entries[:10]:
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
def generate_news_articles(news_articles, tips_articles):
    logging.info("Gemini APIを使用して記事の選定と生成を開始します...")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 環境変数が設定されていません。")
    news_text = ""
    for idx, a in enumerate(news_articles):
        news_text += f"[ニュース候補{idx+1}] タイトル: {a['title']}\nURL: {a['link']}\n概要: {a['summary']}\n\n"
    tips_text = ""
    for idx, a in enumerate(tips_articles):
        tips_text += f"[Tips候補{idx+1}] タイトル: {a['title']}\nURL: {a['link']}\n概要: {a['summary']}\n\n"
    system_instruction = """あなたはAIニュースの編集アシスタントです。
【目的】
・受講生が実務活用を具体的にイメージできる内容にする
・リテラシーが低い人でも一読で理解できる構成にする
・難しい言葉は使わない
・元記事の内容は一切変更しない（追加・削除・推測禁止）
【記事の選び方（合計10件）】
★ 記事1〜7 → 必ず【ニュース記事リスト】から選ぶこと（新サービス発表、AI導入事例、大型提携、新機能リリースなどの最新ニュース）
★ 記事8〜10 → 必ず【Tips・テクニック記事リスト】から選ぶこと（プロンプト術、効率化テクニック、活用ノウハウなどの実践Tips）
※ 10件すべて異なるトピック・異なるURLにすること（重複禁止）。
※ 10件すべて、以下のMarkdown構成を最後まで省略せずに書き切ること。
【ピックアップ基準】
優先度高: 読者が「明日から使ってみたい！」と思えるニュース
- 身近なサービスへのAI導入
- 有名なAIツールの「無料」または簡単な新機能
- 面倒な作業が劇的に楽になる事例
- すぐに使えるAIツールのプロンプト術
優先度中: 社会的な影響が大きい話題など、未来を感じられるニュース
【最重要：文章のルール】
・「概要」セクションの最初の2文で「何が起きたニュースか」を明確に書くこと
・「詳しい解説」セクションでは、最初にどんな話なのかを書くこと
・主語を必ず明示し、省略しないこと
・説明の重複は禁止（概要で書いたことを解説で繰り返さない）
・抽象表現は禁止（「すごい」「便利」などの曖昧な表現ではなく、具体的に何がどうなるかを書く）
・専門用語は最小限にし、使う場合は文脈内で意味がわかるように書くこと
・1文の情報量は適切に保ち、短文の連続で分断しないこと（短すぎる文の羅列は禁止）
・同じ語尾の反復を避け、文末表現に変化をつけること（「です。」「です。」「です。」の連続は禁止）
・同じ意味の言い換えは禁止し、用語は統一すること
【文体】
・ニュース調
・ですます調
・読者が実務を想像できる具体性を持たせる
・過度な感情表現は禁止（「驚きの」「衝撃の」「すごい」「ヤバい」等は使わない）
【Markdown構成（必ずこの通りにすること）】
## 💡 概要
（最初の2文で「何が起きたか」を明確に。続けてニュースの要点を簡潔にまとめる）
---
## 🧐 詳しい解説
**一言でいうと：** （一番伝えたいことを1行で）
- **（ポイント1のタイトル）**
  （ポイント1の具体的な内容を説明。概要との重複禁止）
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
    prompt = f"""以下の2種類のリストから、ルールに従って合計10件選び、それぞれの解説をMarkdownで生成してください。
【ニュース記事リスト（ここから7件選ぶこと）】
{news_text}
【Tips・テクニック記事リスト（ここから3件選ぶこと）】
{tips_text}"""
    client = genai.Client(api_key=GEMINI_API_KEY)
    response_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "url": {"type": "string"},
                "markdown": {"type": "string", "description": "必ず『💡 概要』『🧐 詳しい解説』『⚒️ 具体的な使い方』『🔗 リンク』の全構成を含めた完全なMarkdown文章。概要の最初の2文で何が起きたかを明確にし、主語を省略せず、説明の重複をせず、抽象表現を避け、語尾の反復を避けること。"}
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
        posted_titles = get_posted_titles_from_notion()
        news_articles = fetch_rss_feeds(NEWS_FEEDS, posted_titles, tag="news")
        tips_articles = fetch_rss_feeds(TIPS_FEEDS, posted_titles, tag="tips")
        logging.info(f"ニュース系: {len(news_articles)}件 / Tips系: {len(tips_articles)}件")
        if not news_articles and not tips_articles:
            logging.info("新しい記事候補がありません。")
            return
        raw_output = generate_news_articles(news_articles, tips_articles)
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
