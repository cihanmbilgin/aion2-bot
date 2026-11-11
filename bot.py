#!/usr/bin/env python3
"""
AION 2 Global duyurularını Türkçeye çevirip Discord'a gönderen bot.

Akış:
  1. plaync topluluk API'sinden izlenen panoların (update_en, notice_en) son postlarını çeker.
  2. state.json'daki "görülen" ID'lerle karşılaştırıp yeni postları bulur.
  3. Her yeni postun HTML içeriğini temizler, Gemini API (ücretsiz katman) ile Türkçeye çevirir.
  4. Çeviriyi Discord webhook'una embed'ler halinde gönderir.
  5. Başarılı gönderilen postları state.json'a kaydeder.

Yalnızca Python standart kütüphanesini kullanır (pip install gerekmez).

Ortam değişkenleri:
  GEMINI_API_KEY        (zorunlu*) Google AI Studio API anahtarı
  DISCORD_WEBHOOK_URL   (zorunlu)  Discord kanal webhook linki
  TRANSLATOR            (isteğe bağlı) "gemini" (varsayılan) veya "claude"
  GEMINI_MODELS         (isteğe bağlı) Sırayla denenecek Gemini modelleri, virgülle ayrılmış.
                                       Varsayılan: gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-2.5-flash
  ANTHROPIC_API_KEY     (*TRANSLATOR=claude ise zorunlu) Claude API anahtarı
  CLAUDE_MODEL          (isteğe bağlı) Varsayılan: claude-sonnet-5-5
  BOARDS                (isteğe bağlı) Virgülle ayrılmış pano listesi. Varsayılan: update_en,notice_en
  POST_ON_FIRST_RUN     (isteğe bağlı) İlk çalıştırmada her panodan kaç eski post gönderilsin. Varsayılan: 0
  TEST_ARTICLE          (isteğe bağlı) "pano:articleId" biçiminde; sadece bu postu çevirip gönderir,
                                       state.json'a dokunmaz. Örnek: update_en:6af42a1affaccc00c265785f
  DRY_RUN               (isteğe bağlı) "1" ise Discord'a göndermez, çeviriyi ekrana yazar.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# --------------------------------------------------------------------------- #
# Ayarlar
# --------------------------------------------------------------------------- #

API_BASE = "https://api-global-community.plaync.com/aion2_global/board"
SITE_BASE = "https://aion2.plaync.com/en-us/board"
STATE_FILE = Path(__file__).with_name("state.json")
LIST_SIZE = 10            # Her kontrolde panodan çekilecek post sayısı
STATE_KEEP = 100          # Pano başına saklanacak görülen ID sayısı
HTTP_TIMEOUT = 30
LLM_TIMEOUT = 600
CLAUDE_MAX_TOKENS = 16000
GEMINI_MAX_TOKENS = 32768
DEFAULT_GEMINI_MODELS = "gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-2.5-flash"

BOARD_INFO = {
    # pano anahtarı: (Discord'da görünecek etiket, site yolu, embed rengi)
    "update_en": ("Güncelleme", "update", 0x8E6CEF),
    "notice_en": ("Duyuru", "notice", 0x3BA5E0),
}

USER_AGENT = "aion2-tr-discord-bot/1.0 (+https://github.com)"

# Discord sınırları
EMBED_DESC_LIMIT = 4096
EMBED_TITLE_LIMIT = 256
CHUNK_TARGET = 3900       # Biraz pay bırakarak bölme hedefi

SYSTEM_PROMPT = """Sen AION 2 (NCSoft MMORPG) duyurularını Türkçeye çeviren bir çevirmensin.
Çevirin bir Discord kanalında Türk oyunculara paylaşılacak.

