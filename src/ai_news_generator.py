import os
import json
import re
import logging
import feedparser
import requests
from datetime import datetime
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# --- 設定 ---
NEWS_FEEDS = [
    "https://rss.itmedia.co.jp/rss/2.0/ait.xml",
    "https://rss.itmedia.co.jp/rss/2.0/enterprise.xml",  # DX・エンタープライズ
    "https://ledge.ai/feed/",
    "https://ainow.ai/feed/",
    "https://www.watch.impress.co.jp/data/rss/1.0/ipw/feed.rdf",
    "https://gigazine.net/news/rss_2.0/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://enterprisezine.jp/rss/new/", # DX・企業IT
]
TIPS_FEEDS = [
    "https://qiita.com/tags/chatgpt/feed",
    "https://qiita.com/tags/ai/feed",
    "https://note.com/hashtag/ChatGPT/rss",
    "https://note.com/hashtag/生成AI/rss"
]
AI_KEYWORDS = [
    "AI", "ai", "人工知能", "機械学習", "深層学習", "ディープラーニング",
    "ChatGPT", "chatgpt", "GPT", "Gemini", "gemini", "Claude", "claude",
    "LLM", "大規模言語モデル", "生成AI", "生成ＡＩ",
    "OpenAI", "Anthropic", "Google AI", "Microsoft AI",
    "自然言語処理", "画像生成", "音声認識", "チャットボット",
    "プロンプト", "Copilot", "copilot", "Perplexity",
    "自動化", "RPA", "エージェント", "agent",
    "Stable Diffusion", "Midjourney", "DALL-E", "Sora",
    "ニューラル", "トランスフォーマー", "ファインチューニング",
    "RAG", "ベクトル", "埋め込み", "embedding",
    "DX", "dx", "デジタルトランスフォーメーション", "業務効率化", "働き方改革"
]
# 除外する画像パターン（アイコン、ロゴ、広告など）
EXCLUDED_IMAGE_PATTERNS = [
    "logo", "icon", "avatar", "badge", "button", "banner",
    "ad-", "ads-", "advertisement", "tracking", "pixel",
    "gravatar", "favicon", "sprite", "spacer",
    "1x1", "widget", "share", "social", "twitter", "facebook",
    "loading", "spinner", "arrow", "chevron"
]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
def is_ai_related(title, summary):
    text = (title + " " + summary).lower()
    for kw in AI_KEYWORDS:
        if kw.lower() in text:
            return True
    return False
def extract_images_from_url(article_url, max_images=5):
    """元記事のページから関連画像のURLを抽出する"""
    images = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AINewsBot/1.0)"}
        resp = requests.get(article_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return images
        soup = BeautifulSoup(resp.text, "html.parser")
        # 1. OGP画像（記事のメイン画像として最も信頼性が高い）
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            img_url = og_image["content"]
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            images.append(img_url)
        # 2. Twitter Card画像
        tw_image = soup.find("meta", attrs={"name": "twitter:image"})
        if tw_image and tw_image.get("content"):
            img_url = tw_image["content"]
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            if img_url not in images:
                images.append(img_url)
        # 3. 記事本文中の画像
        # 主要コンテンツエリアを探す
        content_area = (
            soup.find("article") or
            soup.find("div", class_=re.compile(r"(article|content|post|entry|main)", re.I)) or
            soup.find("main") or
            soup.body
        )
        if content_area:
            for img in content_area.find_all("img"):
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if not src:
                    continue
                # 相対URLを絶対URLに変換
                src = urljoin(article_url, src)
                # 除外パターンに一致するものはスキップ
                src_lower = src.lower()
                if any(pat in src_lower for pat in EXCLUDED_IMAGE_PATTERNS):
                    continue
                # 小さすぎる画像をスキップ（widthやheight属性で判定）
                width = img.get("width", "")
                height = img.get("height", "")
                try:
                    if width and int(width) < 100:
                        continue
                    if height and int(height) < 100:
                        continue
                except ValueError:
                    pass
                # 画像ファイル拡張子チェック
                parsed = urlparse(src)
                path_lower = parsed.path.lower()
                if not any(path_lower.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]):
                    # 拡張子がなくてもOGPなどは通す（URLパラメータに画像情報がある場合）
                    if "image" not in src_lower and "photo" not in src_lower and "img" not in src_lower:
                        continue
                if src not in images:
                    images.append(src)
                if len(images) >= max_images:
                    break
    except Exception as e:
        logging.warning(f"画像抽出エラー ({article_url}): {e}")
    logging.info(f"  → {article_url[:50]}... から {len(images)} 枚の画像を抽出")
    return images
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
                summary = getattr(entry, "summary", "")[:200]
                is_excluded = False
                for ex_title in exclude_titles:
                    if (title in ex_title) or (ex_title in title):
                        is_excluded = True
                        break
                # Try getting image from RSS
                item_image = ""
                if "media_content" in entry and len(entry.media_content) > 0:
                    item_image = entry.media_content[0].get("url", "")
                elif "media_thumbnail" in entry and len(entry.media_thumbnail) > 0:
                    item_image = entry.media_thumbnail[0].get("url", "")
                elif "links" in entry:
                    for l in entry.links:
                        if "image" in l.get("type", ""):
                            item_image = l.get("href", "")
                            break
                if not is_excluded and link not in seen_urls:
                    seen_urls.add(link)
                    articles.append({
                        "title": title,
                        "link": link,
                        "published": getattr(entry, "published", getattr(entry, "updated", "")),
                        "summary": summary,
                        "category": tag,
                        "image_url": item_image
                    })
        except Exception as e:
            logging.error(f"フィード取得エラー ({url}): {e}")
    return articles
