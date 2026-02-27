"""
Lichess bot iskeleti.

Bu dosya, Lichess Bot API ile konuşup gelen oyunlarda YourStyleEngine'i
hamle seçmek için kullanır.

NOT:
- Buradaki kod, Lichess API ile tamamen test edilemedi; küçük ayarlar
  yapman gerekebilir.
- Lichess dokümantasyonu: https://lichess.org/api#tag/Bot
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Dict, List

import requests

import chess

from engine import YourStyleEngine, load_engine_from_config

LICHESS_API = "https://lichess.org"


def auth_headers() -> Dict[str, str]:
    token = os.environ.get("LICHESS_TOKEN")
    if not token:
        raise SystemExit(
            "LICHESS_TOKEN environment değişkeni tanımlı değil.\n"
            "Lichess hesabından bir API token üret ve terminalde:\n"
            "  export LICHESS_TOKEN='xxxxxxxx'\n"
            "şeklinde ayarla."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/x-ndjson",
    }


def json_headers() -> Dict[str, str]:
    """Normal JSON endpoint'leri için header."""
    token = os.environ.get("LICHESS_TOKEN")
    if not token:
        raise SystemExit(
            "LICHESS_TOKEN environment değişkeni tanımlı değil.\n"
            "Lichess hesabından bir API token üret ve terminalde:\n"
            "  export LICHESS_TOKEN='xxxxxxxx'\n"
            "şeklinde ayarla."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def get_my_bot_id() -> str:
    """Token'e karşılık gelen hesabın Lichess id'sini al."""
    url = f"{LICHESS_API}/api/account"
    r = requests.get(url, headers=json_headers())
    r.raise_for_status()
    data = r.json()
    return data["id"]


def get_online_bots() -> List[Dict]:
    """Çevrimiçi botların listesini al. Lichess /api/bot/online (NDJSON)."""
    url = f"{LICHESS_API}/api/bot/online"
    r = requests.get(url, headers=auth_headers())
    r.raise_for_status()
    bots = []
    for line in r.text.strip().split("\n"):
        if not line:
            continue
        try:
            bots.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return bots


def challenge_user(username: str, rated: bool = False, clock_limit: int = 180, clock_increment: int = 2) -> bool:
    """
    Belirtilen kullanıcıya meydan oku. Başarılı True döner.
    """
    url = f"{LICHESS_API}/api/challenge/{username}"
    payload = {
        "rated": str(rated).lower(),
        "clock.limit": str(clock_limit),
        "clock.increment": str(clock_increment),
        "variant": "standard",
        "color": "random",
    }
    r = requests.post(url, headers=json_headers(), data=payload)
    if r.status_code == 200:
        return True
    try:
        err = r.json()
        msg = err.get("error", err.get("message", r.text[:150]))
    except Exception:
        msg = r.text[:150]
    print(f"Meydan okuma hatası ({username}, status={r.status_code}): {msg}")
    return False


def stream_events():
    """
    Genel event stream'i: yeni challenge'lar, başlayan oyunlar vs.
    """
    url = f"{LICHESS_API}/api/stream/event"
    with requests.get(url, headers=auth_headers(), stream=True) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            yield json.loads(line.decode("utf-8"))


def accept_challenge(challenge_id: str) -> bool:
    """Challenge'ı kabul et. Başarılı True, hata False döner."""
    url = f"{LICHESS_API}/api/challenge/{challenge_id}/accept"
    try:
        r = requests.post(url, headers=json_headers())
        if r.status_code == 200:
            print(f"Challenge {challenge_id} kabul edildi, oyun başlayacak.")
            return True
        print(f"Challenge kabul hatası (status={r.status_code}): {r.text[:200]}")
        return False
    except Exception as e:
        print(f"Challenge kabul hatası: {e}")
        return False


def make_move(game_id: str, move_uci: str) -> None:
    url = f"{LICHESS_API}/api/bot/game/{game_id}/move/{move_uci}"
    r = requests.post(url, headers=auth_headers())
    r.raise_for_status()


def stream_game(game_id: str, engine: YourStyleEngine, my_id: str) -> None:
    """
    Tek bir oyunu stream edip gerektiğinde hamle oynar.
    """
    url = f"{LICHESS_API}/api/bot/game/stream/{game_id}"
    with requests.get(url, headers=auth_headers(), stream=True) as r:
        if r.status_code != 200:
            # Hata mesajını yazdır ve bu oyunu yok say
            try:
                err_text = r.text
            except Exception:
                err_text = "<body okunamadı>"
            print(
                f"Oyun stream başlatılamadı (id={game_id}), "
                f"status={r.status_code}, body={err_text!r}"
            )
            return
        board = chess.Board()
        my_is_white: bool | None = None

        for line in r.iter_lines():
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            t = data.get("type")

            if t == "gameFull":
                # Başlangıç konumu ve kim kim
                initial = data.get("initialFen", "startpos")
                if initial != "startpos":
                    board.set_fen(initial)

                white_id = data["white"]["id"]
                black_id = data["black"]["id"]
                if my_id == white_id:
                    my_is_white = True
                elif my_id == black_id:
                    my_is_white = False
                else:
                    # Bu oyun bize ait değil, güvenlik için hiç oynamayalım.
                    my_is_white = None

                state_moves = data.get("state", {}).get("moves", "")
                if state_moves:
                    for m in state_moves.split():
                        board.push_uci(m)

                _play_if_our_turn(game_id, board, engine, my_is_white)

            elif t == "gameState":
                moves = data.get("moves", "")
                board = chess.Board()
                for m in moves.split():
                    board.push_uci(m)
                _play_if_our_turn(game_id, board, engine, my_is_white)

            elif t == "chatLine":
                # Sohbet mesajları, şimdilik yok sayıyoruz
                continue


def _play_if_our_turn(
    game_id: str,
    board: chess.Board,
    engine: YourStyleEngine,
    my_is_white: bool | None,
) -> None:
    if board.is_game_over() or my_is_white is None:
        return

    my_turn = (board.turn and my_is_white) or (not board.turn and not my_is_white)
    if not my_turn:
        return

    move = engine.pick_move(board)
    make_move(game_id, move.uci())


def run_bot(config_path: Path) -> None:
    engine = load_engine_from_config(config_path)
    my_id = get_my_bot_id()

    # config.json'dan challenge ayarlarını oku
    challenge_humans: List[str] = []
    challenge_interval: int = 1200
    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        challenge_humans = [u.strip() for u in cfg.get("challenge_humans", []) if isinstance(u, str) and u.strip()]
        challenge_interval = max(30, int(cfg.get("challenge_interval_seconds", 1200)))
    except Exception:
        pass

    print(f"YourStyleEngine yüklendi. Lichess id: {my_id}")
    print(f"Bota oynamak için: https://lichess.org/@{my_id}  (sayfadan 'Meydan oku')")
    if challenge_humans:
        print(f"İnsanlara meydan okuma listesi: {challenge_humans}")
    print(f"Meydan okuma aralığı: {challenge_interval} saniye ({challenge_interval // 60} dakika)")
    print("Lichess event stream dinleniyor...")

    def challenge_loop() -> None:
        """Periyodik olarak botlara ve challenge_humans listesindeki insanlara meydan oku."""
        while True:
            try:
                targets: List[str] = []

                # İnsan listesinden rastgele biri
                if challenge_humans:
                    targets.append(random.choice(challenge_humans))

                # Çevrimiçi botlardan rastgele biri
                bots = get_online_bots()
                others = [b.get("id") for b in bots if b.get("id") and b.get("id") != my_id]
                if others:
                    targets.append(random.choice(others))

                if targets:
                    target = random.choice(targets)
                    if challenge_user(target, rated=False, clock_limit=180, clock_increment=2):
                        print(f"{target} adresine meydan okundu (3+2).")
                else:
                    print("Hedef yok (bot veya insan listesi boş), bekleniyor...")
            except Exception as exc:
                print(f"Meydan okuma hatası: {exc}")
            time.sleep(challenge_interval)

    challenge_thread = threading.Thread(target=challenge_loop, daemon=True)
    challenge_thread.start()

    for event in stream_events():
        t = event.get("type")
        print(f"[Event] {t}")  # Gelen her event tipini göster

        if t == "challenge":
            challenge = event["challenge"]
            ch_id = challenge["id"]
            challenger_id = challenge.get("challenger", {}).get("id", "")
            # Kendimiz meydan okuduysak kabul etme; karşı taraf kabul edince gameStart gelir
            if challenger_id == my_id:
                print(f"Kendi meydan okumamız ({ch_id}), karşı tarafın kabulünü bekliyoruz.")
                continue
            print(f"Yeni challenge: {ch_id} (gönderen: {challenger_id}), kabul ediliyor...")
            accept_challenge(ch_id)

        elif t == "gameStart":
            game_id = event["game"]["id"]
            print(f"Oyun başladı: {game_id}, stream ediliyor...")
            thread = threading.Thread(
                target=stream_game, args=(game_id, engine, my_id), daemon=True
            )
            thread.start()


if __name__ == "__main__":
    cfg = Path("config.json")
    if not cfg.exists():
        raise SystemExit(
            "config.json bulunamadı.\n"
            "Örnek bir config için README.md dosyasına bak."
        )
    run_bot(cfg)

