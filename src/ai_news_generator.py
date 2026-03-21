import os
import json
import re
import logging
import feedparser
import requests
from datetime import datetime
from urllib.parse import urljoin, urlparse
from google import genai
from google.genai import types
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# --- 情報源（この3カテゴリのみ） ---
NEWS_FEEDS = [
    # 1. AI特化型の専門メディア（深掘りしたい人向け）
    "https://ledge.ai/feed/",
    "https://ainow.ai/feed/",
    "https://news.google.com/rss/search?q=AI+site:aismiley.co.jp&hl=ja&gl=JP&ceid=JP:ja", # AIsmiley
    # 2. 大手ニュースサイトのAIカテゴリ（速報重視の人向け）
    "https://news.google.com/rss/search?q=AI+site:nikkei.com&hl=ja&gl=JP&ceid=JP:ja", # 日本経済新聞
    "https://news.google.com/rss/search?q=AI+site:nhk.or.jp&hl=ja&gl=JP&ceid=JP:ja", # NHKニュース
    "https://news.google.com/rss/search?q=AI+site:asahi.com&hl=ja&gl=JP&ceid=JP:ja", # 朝日新聞
    "https://news.google.com/rss/search?q=AI+site:yomiuri.co.jp&hl=ja&gl=JP&ceid=JP:ja", # 読売新聞
    # 3. XのAIトレンド（SNSの話題拾い上げ用）
    "https://togetter.com/rss/it", # Togetter ITカテゴリ
    "https://news.yahoo.co.jp/rss/topics/it.xml", # Yahoo ITトレンド
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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
def is_ai_related(title, summary):
    text = (title + " " + summary).lower()
    for kw in AI_KEYWORDS:
        if kw.lower() in text:
            return True
    return False
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
def fetch_rss_feeds(urls, exclude_titles):
    articles = []
    seen_urls = set()
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                link = entry.link
                title = entry.title
                summary = getattr(entry, "summary", "")[:200]
                is_excluded = False
                for ex_title in exclude_titles:
                    if (title in ex_title) or (ex_title in title):
                        is_excluded = True
                        break
                if not is_excluded and link not in seen_urls and is_ai_related(title, summary):
                    seen_urls.add(link)
                    articles.append({
                        "title": title,
                        "link": link,
                        "published": getattr(entry, "published", getattr(entry, "updated", "")),
                        "summary": summary
                    })
        except Exception as e:
            logging.error(f"フィード取得エラー ({url}): {e}")
    return articles
def generate_news_articles(articles, posted_titles):
    logging.info("Gemini APIを使用して記事の選定と生成を開始します...")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 環境変数が設定されていません。")
    articles_text = ""
    for idx, a in enumerate(articles):
        articles_text += f"[候補{idx+1}] タイトル: {a['title']}\nURL: {a['link']}\n概要: {a['summary']}\n\n"
    past_titles_str = "\n".join([f"- {t}" for t in list(posted_titles)[:100]])
    system_instruction = f"""あなたはNewsPicksの編集者です。
ユーザーが入力したニュースの情報を、AIスクール受講生向けの「AIニュース」として再構成してください。
【ターゲット読者の定義（ペルソナ）】
以下のレベル感を「AI初心者・中級者」として明確に定義し、この読者が理解できる記事のみを選定・執筆すること。
・レベル感：日常や業務で少しAI（ChatGPT等）を触ったことがある、または興味はあるが使いこなせていない非エンジニア。
・知識量：プログラミング言語、API、パラメータチューニング、トークンなどの技術的な仕組みは全く知らない。
・知りたいこと：「どんな指示（プロンプト）を出せば仕事が楽になるか」「無料で使える便利なAIツールは何か」「他社はどうやってAIで業務効率化しているか」という実用的な情報。
【過去の配信済み記事（重複除外用・最重要！）】
以下のトピックは既に過去に配信済みです。これらと同じニュース、または非常に似た内容のニュースは「絶対に」選ばないでください。
同じ元記事URL、同じサービス名の話題、同じ企業の同じ発表についての記事も重複とみなし、選んではいけません。
{past_titles_str}
【目的】
・初心者や中級者が、すぐに自分の仕事や日常でAIを活用できると感じられる内容にする
・中学生・高校生が読んでも完全に理解できるレベルの平易な日本語のみで記述する
・難しい言葉や抽象度が高い表現は「絶対に」使用禁止とし、誰にでもわかる具体例を用いて解説する
・ニュースの事実（ファクト）は変更しない。ただし、読者の理解を助けるための「前提知識の補足」「専門用語の解説」「カリキュラム（リスク管理等）との紐づけ」の追加は必須とする。
【著作権に関する最重要ルール】
・元記事のタイトルや文章をそのままコピーして使うことは禁止。必ず自分の言葉で書き直すこと。
・元記事の見出しや本文の表現をそのまま流用せず、事実だけを抽出して独自の文章で再構成すること。
・引用の範囲を超えた転載にならないよう、元記事の文章構成や表現をなぞらないこと。
・「リンク」セクションに元記事へのリンクを必ず掲載し、読者が元記事を参照できるようにすること。
【記事の選び方（合計10件）】
以下の候補リストから合計10件を選ぶこと。
※ 10件すべて異なるトピック・異なるURLにすること（重複禁止）。
※ 10件すべて、以下のMarkdown構成を最後まで省略せずに書き切ること。
※ AI（人工知能）に直接関係のない記事は絶対に選ばないこと。
【ピックアップ基準】
優先度高: 読者が「明日から使ってみたい！」と思えるニュース
優先度中: 社会的な影響が大きい話題など、未来を感じられるニュース
【内容の正確性ルール（厳守）】
・元記事が「〇選」「〇つのツール」「〇つの方法」のようにリスト形式で紹介している場合、解説記事の中でもそのリストの具体的な項目名（ツール名、方法名など）を必ず列挙すること。項目名を省略して「紹介されています」とだけ書くことは禁止。
・元記事に含まれる固有名詞（サービス名、企業名、数値）は正確に記載すること。
【文章のルール】
・「概要」セクションの1文目では、いきなりニュースの本題に入らず「前提となる背景（例：〇〇というサービスがある等）」を補足すること。2文目以降で「何が起きたか」を明確に書くこと。
・「LLM」「RAG」「プロンプト」などのAI用語やIT専門用語が登場した場合、必ず「※」を用いて中高生にもわかる簡潔な用語解説を追加すること。用語を解説せずにスルーすることは絶対に禁止。
・「詳しい解説」セクションでは、最初にどんな話なのかを書くこと。
・「詳しい解説」の中には、受講生にとっての具体的なメリット（例：「これまで〇〇ができなかった人でも、××できるようになる」等）を言語化して含めること。
・ニュースの内容に応じて、AI利用時のリスク（著作権、情報漏洩、ハルシネーションなど）に関する注意喚起を、独立したポイントとして必ず1つ盛り込むこと。その際、必要に応じて「モジュール１-セクション６『AIリテラシー・ガバナンス・リスク』」などのスクール学習内容と紐づけて解説すること。
・主語を必ず明示し、省略しないこと。
・説明の重複は禁止（概要で書いたことを解説で繰り返さない）。
・抽象表現は一切禁止し、小学生でも情景が浮かぶレベルで具体的に何がどうなるかを書くこと。
・1文の情報量は適切に保ち、短文の連続で分断しないこと。
・同じ語尾の反復を避け、文末表現に変化をつけること。
・同じ意味の言い換えは禁止し、用語は統一すること。
・第3セクションのタイトル（「私たちの未来にどう関係する？」など）は固定せず、ニュースの内容や性質（新サービスの導入、技術の詳しい説明、社会動向など）に合わせて、読者が最も興味を持つような適切なタイトルをAIが柔軟に決定すること。
・第3セクションのステップ数（具体的なアクションや未来への影響）は、ニュースの内容に応じてAIが最適な数を判断し、番号付きリストで記述すること。2つに限定せず、必要に応じて増減させること。
【文体】
・ニュース記事の文体を厳守すること。
・ですます調。
・読者が実務を想像できる具体性を持たせる。
・過度な感情表現は禁止（「驚きの」「衝撃の」「すごい」「ヤバい」等は使わない）。
・話し言葉は禁止（「〜ですね。」「〜ですよね。」「〜してみましょう！」「〜かもしれませんね。」等は使わない）。
・語尾は「〜です。」「〜ます。」「〜でしょう。」「〜ました。」「〜となります。」「〜が見込まれます。」「〜としています。」等のニュース調の表現を使うこと。
【太字のルール（厳守）】
・Markdown内で ** を使った太字装飾は以下の3箇所のみに限定すること。
  1. 「一言でいうと：」のラベル部分
  2. ポイントの見出し（箇条書きの先頭ラベル）
  3. ステップの見出し（番号付きリストの先頭ラベル）
・上記以外の本文、キーワード、サービス名、数値などを太字にすることは禁止。
【Markdown構成（必ずこの通りにすること）】
## 💡 概要
（1文目で前提背景、2文目以降で「何が起きたか」を明確にまとめる。自分の言葉で書き直すこと）
※（初心者向けの用語解説や補足が必要な場合はここに記載）
引用元：[元記事のURL]
---
## 🧐 詳しい解説
**一言でいうと：** （一番伝えたいことを1行で）
- **（ポイント1のタイトル）**
  （ニュースの具体的な内容を説明。概要との重複禁止。自分の言葉で書くこと）
- **（ポイント2のタイトル：受講生への影響やメリットなど）**
  （このニュースが受講生にどう役立つか、「これまで〇〇できなかった人でも〜」などの具体例を用いて解説）
- **（ポイント3のタイトル：AI利用時のリスクや注意点など）**
  （著作権やリテラシー等の観点から注意すべき点を、スクールの学習内容と関連付けて解説）
---
## ⚒️ （記事の内容に合わせてAIが柔軟に決定するタイトル）
（ニュースの内容に応じて、具体的なアクションや未来への影響を番号付きリストで記述。ステップ数はAIが最適な数を判断し、2つに限定せず増減させること）
---
## 🔗 リンク
* 参考記事: [（元のニュース記事のタイトル）]（（元のニュース記事のURL））
* スクール教材: モジュールXX-セクションXX「XXXXXX」（※言及した場合のみ追加）
【画像に関する指示】
・記事生成の際、画像はユーザー自身が選びたいという意向があるため、記事生成のみを行い、画像の選定や挿入は行わない。
"""
    prompt = f"""以下の候補リストから、ルールに従って合計10件選び、それぞれの解説をMarkdownで生成してください。
10件すべて異なるURLの記事にしてください。同じURLの記事を2回選ぶことは禁止です。
【記事候補リスト】
{articles_text}"""
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
def create_notion_blocks(markdown_text):
    """MarkdownをNotionのブロック形式に変換する"""
    blocks = []
    lines = markdown_text.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # --- 見出し2 ---
        if stripped.startswith('## '):
            heading_text = stripped[3:][:2000]
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
    return blocks
def create_notion_page(title, markdown_content):
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return False
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    blocks = create_notion_blocks(markdown_content)
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
        # 指定3カテゴリのみから記事を取得（note.comは含まない）
        articles = fetch_rss_feeds(NEWS_FEEDS, posted_titles)
        logging.info(f"AI関連の記事候補: {len(articles)}件")
        if not articles:
            logging.info("新しい記事候補がありません。")
            return
        raw_output = generate_news_articles(articles, posted_titles)
        generated_articles = parse_generated_content(raw_output)
        if not generated_articles:
            logging.error("記事の生成に失敗しました。")
            return
        # 重複URLチェック（同じURLの記事を2回投稿しない）
        seen_urls = set()
        unique_articles = []
        for item in generated_articles:
            article_url = item.get("url", "")
            if article_url not in seen_urls:
                seen_urls.add(article_url)
                unique_articles.append(item)
            else:
                logging.warning(f"重複URLを除外しました: {article_url[:60]}")
        logging.info(f"{len(unique_articles)}件の記事をNotion投稿します。")
        for item in unique_articles:
            title = item.get("title", "無題")
            markdown_content = item.get("markdown", "")
            if not markdown_content:
                continue
            create_notion_page(title, markdown_content)
    except Exception as e:
        logging.error(f"実行中にエラーが発生しました: {e}")
        raise
if __name__ == "__main__":
    main()