def generate_news_articles(news_articles, tips_articles, posted_titles):
    logging.info("Gemini APIを使用して記事の選定と生成を開始します...")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 環境変数が設定されていません。")
    news_text = ""
    for idx, a in enumerate(news_articles):
        news_text += f"[ニュース候補{idx+1}] タイトル: {a['title']}\nURL: {a['link']}\n概要: {a['summary']}\n\n"
    tips_text = ""
    for idx, a in enumerate(tips_articles):
        tips_text += f"[Tips候補{idx+1}] タイトル: {a['title']}\nURL: {a['link']}\n概要: {a['summary']}\n\n"
    past_titles_str = "\n".join([f"- {t}" for t in list(posted_titles)[:100]])
    system_instruction = f"""あなたはAIニュースの編集アシスタントです。
【目的】
・受講生が実務活用を具体的にイメージできる内容にする
・リテラシーが低い人でも一読で理解できる構成にする
・難しい言葉は使わない
・元記事の内容は一切変更しない（追加・削除・推測禁止）
【過去の配信済み記事（重複除外用・最重要！）】
以下のトピックは既に過去に配信済みです。これらと同じニュース、または非常に似た内容のニュースは「絶対に」選ばないでください。
{past_titles_str}
【著作権に関する最重要ルール】
・元記事のタイトルや文章をそのままコピーして使うことは禁止。必ず自分の言葉で書き直すこと。
・元記事の見出しや本文の表現をそのまま流用せず、事実だけを抽出して独自の文章で再構成すること。
・引用の範囲を超えた転載にならないよう、元記事の文章構成や表現をなぞらないこと。
・「リンク」セクションに元記事へのリンクを必ず掲載し、読者が元記事を参照できるようにすること。
【記事の選び方（合計10件）】
★ 記事1〜7 → 必ず【ニュース記事リスト】から選ぶこと
★ 記事8〜10 → 必ず【Tips・テクニック記事リスト】から選ぶこと
※ 10件すべて異なるトピック・異なるURLにすること（重複禁止）。
※ 10件すべて、以下のMarkdown構成を最後まで省略せずに書き切ること。
※ AI（人工知能）やデジタルトランスフォーメーション（DX）に直接関係のない記事は絶対に選ばないこと。
【ピックアップ基準】
優先度高: 読者が「明日から使ってみたい！」と思えるニュース、企業のDX事例や業務効率化の成功事例、AI初心者や中級者が「うまく使うコツ」として実践できるテクニック
優先度中: 社会的な影響が大きい話題など、未来を感じられるニュース
【内容の正確性ルール（厳守）】
・元記事が「〇選」「〇つのツール」「〇つの方法」のようにリスト形式で紹介している場合、解説記事の中でもそのリストの具体的な項目名を必ず列挙すること。
・元記事に含まれる固有名詞は正確に記載すること。
【文章のルール】
・「概要」セクションの最初の2文で「何が起きたニュースか」を明確に書くこと
・「詳しい解説」セクションでは、最初にどんな話なのかを書くこと
・主語を必ず明示し、省略しないこと
・説明の重複は禁止（概要で書いたことを解説で繰り返さない）
・抽象表現は禁止（具体的に何がどうなるかを書く）
・専門用語は最小限にし、使う場合は文脈内で意味がわかるように書くこと
・1文の情報量は適切に保ち、短文の連続で分断しないこと
・同じ語尾の反復を避け、文末表現に変化をつけること
・同じ意味の言い換えは禁止し、用語は統一すること
【文体】
・ニュース記事の文体を厳守すること
・ですます調
・読者が実務を想像できる具体性を持たせる
・過度な感情表現は禁止
・話し言葉は禁止（「〜ですね。」「〜してみましょう！」等は使わない）
【太字のルール（厳守）】
・太字は以下の3箇所のみ。
  1. 「一言でいうと：」のラベル部分
  2. ポイントの見出し（箇条書きの先頭ラベル）
  3. ステップの見出し（番号付きリストの先頭ラベル）
・上記以外の本文を太字にすることは禁止。
【Markdown構成（必ずこの通りにすること）】
## 💡 概要
（最初の2文で「何が起きたか」を明確に。自分の言葉で書き直すこと）
---
## 🧐 詳しい解説
**一言でいうと：** （一番伝えたいことを1行で）
- **（ポイント1のタイトル）**
  （ポイント1の内容。概要との重複禁止。自分の言葉で書くこと）
- **（ポイント2のタイトル）**
  （ポイント2の内容）
---
## ⚒️ 具体的な使い方
1. **（ステップ1）**
   （具体的なアクションを説明）
2. **（ステップ2）**
   （具体的なアクションを説明）
---
## 🔗 リンク
* 参考記事: [（元のニュース記事のタイトル）]（（元のニュース記事のURL））
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
                "title": {
                    "type": "string",
                    "description": "独自の言葉で作成した記事タイトル"
                },
                "url": {"type": "string"},
                "markdown": {
                    "type": "string",
                    "description": "全セクション含む完全なMarkdown。太字はラベルのみ。話し言葉禁止。"
                }
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
def parse_rich_text(text):
    """Markdown の **太字** を Notion の rich_text bold annotation に変換"""
    rich_text = []
    parts = re.split(r'(\*\*.*?\*\*)', text)
    for part in parts:
        if not part:
            continue
        if part.startswith('**') and part.endswith('**'):
            rich_text.append({
                "type": "text",
                "text": {"content": part[2:-2]},
                "annotations": {"bold": True}
            })
        else:
            rich_text.append({
                "type": "text",
                "text": {"content": part}
            })
    return rich_text
def create_image_blocks(image_urls, source_url):
    """画像URLリストから、引用元付きのNotionイメージブロックを作成する"""
    blocks = []
    for img_url in image_urls:
        # 画像ブロック
        blocks.append({
            "object": "block",
            "type": "image",
            "image": {
                "type": "external",
                "external": {"url": img_url},
                "caption": [
                    {
                        "type": "text",
                        "text": {
                            "content": f"（引用元：{source_url}）",
                            "link": {"url": source_url}
                        }
                    }
                ]
            }
        })
    return blocks
def create_notion_blocks(markdown_text, image_urls=None, source_url=""):
    """MarkdownをNotionのブロック形式に変換し、画像も挿入する"""
    blocks = []
    lines = markdown_text.split('\n')
    i = 0
    images_inserted = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # --- 見出し2 ---
        if stripped.startswith('## '):
            heading_text = stripped[3:][:2000]
            # 「詳しい解説」セクションの直前に画像を挿入する
            if not images_inserted and image_urls and '詳しい解説' in heading_text:
                img_blocks = create_image_blocks(image_urls, source_url)
                blocks.extend(img_blocks)
                images_inserted = True
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": heading_text}}]}
            })
            i += 1
            continue
        # --- 区切り線 ---
        if stripped == '---':
            blocks.append({
                "object": "block",
                "type": "divider",
                "divider": {}
            })
            i += 1
            continue
        # --- 箇条書き (- で始まる行) ---
        if stripped.startswith('- '):
            item_text = stripped[2:]
            desc_lines = []
            j = i + 1
            while j < len(lines) and lines[j].startswith('  ') and not lines[j].strip().startswith('- '):
                desc_lines.append(lines[j].strip())
                j += 1
            if desc_lines:
                item_text = item_text + '\n' + '\n'.join(desc_lines)
            rich_text = parse_rich_text(item_text[:2000])
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich_text}
            })
            i = j
            continue
        # --- 番号付きリスト ---
        num_match = re.match(r'^(\d+)\.\s+', stripped)
        if num_match:
            item_text = stripped[num_match.end():]
            desc_lines = []
            j = i + 1
            while j < len(lines) and lines[j].startswith('   ') and not re.match(r'^\d+\.\s+', lines[j].strip()):
                desc_lines.append(lines[j].strip())
                j += 1
            if desc_lines:
                item_text = item_text + '\n' + '\n'.join(desc_lines)
            rich_text = parse_rich_text(item_text[:2000])
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": rich_text}
            })
            i = j
            continue
        # --- リンク行 (* で始まる行) ---
        if stripped.startswith('* '):
            link_text = stripped[2:]
            link_match = re.search(r'\[([^\]]+)\]\(([^)]+)\)', link_text)
            if link_match:
                label_before = link_text[:link_match.start()]
                link_label = link_match.group(1)
                link_url = link_match.group(2)
                rich_text = []
                if label_before.strip():
                    rich_text.append({"type": "text", "text": {"content": label_before}})
                rich_text.append({
                    "type": "text",
                    "text": {"content": link_label, "link": {"url": link_url}}
                })
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rich_text}
                })
            else:
                rich_text = parse_rich_text(link_text[:2000])
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": rich_text}
                })
            i += 1
            continue
        # --- 通常のテキスト ---
        para_lines = [stripped]
        j = i + 1
        while j < len(lines):
            next_stripped = lines[j].strip()
            if not next_stripped or next_stripped.startswith('## ') or next_stripped == '---' or next_stripped.startswith('- ') or next_stripped.startswith('* ') or re.match(r'^\d+\.\s+', next_stripped):
                break
            para_lines.append(next_stripped)
            j += 1
        para_text = '\n'.join(para_lines)
        rich_text = parse_rich_text(para_text[:2000])
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich_text}
        })
        i = j
    # 画像がまだ挿入されていない場合（「詳しい解説」がなかった場合のフォールバック）
    if not images_inserted and image_urls:
        img_blocks = create_image_blocks(image_urls, source_url)
        # 概要の直後（最初のdividerの後）に挿入
        insert_pos = 0
        for idx, block in enumerate(blocks):
            if block.get("type") == "divider":
                insert_pos = idx + 1
                break
        for img_block in reversed(img_blocks):
            blocks.insert(insert_pos, img_block)
    return blocks
def create_notion_page(title, markdown_content, image_urls=None, source_url=""):
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return False
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    blocks = create_notion_blocks(markdown_content, image_urls=image_urls, source_url=source_url)
    data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "title": {"title": [{"text": {"content": f"【デイリーAIニュース】{title}"}}]}
        },
        "children": blocks
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
        raw_output = generate_news_articles(news_articles, tips_articles, posted_titles)
        generated_articles = parse_generated_content(raw_output)
        if not generated_articles:
            logging.error("記事の生成に失敗しました。")
            return
        logging.info(f"{len(generated_articles)}件の記事が生成されました。画像抽出とNotion投稿を開始します。")
        for item in generated_articles:
            title = item.get("title", "無題")
            markdown_content = item.get("markdown", "")
            article_url = item.get("url", "")
            if not markdown_content:
                continue
            # 元記事から画像を抽出
            image_urls = []
            if article_url:
                logging.info(f"画像抽出中: {article_url[:60]}...")
                
                # RSSからすでに画像が取得できているか探す
                rss_image = ""
                for a in news_articles + tips_articles:
                    if a['link'] == article_url and a.get('image_url'):
                        rss_image = a['image_url']
                        break
                # スクレイピングで画像抽出を試みる
                image_urls = extract_images_from_url(article_url, max_images=5)
                
                # スクレイピングで1枚も取れなかった場合、RSSの画像をフォールバックとして使う
                if not image_urls and rss_image:
                    logging.info(f"  → スクレイピング失敗のためRSSの画像を使用します: {rss_image}")
                    image_urls.append(rss_image)
            # Notionへ投稿（画像付き）
            create_notion_page(title, markdown_content, image_urls=image_urls, source_url=article_url)
    except Exception as e:
        logging.error(f"実行中にエラーが発生しました: {e}")
        raise
if __name__ == "__main__":
    main()