Kurallar:
- Tüm metni eksiksiz ve doğru biçimde Türkçeye çevir. Hiçbir sayı, tarih, saat, yüzde, eşya, ödül veya maddeyi atlama, özetleme ya da ekleme yapma.
- Oyun içi özel adları İngilizce bırak: eşya, skill, zindan, boss, bölge, sınıf, etkinlik ve para birimi adları (ör. Abyssal Forge: Ludra, Substance Morph, Kina (Bound), Gear Change Voucher, Ranger, Expedition, Abyss Point). Türk oyuncular bunları oyunda İngilizce görüyor.
- "Bound" gibi eşya etiketlerini çevirme.
- Tarih ve saatlerde CET/PST verildiyse aynen koru; yanına Türkiye saatini ekleme.
- Çıktıyı Discord markdown'u ile biçimlendir:
  - Bölüm başlıkları için **kalın** metin kullan (başlık için # kullanma).
  - Maddeler için "- " kullan, alt maddeler için iki boşluk girintili "  - ".
  - Discord tablo desteklemez: Tabloları "- Sütun1: değer · Sütun2: değer" gibi madde listesine çevir.
  - Kod bloğu, HTML veya görsel ekleme.
- Duyuruda olmayan hiçbir yorum, açıklama, giriş veya kapanış cümlesi yazma. Sadece çeviriyi ver.
- Çıktının en başına çevrilmiş başlığı yazma; başlık ayrıca gösterilecek."""


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def http_json(url: str, *, method: str = "GET", data: dict | None = None,
              headers: dict | None = None, timeout: int = HTTP_TIMEOUT) -> tuple[int, dict | None]:
    """JSON isteği gönderir; (durum kodu, JSON gövde) döner."""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            parsed = {"raw": raw[:500]}
        return e.code, parsed


# --------------------------------------------------------------------------- #
# plaync API
# --------------------------------------------------------------------------- #

def fetch_board_list(board: str, size: int = LIST_SIZE) -> list[dict]:
    """Panonun en yeni postlarını (yeniden eskiye) döner."""
    url = (f"{API_BASE}/{board}/article/search/moreArticle"
           f"?isVote=true&moreSize={size}&moreDirection=BEFORE&previousArticleId=0")
    status, data = http_json(url)
    if status != 200 or not data:
        raise RuntimeError(f"{board} listesi alınamadı (HTTP {status})")
    return data.get("contentList") or []


def fetch_article(board: str, article_id: str) -> dict:
    """Postun başlığını, HTML içeriğini ve tarihini döner."""
    status, data = http_json(f"{API_BASE}/{board}/article/{article_id}")
    if status != 200 or not data or "article" not in data:
        raise RuntimeError(f"Post alınamadı: {board}/{article_id} (HTTP {status})")
    art = data["article"]
    meta = art.get("contentMeta") or {}
    content = (art.get("content") or {}).get("content") or ""
    return {
        "id": article_id,
        "board": board,
        "title": html.unescape(meta.get("title") or "(başlıksız)"),
        "html": content,
        "timestamp": _extract_timestamp(meta.get("timestamps")),
    }


def _extract_timestamp(ts) -> str | None:
    """API'deki zaman bilgisini ISO 8601 biçimine çevirir (bulunamazsa None)."""
    if not isinstance(ts, dict):
        return None
    for key in ("postedAt", "createdAt", "created", "registeredAt", "updatedAt"):
        val = ts.get(key)
        if isinstance(val, (int, float)) and val > 0:
            sec = val / 1000 if val > 1e12 else val
            return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat()
        if isinstance(val, str) and val:
            return val
    return None


def article_url(board: str, article_id: str) -> str:
    path = BOARD_INFO.get(board, (board, board.replace("_en", ""), 0))[1]
    return f"{SITE_BASE}/{path}/view?articleId={article_id}"


# --------------------------------------------------------------------------- #
# HTML → sade metin
# --------------------------------------------------------------------------- #

class _Simplifier(HTMLParser):
    """HTML'i Claude'a gönderilecek sade, yapısı korunmuş metne çevirir.

    Paragraf/satır yapısını, listeleri ve tabloları korur; stil ve öznitelikleri atar.
    """

    BLOCK = {"p", "div", "section", "article", "header", "footer", "h1", "h2", "h3",
             "h4", "h5", "h6", "ul", "ol", "table", "thead", "tbody", "blockquote"}
    SKIP = {"script", "style", "noscript", "svg", "iframe"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_depth = 0
        self.in_cell = False
        self.row: list[str] = []
        self.cell: list[str] = []

    def _emit(self, text: str) -> None:
        if self.in_cell:
            self.cell.append(text)
        else:
            self.out.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "br":
            self._emit("\n")
        elif tag == "li":
            self._emit("\n- ")
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.cell = []
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self._emit(f"[görsel: {alt}]")
        elif tag in self.BLOCK:
            self._emit("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in ("td", "th"):
            text = re.sub(r"\s+", " ", "".join(self.cell)).strip()
            self.row.append(text)
            self.in_cell = False
            self.cell = []
        elif tag == "tr":
            cells = [c for c in self.row]
            if any(cells):
                self.out.append("\n| " + " | ".join(cells) + " |")
            self.row = []
        elif tag in self.BLOCK or tag == "li":
            self._emit("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        self._emit(data)

    def text(self) -> str:
        raw = "".join(self.out)
        raw = raw.replace("​", "").replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.split("\n")]
        cleaned: list[str] = []
        for ln in lines:
            if ln == "" and cleaned and cleaned[-1] == "":
                continue
            cleaned.append(ln)
        return "\n".join(cleaned).strip()


def html_to_text(content_html: str) -> str:
    parser = _Simplifier()
    parser.feed(content_html)
    parser.close()
    return parser.text()


# --------------------------------------------------------------------------- #
# Çeviri (Gemini varsayılan, isteğe bağlı Claude)
# --------------------------------------------------------------------------- #

RETRYABLE = (429, 500, 502, 503, 504, 529)


def _build_user_msg(title: str, body_text: str) -> str:
    return (
        "Aşağıdaki AION 2 duyurusunu çevir.\n\n"
        "Yanıtını tam olarak şu biçimde ver:\n"
        "BAŞLIK: <çevrilmiş başlık, tek satır>\n"
        "---\n"
        "<çevrilmiş gövde>\n\n"
        f"Orijinal başlık: {title}\n\n"
        f"Orijinal metin (tablolar | ile ayrılmış satırlar halinde):\n{body_text}"
    )


def translate(title: str, body_text: str) -> tuple[str, str]:
    """(Türkçe başlık, Türkçe gövde) döner."""
    provider = (os.environ.get("TRANSLATOR") or "gemini").strip().lower()
    user_msg = _build_user_msg(title, body_text)
    if provider == "claude":
        text = _translate_claude(user_msg)
    elif provider == "gemini":
        text = _translate_gemini(user_msg)
    else:
        raise RuntimeError(f"Bilinmeyen TRANSLATOR değeri: {provider}")
    return _split_title(text, fallback_title=title)


def _translate_gemini(user_msg: str) -> str:
    """Gemini API (generateContent). Model sırayla denenir; kota/erişim hatasında sıradakine geçer."""
    api_key = os.environ["GEMINI_API_KEY"]
    models = [m.strip() for m in (os.environ.get("GEMINI_MODELS") or DEFAULT_GEMINI_MODELS).split(",")
              if m.strip()]
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "generationConfig": {"maxOutputTokens": GEMINI_MAX_TOKENS},
    }
    headers = {"x-goog-api-key": api_key}
    errors = []

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(1, 4):
            status, data = http_json(url, method="POST", data=payload, headers=headers,
                                     timeout=LLM_TIMEOUT)
            if status == 200 and data:
                cands = data.get("candidates") or []
                if not cands:
                    reason = (data.get("promptFeedback") or {}).get("blockReason", "boş yanıt")
                    errors.append(f"{model}: {reason}")
                    break
                cand = cands[0]
                parts = (cand.get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                if cand.get("finishReason") == "MAX_TOKENS":
                    log("UYARI: Çeviri çıktı sınırına ulaştı, sonu eksik olabilir.")
                if text:
                    log(f"Çeviri modeli: {model}")
                    return text
                errors.append(f"{model}: metin yok (finishReason={cand.get('finishReason')})")
                break
            err = f"{model} HTTP {status}: {json.dumps(data, ensure_ascii=False)[:300] if data else ''}"
            # Model yok / erişim yok / günlük kota bitti -> sıradaki modeli dene
            if status in (400, 403, 404) or (status == 429 and _is_daily_quota(data)):
                errors.append(err)
                log(f"{err} -> sıradaki model denenecek")
                break
            if status in RETRYABLE and attempt < 3:
                wait = 30 * attempt
                log(f"Gemini geçici hata ({err}), {wait} sn sonra tekrar denenecek...")
                time.sleep(wait)
                continue
            errors.append(err)
            break
    raise RuntimeError("Çeviri başarısız: " + " | ".join(errors))


def _is_daily_quota(data) -> bool:
    text = json.dumps(data or {}).lower()
    return "perday" in text or "per_day" in text or "requests per day" in text


def _translate_claude(user_msg: str) -> str:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    payload = {
        "model": os.environ.get("CLAUDE_MODEL") or "claude-sonnet-5-5",
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    last_err = None
    for attempt in range(1, 4):
        status, data = http_json("https://api.anthropic.com/v1/messages", method="POST",
                                 data=payload, headers=headers, timeout=LLM_TIMEOUT)
        if status == 200 and data:
            if data.get("stop_reason") == "max_tokens":
                log("UYARI: Çeviri max_tokens sınırına ulaştı, sonu eksik olabilir.")
            return "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text").strip()
        last_err = f"HTTP {status}: {json.dumps(data)[:300] if data else ''}"
        if status in RETRYABLE and attempt < 3:
            wait = 20 * attempt
            log(f"Claude API geçici hata ({last_err}), {wait} sn sonra tekrar denenecek...")
            time.sleep(wait)
            continue
        break
    raise RuntimeError(f"Çeviri başarısız: {last_err}")


def _split_title(text: str, fallback_title: str) -> tuple[str, str]:
    m = re.match(r"\s*BAŞLIK:\s*(.+?)\s*\n-{3,}\s*\n(.*)", text, flags=re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return fallback_title, text.strip()


# --------------------------------------------------------------------------- #
# Discord
# --------------------------------------------------------------------------- #

def split_for_discord(text: str, limit: int = CHUNK_TARGET) -> list[str]:
    """Metni satır sınırlarından bölerek her parçayı limitin altında tutar."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        # Tek satır bile sınırı aşıyorsa kelime kelime böl
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            piece, line = line[:cut], line[cut:].lstrip()
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(piece)
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current.rstrip())
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current.rstrip())
    return [c for c in chunks if c.strip()]


def post_to_discord(article: dict, tr_title: str, tr_body: str) -> None:
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    label, _, color = BOARD_INFO.get(article["board"], ("Duyuru", "", 0x99AAB5))
    url = article_url(article["board"], article["id"])
    parts = split_for_discord(tr_body)
    total = len(parts)

    for i, part in enumerate(parts, start=1):
        embed = {"description": part[:EMBED_DESC_LIMIT], "color": color}
        if i == 1:
            embed["title"] = f"[{label}] {tr_title}"[:EMBED_TITLE_LIMIT]
            embed["url"] = url
            embed["author"] = {"name": "AION 2 · Türkçe çeviri"}
        if i == total:
            footer = f"Orijinal: {article['title']}"
            if total > 1:
                footer = f"Bölüm {i}/{total} · " + footer
            embed["footer"] = {"text": footer[:2048]}
            if article.get("timestamp"):
                embed["timestamp"] = article["timestamp"]
        elif total > 1:
            embed["footer"] = {"text": f"Bölüm {i}/{total}"}

        payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
        _send_webhook(webhook, payload)
        time.sleep(1.2)  # Discord hız sınırına takılmamak için


def _send_webhook(webhook: str, payload: dict) -> None:
    for attempt in range(1, 6):
        status, data = http_json(webhook + ("&" if "?" in webhook else "?") + "wait=true",
                                 method="POST", data=payload)
        if 200 <= status < 300:
            return
        if status == 429:
            retry = float((data or {}).get("retry_after", 2))
            log(f"Discord hız sınırı, {retry:.1f} sn bekleniyor...")
            time.sleep(retry + 0.5)
            continue
        if status >= 500:
            time.sleep(3 * attempt)
            continue
        raise RuntimeError(f"Discord gönderimi başarısız (HTTP {status}): {json.dumps(data)[:300]}")
    raise RuntimeError("Discord gönderimi tekrar denemelerden sonra başarısız oldu")


# --------------------------------------------------------------------------- #
# Durum (görülen postlar)
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("UYARI: state.json bozuk, sıfırdan başlanıyor.")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Ana akış
# --------------------------------------------------------------------------- #

def process_article(board: str, article_id: str, dry_run: bool) -> None:
    article = fetch_article(board, article_id)
    body_text = html_to_text(article["html"])
    log(f"Çevriliyor: {article['title']} ({len(body_text)} karakter)")
    tr_title, tr_body = translate(article["title"], body_text)
    if dry_run:
        print("=" * 70)
        print(f"[{board}] {tr_title}\n{article_url(board, article_id)}\n")
        print(tr_body)
        print("=" * 70)
        for i, p in enumerate(split_for_discord(tr_body), 1):
            log(f"  Discord parçası {i}: {len(p)} karakter")
    else:
        post_to_discord(article, tr_title, tr_body)
        log(f"Discord'a gönderildi: {tr_title}")


def main() -> int:
    dry_run = os.environ.get("DRY_RUN") == "1"
    provider = (os.environ.get("TRANSLATOR") or "gemini").strip().lower()
    required = ["ANTHROPIC_API_KEY" if provider == "claude" else "GEMINI_API_KEY"]
    if not dry_run:
        required.append("DISCORD_WEBHOOK_URL")
    for var in required:
        if not os.environ.get(var):
            log(f"HATA: {var} ortam değişkeni tanımlı değil.")
            return 2

    # Tek post test modu
    test = os.environ.get("TEST_ARTICLE", "").strip()
    if test:
        board, _, art_id = test.partition(":")
        if not art_id:
            log("HATA: TEST_ARTICLE 'pano:articleId' biçiminde olmalı.")
            return 2
        process_article(board, art_id, dry_run)
        return 0

    boards = [b.strip() for b in (os.environ.get("BOARDS") or "update_en,notice_en").split(",") if b.strip()]
    first_run_posts = int(os.environ.get("POST_ON_FIRST_RUN", "0") or 0)
    state = load_state()
    failures = 0

    for board in boards:
        try:
            items = fetch_board_list(board)
        except Exception as e:  # noqa: BLE001
            log(f"HATA: {e}")
            failures += 1
            continue

        ids_newest_first = [it["id"] for it in items if it.get("id")]
        seen: list[str] = state.get(board, [])

        if board not in state:
            # İlk çalıştırma: eski postlarla kanalı doldurmamak için sadece kaydet
            to_post = list(reversed(ids_newest_first[:first_run_posts]))
            seen = [i for i in ids_newest_first if i not in to_post]
            log(f"{board}: ilk çalıştırma, {len(seen)} mevcut post kaydedildi"
                + (f", {len(to_post)} tanesi gönderilecek." if to_post else "."))
        else:
            to_post = [i for i in reversed(ids_newest_first) if i not in seen]  # eskiden yeniye

        if not to_post:
            log(f"{board}: yeni post yok.")
        for art_id in to_post:
            try:
                process_article(board, art_id, dry_run)
                seen.append(art_id)
            except Exception as e:  # noqa: BLE001
                # Görülmedi say; bir sonraki çalıştırmada tekrar denenir
                log(f"HATA ({board}/{art_id}): {e}")
                failures += 1

        state[board] = seen[-STATE_KEEP:]

    if not dry_run:
        save_state(state)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
