# AION 2 Türkçe Duyuru Botu

AION 2 Global sitesindeki **Update** ve **Notice** panolarına yeni bir post düştüğünde,
postu Google Gemini API ile Türkçeye çevirir ve Discord kanalına gönderir.
GitHub Actions üzerinde her 15 dakikada bir çalışır, bilgisayarının açık olması gerekmez.
**Tamamen ücretsiz:** GitHub Actions (public repo), Discord webhook ve Gemini ücretsiz katmanı.

- Kaynak: `https://aion2.plaync.com/en-us/board/update/list` ve `.../notice/list`
- Çeviri: Gemini API ücretsiz katmanı (oyun içi adlar İngilizce kalır)
- Gönderim: Discord webhook (uzun yama notları otomatik olarak birkaç mesaja bölünür)
- Bağımlılık yok: Sadece Python standart kütüphanesi

---

## Kurulum (yaklaşık 10 dakika)

### 1. Discord webhook oluştur
1. Discord'da çevirilerin düşeceği kanalın yanındaki ⚙️ **Kanalı Düzenle** simgesine tıkla.
2. **Entegrasyonlar → Webhook'lar → Yeni Webhook**.
3. İstersen adını (ör. *AION 2 TR*) ve avatarını değiştir.
4. **Webhook URL'sini Kopyala**.

> ⚠️ Bu linki kimseyle paylaşma. Linki bilen herkes o kanala mesaj atabilir.
> Yanlışlıkla paylaşırsan aynı ekrandan webhook'u silip yenisini oluştur.

### 2. Gemini API anahtarı al (ücretsiz, kart gerekmez)
1. https://aistudio.google.com adresine Google hesabınla gir.
2. Sol menüden **Get API key → Create API key**.
3. Anahtarı kopyala. Faturalandırma (billing) açmana gerek yok; açmazsan ücretsiz katmanda kalırsın
   ve hiçbir zaman ücret çıkmaz.

> Not: Google'ın şartlarına göre ücretsiz katmandaki istekler Google ürünlerini geliştirmek için
> kullanılabilir. Burada çevrilen şey herkese açık yama notları olduğu için sorun değil.

### 3. GitHub reposunu oluştur
1. GitHub'da yeni bir repo oluştur (ör. `aion2-tr-bot`).
   - **Public** önerilir: Public repolarda GitHub Actions dakikaları ücretsiz ve sınırsız.
     Anahtarlar Secrets'ta saklandığı için public repoda da kimse göremez.
   - **Private** seçersen ayda 2.000 ücretsiz dakika var. 15 dakikalık aralık bunu aşar;
     workflow dosyasındaki `*/15` değerini `*/30` yap.
2. Bu klasördeki tüm dosyaları repoya yükle. `.github/workflows/aion2-bot.yml` dosyası da dahil
   (GitHub web arayüzünde "Add file → Upload files" ile klasörü sürükleyip bırakabilirsin).

### 4. Anahtarları GitHub Secrets'a ekle
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Ad | Değer |
|---|---|
| `GEMINI_API_KEY` | Gemini API anahtarın |
| `DISCORD_WEBHOOK_URL` | Discord webhook linkin |

### 5. Yazma iznini aç
Repo → **Settings → Actions → General → Workflow permissions** →
**Read and write permissions** seç ve kaydet.
(Bot, hangi postları gönderdiğini `state.json` dosyasına yazıp repoya kaydediyor.)

### 6. Test et
Repo → **Actions → AION 2 Türkçe Duyuru Botu → Run workflow**:

- **Önce sadece çeviriyi görmek için:** `test_article` alanına
  `update_en:6af42a1affaccc00c265785f` yaz, `dry_run` kutusunu işaretle → Run.
  Çeviri, çalışmanın log'unda görünür, Discord'a bir şey gitmez.
- **Discord'a gerçekten göndermek için:** Aynısını `dry_run` işaretlemeden çalıştır.
  11 Kasım yama notu kanalına düşmeli.
- **Normal çalışmayı başlatmak için:** Alanları boş bırakıp çalıştır.
  İlk çalıştırmada mevcut postlar sadece "görüldü" olarak kaydedilir, kanala eski postlar yağmaz.
  Bundan sonra yeni gelen her post otomatik çevrilip gönderilir.

---

## İsteğe bağlı ayarlar
Repo → **Settings → Secrets and variables → Actions → Variables** sekmesinden eklenir:

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `GEMINI_MODELS` | `gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-2.5-flash` | Sırayla denenecek modeller. Biri kotaya takılır ya da kullanımdan kalkarsa sıradakine geçilir |
| `TRANSLATOR` | `gemini` | `claude` yaparsan Claude API kullanılır (`ANTHROPIC_API_KEY` secret'ı gerekir, ücretli) |
| `BOARDS` | `update_en,notice_en` | İzlenecek panolar. Sadece yama notları için `update_en` |
| `POST_ON_FIRST_RUN` | `0` | İlk çalıştırmada her panodan kaç eski post gönderilsin |

## Kendi bilgisayarında çalıştırma (isteğe bağlı)
```bash
export GEMINI_API_KEY="..."
export DISCORD_WEBHOOK_URL="..."
python3 bot.py                                   # normal çalışma
DRY_RUN=1 TEST_ARTICLE=update_en:6af42a1affaccc00c265785f python3 bot.py   # sadece çeviriyi göster
```

## Nasıl çalışıyor?
1. `api-global-community.plaync.com/aion2_global/board/{pano}/article/search/moreArticle`
   adresinden panonun son 10 postunu çeker. Bu, sitenin kendi kullandığı herkese açık API.
2. `state.json` içindeki görülen ID'lerle karşılaştırıp yenileri bulur.
3. `.../article/{id}` adresinden postun HTML içeriğini alır ve sade metne çevirir (tablolar korunur).
4. Gemini API'ye çeviri talimatlarıyla gönderir. İlk model hata verirse listedeki sıradaki model denenir.
5. Çeviriyi Discord embed'leri halinde (her biri en fazla ~3.900 karakter) webhook'a yollar.
6. Başarılı gönderilen postu `state.json`'a ekler. Bir adım hata verirse post görülmedi sayılır
   ve bir sonraki çalışmada tekrar denenir.

## Sorun giderme
- **Actions sekmesinde çalışma görünmüyor:** Workflow dosyasının yolu tam olarak
  `.github/workflows/aion2-bot.yml` olmalı.
- **"Permission denied" ile state.json kaydedilemiyor:** 5. adımdaki yazma iznini kontrol et.
- **Discord HTTP 401/404:** Webhook linki yanlış ya da webhook silinmiş.
- **Gemini HTTP 400/403 "API key not valid":** Anahtar yanlış kopyalanmış, AI Studio'dan yenisini al.
- **Gemini HTTP 429:** Ücretsiz katmanın dakikalık/günlük sınırı. Bot bekleyip tekrar dener, günlük
  kota bittiyse sıradaki modele geçer. Post görülmedi sayıldığı için kaçmaz, sonraki çalışmada gönderilir.
- **Gemini HTTP 404:** Model adı kullanımdan kalkmış. `GEMINI_MODELS` değişkenine güncel bir Flash modeli yaz
  (güncel liste: https://ai.google.dev/gemini-api/docs/models).
- **Zamanlanmış çalışmalar durdu:** GitHub, 60 gün boyunca hiç commit almayan public repolarda
  zamanlanmış görevleri kapatır. Bot her yeni postta `state.json`'u commit'lediği için normalde
  bu olmaz. Olursa Actions sekmesinden tekrar etkinleştir.
