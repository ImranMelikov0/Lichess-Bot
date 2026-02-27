# Lichess "Senin Gibi Oynayan" Bot

Bu proje, kendi PGN oyunlarından öğrenip **senin stiline benzeyen** bir Lichess botu oluşturmak içindir.

Genel akış:

1. PGN oyunlarından FEN → hamle istatistiği çıkar (`trainer.py`)
2. Bu istatistiği kullanan bir motor oluştur (`engine.py`)
3. Motoru Lichess Bot API ile bağlayıp çevrim içi oyna (`lichess_bot.py`)

---

## Kurulum

Python 3.10+ önerilir.

```bash
cd myengine
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 1. PGN'lerinden model üret (trainer.py)

Elinde şu olsun:

- Tek bir büyük PGN dosyası: `data/my_games.pgn`
  - veya bir klasörde birden fazla PGN: `data/pgns/`

### Örnek kullanım

Tek dosya:

```bash
python trainer.py data/my_games.pgn -o data/fen_stats.json
```

Klasördeki tüm `*.pgn` dosyaları:

```bash
python trainer.py data/pgns -o data/fen_stats.json
```

PGN dosya listesi (satır satır):

```bash
python trainer.py data/pgn_list.txt -o data/fen_stats.json
```

Çıktı: `data/fen_stats.json`  
Bu dosya, her FEN için **senin oynadığın SAN hamlelerini ve sayısını** içerir.

---

## 2. Motoru yapılandır (engine.py)

`engine.py`, PGN modelini ve isteğe bağlı olarak Stockfish'i kullanarak hamle seçen `YourStyleEngine` sınıfını sağlar.

### config.json örneği

Proje kökünde bir `config.json` oluştur:

```json
{
  "model_path": "data/fen_stats.json",
  "stockfish_path": "/usr/local/bin/stockfish",
  "stockfish_depth": 12
}
```

- **model_path**: `trainer.py` ile ürettiğin JSON model dosyası
- **stockfish_path**: Stockfish binary yolu (istemiyorsan `null` veya alanı kaldır)
- **stockfish_depth**: Motor derinliği (büyük değer = daha güçlü ama daha yavaş)

Lokal kısa test:

```bash
python engine.py
```

`config.json` varsa sadece motoru yükleyip bir mesaj basar.

---

## 3. Offline modda botla oyna (offline.py)

Lichess'e bağlanmadan, sadece lokal olarak motorla oynamak istersen:

```bash
python offline.py --color white   # sen beyazsın
# veya
python offline.py --color black   # sen siyahsın
```

- `config.json` yine kullanılır (model + opsiyonel Stockfish ayarları).
- Tahta ASCII olarak terminalde gösterilir.
- Hamleni `e4`, `Nf3` gibi SAN veya `e2e4` gibi UCI formatında girebilirsin.
- Çıkmak için hamle yerine `quit` yazman yeterli.

---

## 4. Lichess botu çalıştır (lichess_bot.py)

### 3.1. Lichess tarafı

1. Lichess'te yeni bir hesap aç (bot hesabı olacak).
2. Hesap ayarlarından bu hesabı **Bot** moduna geçir.
3. `https://lichess.org/account/oauth/token` sayfasından yeni bir API token oluştur:
   - İzinler:
     - **Play games with the bot API**
     - (İstersen ek okuma/mesaj izinleri)
4. Token'ı bir ortam değişkeni olarak kaydet:

```bash
export LICHESS_TOKEN="senin_tokenin_buraya"
```

Her yeni terminal açtığında otomatik gelsin istiyorsan `.bashrc`, `.zshrc` vb. içine koy.

### 3.2. Botu başlat

`config.json` hazır ve `LICHESS_TOKEN` ayarlıysa:

```bash
python lichess_bot.py
```

Bot:

- Lichess event stream'ini dinler
- Gelen challenge'ları **kabul eder**
- Başlayan oyunlar için ayrı bir thread açar ve:
  - Tahtayı takip eder
  - Sıra geldiğini varsayarak `YourStyleEngine` ile hamle seçip oynar

> Not: Lichess Bot API akışı tam test edilemedi, pratikte ufak ayar/tweak gerekebilir.

---

## 5. Geliştirme fikirleri

- Sadece **senin taraf** olduğun (beyaz/siyah) oyunları modele dahil etmek
- Çok az oynadığın hamleleri filtrelemek (min. frekans)
- Hiç görmediğin konumlarda tamamen Stockfish'e bırakmak
- Belli açılışlarda tamamen senin stilini, oyun ortası/sonunda daha çok motoru ağırlıklandırmak

---

---
pgnleri her ay eklemek usulu
her ay tarihi filtrele son oyunlari indir data/pgns kismina ekle sonra 
asagidakileri calistir veya toplu sekilde tek pgn indir asagidakilardan birini calistir


insanlara karsi istek gondermesi icin boyle olmasi gerek

"challenge_humans": ["arkadas1", "arkadas2", "imranmelikov"]


- ```bash
  cd /Users/imranmelikov/Desktop/myengine
  source .venv/bin/activate
    ```

- # Tek dosya:
```bash
  python trainer.py data/pgns/lichess_feb2026.pgn -o data/fen_stats.json
```

- # Veya klasördeki tüm PGN'ler (eski + yeni):
 ```bash
    python trainer.py data/pgns -o data/fen_stats.json
 ```
---

## Özet kullanım

1. PGN'leri `data/` altına koy
2. Model üret:
   ```bash
   python trainer.py data/my_games.pgn -o data/fen_stats.json
   ```
3. `config.json` yarat
4. Lichess token'ını ayarla:
   ```bash
   export LICHESS_TOKEN="..."
   ```
5. Offline denemek için:
   ```bash
   python offline.py --color white
   ```
6. Online botu çalıştırmak için:
   ```bash
   python lichess_bot.py
   ```

