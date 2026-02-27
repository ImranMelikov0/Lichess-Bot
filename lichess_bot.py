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
from typing import Dict, List, Optional, Tuple

import requests

import chess

from engine import YourStyleEngine, load_engine_from_config

LICHESS_API = "https://lichess.org"
ROTATION_STATE_FILE = Path("data/challenge_rotation.json")
CHAT_STATE_FILE = Path("data/chat_state.json")

# Meydan okuma rotasyonu: (isim, clock_limit, clock_increment, variant, days)
# classical: 30+0, 30+20, 60+20, 120+20 | chess960: blitz + rapid + classical
CHALLENGE_ROTATION = [
    ("bullet", 60, 1, "standard", 0),
    ("bullet", 60, 0, "standard", 0),
    ("blitz", 180, 2, "standard", 0),
    ("blitz", 180, 0, "standard", 0),
    ("blitz", 300, 3, "standard", 0),
    ("rapid", 600, 0, "standard", 0),
    ("rapid", 600, 5, "standard", 0),
    ("rapid", 900, 10, "standard", 0),
    ("classical", 1800, 0, "standard", 0),
    ("classical", 1800, 20, "standard", 0),
    ("classical", 3600, 20, "standard", 0),   # 1 saat + 20
    ("classical", 7200, 20, "standard", 0),   # 2 saat + 20
    ("chess960", 180, 2, "chess960", 0),
    ("chess960", 180, 0, "chess960", 0),
    ("chess960", 300, 3, "chess960", 0),
    ("chess960", 600, 0, "chess960", 0),
    ("chess960", 600, 5, "chess960", 0),
    ("chess960", 900, 10, "chess960", 0),
    ("chess960", 1800, 0, "chess960", 0),
    ("chess960", 1800, 20, "chess960", 0),
    ("chess960", 3600, 20, "chess960", 0),
    ("chess960", 7200, 20, "chess960", 0),
    ("correspondence", 0, 0, "standard", 1),
]


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


def challenge_user(
    username: str,
    rated: bool = False,
    clock_limit: int = 180,
    clock_increment: int = 2,
    variant: str = "standard",
    days: int = 0,
) -> Optional[str]:
    """
    Belirtilen kullanıcıya meydan oku.
    Başarılıysa challenge id döner, değilse None.
    """
    url = f"{LICHESS_API}/api/challenge/{username}"
    payload: Dict[str, str] = {
        "rated": str(rated).lower(),
        "variant": variant,
        "color": "random",
    }
    if days > 0:
        payload["days"] = str(days)
    else:
        payload["clock.limit"] = str(clock_limit)
        payload["clock.increment"] = str(clock_increment)
    r = requests.post(url, headers=json_headers(), data=payload)
    if r.status_code == 200:
        try:
            data = r.json()
            return str(data.get("challenge", {}).get("id") or data.get("id") or "")
        except Exception:
            return ""
    try:
        err = r.json()
        msg = err.get("error", err.get("message", r.text[:150]))
    except Exception:
        msg = r.text[:150]
    print(f"Meydan okuma hatası ({username}, status={r.status_code}): {msg}")
    return None


def send_chat(game_id: str, room: str, text: str) -> None:
    """Oyunda sohbet mesajı gönder. room: 'player' veya 'spectator'."""
    url = f"{LICHESS_API}/api/bot/game/{game_id}/chat"
    headers = {**auth_headers(), "Accept": "*/*"}  # Chat endpoint NDJSON döndürmüyor
    try:
        r = requests.post(url, headers=headers, data={"room": room, "text": text[:140]})
        if r.status_code != 200:
            print(f"[Chat hata] game={game_id} status={r.status_code} body={r.text[:200]!r}")
    except Exception as e:
        print(f"[Chat hata] game={game_id} exception={e}")


def send_chat_both(game_id: str, text: str) -> None:
    """Mesajı hem rakibe (player) hem izleyicilere (spectator) gönderir."""
    send_chat(game_id, "player", text)
    send_chat(game_id, "spectator", text)


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


def _load_rotation_index() -> int:
    """Kaydedilmiş rotasyon indeksini yükle."""
    try:
        if ROTATION_STATE_FILE.exists():
            data = json.loads(ROTATION_STATE_FILE.read_text(encoding="utf-8"))
            return int(data.get("index", 0))
    except Exception:
        pass
    return 0


def _save_rotation_index(idx: int) -> None:
    """Rotasyon indeksini kaydet."""
    try:
        ROTATION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ROTATION_STATE_FILE.write_text(
            json.dumps({"index": idx}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def human_like_think_seconds(
    our_time_ms: int,
    our_inc_ms: int,
    board: chess.Board,
    move: chess.Move,
) -> float:
    """
    İnsan gibi düşünme: açılışta çok az, basit hamlelerde hızlı,
    karmaşık hamlelerde bazen düşün bazen hızlı. Yazışmada saatlerce.
    """
    time_sec = our_time_ms / 1000.0
    inc_sec = our_inc_ms / 1000.0
    has_increment = inc_sec >= 0.5
    move_count = board.fullmove_number * 2 - (0 if board.turn else 1)
    piece_count = len(board.piece_map())

    # Sınırsız süre (saat yok): direkt oyna, kısa gecikme
    if time_sec <= 0:
        return random.uniform(2.0, 10.0)

    # Yazışma: kalan süre günler mertebesinde (örn. > 12 saat)
    if time_sec > 43200:
        if move_count <= 16:
            return random.uniform(300, 1800)  # Açılışta 5-30 dk
        return random.uniform(7200, 28800)  # Orta/oyun sonu 2-8 saat

    # Bullet bölgesi
    if time_sec < 60:
        if not has_increment or time_sec <= 30:
            return random.uniform(0.1, 0.35)
        return random.uniform(0.4, 1.2)

    # Basit hamleler: hızlı oyna
    is_capture = board.is_capture(move)
    is_recapture = False
    if board.move_stack and is_capture:
        last = board.move_stack[-1]
        if board.is_capture(last):
            is_recapture = True
    is_king_move = board.piece_type_at(move.from_square) == chess.KING
    is_pawn_push = board.piece_type_at(move.from_square) == chess.PAWN and not is_capture

    if is_recapture:
        return random.uniform(0.15, 0.5)
    if is_capture and move_count <= 25:
        return random.uniform(0.2, 0.8)
    if is_king_move and piece_count <= 10:
        return random.uniform(0.2, 0.7)
    if move_count <= 16:
        if is_pawn_push or is_king_move:
            return random.uniform(0.2, 0.6)
        return random.uniform(0.3, 1.2)

    # Orta/oyun sonu: bazen hızlı bazen düşün (insan gibi)
    if random.random() < 0.35:
        return random.uniform(0.4, 1.5)
    if move_count <= 30:
        return random.uniform(1.5, 4.5)

    # Uzun klasik (1 saat+): bazen 15-25 dakika düşün (kritik pozisyonlar)
    if time_sec > 3600 and move_count > 25 and not is_capture:
        if random.random() < 0.08:
            return random.uniform(900, 1500)   # 15-25 dakika
        if random.random() < 0.18:
            return random.uniform(480, 900)    # 8-15 dakika
    # Klasik (20+ dk): bol süre varsa bazen 3-8 dakika düşün
    elif time_sec > 1200 and move_count > 20 and not is_capture:
        if random.random() < 0.15:
            return random.uniform(180, 480)  # 3-8 dakika
        if random.random() < 0.30:
            return random.uniform(90, 240)   # 1.5-4 dakika
    # Rapid (10+ dk): bazen 2-5 dakika düşün
    elif time_sec > 600 and move_count > 20 and not is_capture:
        if random.random() < 0.12:
            return random.uniform(120, 300)  # 2-5 dakika
        if random.random() < 0.22:
            return random.uniform(60, 180)   # 1-3 dakika
    # Rapid (5+ dk): bazen 1-5 dakika düşün
    elif time_sec > 300 and move_count > 18 and not is_capture:
        if random.random() < 0.10:
            return random.uniform(120, 300)  # 2-5 dakika
        if random.random() < 0.18:
            return random.uniform(45, 150)   # 45 sn - 2.5 dk

    # Klasik/rapid: varsayılan düşünme süresi (süre bolsa biraz daha uzun)
    if time_sec > 600:
        return random.uniform(2.0, 6.0)
    if time_sec > 300:
        return random.uniform(1.5, 4.5)
    return random.uniform(1.0, 3.5)


def _get_rotation_index() -> int:
    if not hasattr(_get_rotation_index, "_idx"):
        _get_rotation_index._idx = _load_rotation_index()  # type: ignore
    return _get_rotation_index._idx  # type: ignore


def get_current_challenge_config() -> Tuple[str, int, int, str, int]:
    """Mevcut konfigürasyonu döner, ilerlemez. Declined'da aynı mod tekrar gönderilir."""
    idx = _get_rotation_index()
    return CHALLENGE_ROTATION[idx % len(CHALLENGE_ROTATION)]


def advance_challenge_rotation() -> None:
    """Rotasyonu ileri al. Oyun kabul edilip oynandıktan sonra çağrılır."""
    if not hasattr(_get_rotation_index, "_idx"):
        _get_rotation_index._idx = _load_rotation_index()  # type: ignore
    next_idx = _get_rotation_index._idx + 1  # type: ignore
    _get_rotation_index._idx = next_idx  # type: ignore
    _save_rotation_index(next_idx)


# Materyal değerleri (centipawn yaklaşık)
_PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def _material_balance(board: chess.Board, my_is_white: bool) -> int:
    """Bizim materyalimiz - rakip materyali. Pozitif = biz öndeyiz."""
    ours = 0
    theirs = 0
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if p is None:
            continue
        v = _PIECE_VALUES.get(p.piece_type, 0)
        if (p.color == chess.WHITE and my_is_white) or (p.color == chess.BLACK and not my_is_white):
            ours += v
        else:
            theirs += v
    return ours - theirs


def _get_opponent_capture_squares(
    moves_list: List[str], initial_fen: str, my_is_white: bool
) -> set:
    """
    Rakibin bizim taşımızı yediği kareler - henüz geri almadığımız.
    Recapture yaptıysak o kareyi çıkarıyoruz.
    """
    opp_captured: set = set()
    try:
        board = chess.Board(initial_fen) if initial_fen and initial_fen != "startpos" else chess.Board()
    except Exception:
        board = chess.Board()
    our_color = chess.WHITE if my_is_white else chess.BLACK
    for i, uci in enumerate(moves_list):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            continue
        is_white_move = (i % 2) == 0
        if is_white_move == my_is_white:  # bizim hamlemiz
            if board.is_capture(move):
                opp_captured.discard(move.to_square)  # recapture - geri aldık
        else:  # rakip hamlesi
            if board.is_capture(move):
                captured = board.piece_at(move.to_square)
                if captured and captured.color == our_color:
                    opp_captured.add(move.to_square)
        board.push(move)
    return opp_captured


def _is_recapture(
    board: chess.Board,
    last_move_uci: str,
    opponent_capture_squares: set,
) -> bool:
    """
    Bizim capture'ımız recapture mı? (Rakibin daha önce bizim taşımızı yediği karede mi alıyoruz?)
    Hemen veya 2-3 hamle sonra geri almak fark etmez.
    """
    if not last_move_uci or not opponent_capture_squares:
        return False
    try:
        last = chess.Move.from_uci(last_move_uci)
    except ValueError:
        return False
    b = board.copy()
    b.pop()
    if not b.is_capture(last):
        return False
    return last.to_square in opponent_capture_squares


def _load_chat_config(raw: object) -> Dict[str, object]:
    """Config'ten chat ayarlarını yükler."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, object] = {}
    for k, v in raw.items():
        if v is None:
            continue
        if k == "opening":
            if isinstance(v, list):
                out[k] = [str(x)[:140] for x in v if isinstance(x, str) and str(x).strip()]
            elif isinstance(v, str) and v.strip():
                out[k] = [v[:140]]
        elif isinstance(v, list):
            out[k] = [str(x)[:140] for x in v if isinstance(x, str) and str(x).strip()]
        elif isinstance(v, str) and v.strip():
            out[k] = v[:140]
    return out


def _we_captured_before(moves_list: List[str], initial_fen: str, my_is_white: bool) -> bool:
    """Bu hamlelerden önce biz hiç capture yaptık mı? (Biz ilk taşı yediysek True)"""
    try:
        board = chess.Board(initial_fen) if initial_fen and initial_fen != "startpos" else chess.Board()
    except Exception:
        board = chess.Board()
    for i, uci in enumerate(moves_list):
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            continue
        if board.is_capture(move):
            # i=0,2,4... beyaz hamlesi | i=1,3,5... siyah hamlesi
            is_our_move = (i % 2 == 0 and my_is_white) or (i % 2 == 1 and not my_is_white)
            if is_our_move:
                return True  # Biz capture yaptık
            return False  # Rakip capture yaptı (biz henüz yapmadık)
        board.push(move)
    return False


def _load_chat_state(game_id: str) -> Dict[str, object]:
    """Oyun için kaydedilmiş chat state'i yükle (yeniden bağlanmada kullanılır)."""
    try:
        if CHAT_STATE_FILE.exists():
            data = json.loads(CHAT_STATE_FILE.read_text(encoding="utf-8"))
            return dict(data.get(game_id, {}))
    except Exception:
        pass
    return {}


def _save_chat_state(game_id: str, state: Dict[str, object]) -> None:
    """Chat state'i dosyaya kaydet."""
    try:
        data = {}
        if CHAT_STATE_FILE.exists():
            data = json.loads(CHAT_STATE_FILE.read_text(encoding="utf-8"))
        # Sadece persist edilebilir alanları kaydet
        persist_keys = (
            "check_count", "sent_middle_game", "sent_opponent_bad_move", "sent_bol_sans",
            "sent_first_capture", "sent_we_material_up", "sent_opponent_material_up",
            "sent_position_good", "sent_position_bad", "last_chat_move",
            "material_advantage_since_move", "opponent_advantage_since_move", "prev_material",
        )
        data[game_id] = {k: state.get(k) for k in persist_keys if k in state}
        CHAT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHAT_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _clear_chat_state(game_id: str) -> None:
    """Oyun bittiğinde state'i dosyadan sil."""
    try:
        if CHAT_STATE_FILE.exists():
            data = json.loads(CHAT_STATE_FILE.read_text(encoding="utf-8"))
            data.pop(game_id, None)
            CHAT_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _opponent_captured_our_piece(board: chess.Board, prev_move_uci: str, my_is_white: bool) -> bool:
    """Rakibin son hamlesi bizim herhangi bir taşımızı mı yedi?"""
    if not prev_move_uci:
        return False
    try:
        prev = chess.Move.from_uci(prev_move_uci)
    except ValueError:
        return False
    b = board.copy()
    b.pop()  # rakip hamleden önce
    if not b.is_capture(prev):
        return False
    if b.is_en_passant(prev):
        return True  # En passant: rakip bizim piyonumuzu yedi
    captured = b.piece_at(prev.to_square)
    our_color = chess.WHITE if my_is_white else chess.BLACK
    return captured is not None and captured.color == our_color


def _process_chat(
    game_id: str,
    board: chess.Board,
    my_is_white: bool | None,
    moves_list: List[str],
    chat: Dict[str, object],
    state: Dict[str, object],
    initial_fen: str = "startpos",
    game_status: Optional[str] = None,
) -> None:
    """
    Oyun durumuna göre sohbet mesajı gönderir.
    state: prev_material, check_count, sent_middle_game, opponent_captured_pawn, sent_opponent_bad_move, sent_bol_sans
    """
    if not chat or my_is_white is None:
        return

    move_count = len(moves_list)
    last_move = moves_list[-1] if moves_list else ""
    prev_move = moves_list[-2] if len(moves_list) >= 2 else ""
    last_was_ours = (board.turn == chess.WHITE) != my_is_white  # sıra rakibindeyse biz oynadık

    # Oyun bitti mi? (board.is_game_over veya API status: mate, resign, draw, stalemate, timeout)
    game_ended = board.is_game_over() or game_status in ("mate", "resign", "draw", "stalemate", "outoftime")
    if game_ended:
        winner = None
        if board.is_game_over() and board.outcome():
            winner = board.outcome().winner
        elif game_status == "resign":
            # Sıra kimdeyse o çekilmiş demektir. Sıra bizdeyse biz kaybettik.
            we_resigned = (board.turn == chess.WHITE) == my_is_white
            winner = (chess.BLACK if my_is_white else chess.WHITE) if we_resigned else (chess.WHITE if my_is_white else chess.BLACK)
        elif game_status == "outoftime":
            # Zaman aşımı - sıra kimdeyse o kaybetti
            we_timeout = (board.turn == chess.WHITE) == my_is_white
            winner = (chess.BLACK if my_is_white else chess.WHITE) if we_timeout else (chess.WHITE if my_is_white else chess.BLACK)
        elif game_status in ("draw", "stalemate"):
            winner = None
        elif game_status == "mate":
            outcome = board.outcome()
            winner = outcome.winner if outcome else None

        _clear_chat_state(game_id)  # Oyun bitti, state'i temizle
        if winner is None:  # berabere
            msg = chat.get("draw")
            if isinstance(msg, list):
                msg = random.choice([m for m in msg if isinstance(m, str) and m.strip()]) if msg else ""
            if isinstance(msg, str) and msg.strip():
                send_chat_both(game_id, msg[:140])
        elif (winner == chess.WHITE and my_is_white) or (winner == chess.BLACK and not my_is_white):
            # biz kazandık
            msg = chat.get("we_won")
            if isinstance(msg, list):
                msg = random.choice([m for m in msg if isinstance(m, str) and m.strip()]) if msg else ""
            if isinstance(msg, str) and msg.strip():
                send_chat_both(game_id, msg[:140])
        else:
            # biz kaybettik
            msg = chat.get("we_lost")
            if isinstance(msg, list):
                msg = random.choice([m for m in msg if isinstance(m, str) and m.strip()]) if msg else ""
            if isinstance(msg, str) and msg.strip():
                send_chat_both(game_id, msg[:140])
        return

    # Rakibin hamlesinden sonra (bizim sıramız)
    if last_was_ours:
        # Bizim hamlemiz - şah çektik mi?
        if board.is_check():
            check_count = state.get("check_count", 0)
            check_msgs = chat.get("check")
            if isinstance(check_msgs, list) and check_count < len(check_msgs) and check_count < 3:
                msg = check_msgs[check_count]
                if isinstance(msg, str) and msg.strip():
                    send_chat_both(game_id, msg[:140])
                    state["check_count"] = check_count + 1
                    state["last_chat_move"] = move_count

        # Materyal öne geçtik mi? (recapture hariç, 3 hamle korunduktan sonra mesaj)
        prev_material = state.get("prev_material", 0)
        curr_material = _material_balance(board, my_is_white)
        state["prev_material"] = curr_material
        if curr_material < 2:
            state["material_advantage_since_move"] = None
        else:
            opp_squares = _get_opponent_capture_squares(moves_list[:-1], initial_fen, my_is_white)
            if not _is_recapture(board, last_move, opp_squares):
                since = state.get("material_advantage_since_move")
                if since is None:
                    state["material_advantage_since_move"] = move_count
                elif move_count - since >= 3 and not state.get("sent_we_material_up"):
                    msg = chat.get("we_material_up")
                    if isinstance(msg, str) and msg.strip():
                        send_chat_both(game_id, msg[:140])
                        state["sent_we_material_up"] = True
                        state["last_chat_move"] = move_count

        # Pozisyon iyi mi / kötü mü (materyal tam belli olduğunda, bir kere)
        if curr_material >= 3 and not state.get("sent_position_good"):
            msg = chat.get("position_good")
            if isinstance(msg, str) and msg.strip():
                send_chat_both(game_id, msg[:140])
                state["sent_position_good"] = True
                state["last_chat_move"] = move_count
        elif curr_material <= -3 and not state.get("sent_position_bad"):
            msg = chat.get("position_bad")
            if isinstance(msg, str) and msg.strip():
                send_chat_both(game_id, msg[:140])
                state["sent_position_bad"] = True
                state["last_chat_move"] = move_count

        # Oyun ortasında bir kere - son 3 hamlede mesaj göndermediysek (diğer mesajlarla çakışmasın)
        last_chat = state.get("last_chat_move", 0)
        if (
            move_count >= 16
            and move_count <= 30
            and not state.get("sent_middle_game")
            and move_count - last_chat >= 3
        ):
            if random.random() < 0.3:
                msg = chat.get("middle_game_once")
                if isinstance(msg, str) and msg.strip():
                    send_chat_both(game_id, msg[:140])
                    state["sent_middle_game"] = True
                    state["last_chat_move"] = move_count

    else:
        # Rakip oynadı
        prev_material = state.get("prev_material", 0)
        curr_material = _material_balance(board, my_is_white)
        state["prev_material"] = curr_material

        # Rakip materyal öne geçti (en az 2 puan geride, 3 hamle korunduktan sonra mesaj)
        if curr_material > -2:
            state["opponent_advantage_since_move"] = None
        elif curr_material <= -2:
            since = state.get("opponent_advantage_since_move")
            if since is None:
                state["opponent_advantage_since_move"] = move_count
            elif move_count - since >= 3 and not state.get("sent_opponent_material_up"):
                msgs = chat.get("opponent_material_up")
                if isinstance(msgs, list):
                    for m in msgs:
                        if isinstance(m, str) and m.strip():
                            send_chat_both(game_id, m[:140])
                            time.sleep(1.5)
                elif isinstance(msgs, str) and msgs.strip():
                    send_chat_both(game_id, msgs[:140])
                state["sent_opponent_material_up"] = True
                state["last_chat_move"] = move_count

        # Çok materyal geri döndü (biz gerideydik, şimdi öne geçtik veya eşitlendik)
        if prev_material < -3 and curr_material >= -1:
            msg = chat.get("we_material_down")
            if isinstance(msg, str) and msg.strip():
                send_chat_both(game_id, msg[:140])
                state["last_chat_move"] = move_count

        # Rakibin kötü hamlesi - 12-16 hamle arası (düşünülecek zamanlarda)
        if 12 <= move_count <= 16 and not state.get("sent_opponent_bad_move"):
            if random.random() < 0.25:
                msg = chat.get("opponent_bad_move")
                if isinstance(msg, str) and msg.strip():
                    send_chat_both(game_id, msg[:140])
                    state["sent_opponent_bad_move"] = True
                    state["last_chat_move"] = move_count

        # Rakip bizim taşımızı yedi (ilk kan) - biz daha önce capture yapmadıysak
        if (
            _opponent_captured_our_piece(board, last_move, my_is_white)
            and not _we_captured_before(moves_list[:-1], initial_fen, my_is_white)
            and not state.get("sent_first_capture")
        ):
            msg = chat.get("opponent_captured_pawn")
            if isinstance(msg, str) and msg.strip():
                send_chat_both(game_id, msg[:140])
                state["sent_first_capture"] = True
                state["last_chat_move"] = move_count


def stream_game(
    game_id: str,
    engine: YourStyleEngine,
    my_id: str,
    chat_config: Optional[Dict[str, object]] = None,
) -> None:
    """
    Tek bir oyunu stream edip gerektiğinde hamle oynar.
    chat_config: Duruma göre mesajlar (opening, check, we_lost, vb.)
    """
    chat = chat_config or {}
    defaults: Dict[str, object] = {
        "prev_material": 0,
        "check_count": 0,
        "sent_middle_game": False,
        "sent_opponent_bad_move": False,
        "sent_bol_sans": False,
        "sent_first_capture": False,
        "sent_we_material_up": False,
        "sent_opponent_material_up": False,
        "sent_position_good": False,
        "sent_position_bad": False,
        "last_chat_move": 0,
    }
    chat_state = {**defaults, **_load_chat_state(game_id)}

    try:
        url = f"{LICHESS_API}/api/bot/game/stream/{game_id}"
        with requests.get(url, headers=auth_headers(), stream=True) as r:
            if r.status_code != 200:
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
            initial_fen = "startpos"
            my_is_white: bool | None = None
            is_chess960 = False

            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line.decode("utf-8"))
                t = data.get("type")

                if t == "gameFull":
                    variant = data.get("variant", "standard")
                    is_chess960 = variant == "chess960"
                    board = chess.Board(chess960=is_chess960)
                    initial_fen = data.get("initialFen", "startpos")
                    if initial_fen != "startpos":
                        board.set_fen(initial_fen)
                    else:
                        board.reset()

                    white_id = data["white"]["id"]
                    black_id = data["black"]["id"]
                    if my_id == white_id:
                        my_is_white = True
                    elif my_id == black_id:
                        my_is_white = False
                    else:
                        my_is_white = None

                    state = data.get("state", {})
                    state_moves = state.get("moves", "")
                    moves_list = state_moves.split() if state_moves else []
                    for m in moves_list:
                        try:
                            board.push_uci(m)
                        except chess.IllegalMoveError:
                            print(f"[{game_id}] Geçersiz hamle atlandı: {m!r} (variant={variant})")

                    # Açılış mesajları (3 adet, kısa aralıklarla) - yeniden bağlanmada veya oyun devam ediyorsa atla
                    opening = chat.get("opening")
                    if (
                        opening
                        and my_is_white is not None
                        and not chat_state.get("sent_bol_sans")
                        and len(moves_list) == 0
                    ):
                        if isinstance(opening, list):
                            for msg in opening[:3]:
                                if isinstance(msg, str) and msg.strip():
                                    send_chat_both(game_id, msg[:140])
                                    time.sleep(1.8)
                            chat_state["last_chat_move"] = len(moves_list)
                        elif isinstance(opening, str) and opening.strip():
                            send_chat_both(game_id, opening[:140])
                            chat_state["sent_bol_sans"] = True
                            chat_state["last_chat_move"] = len(moves_list)

                    if isinstance(opening, list) and len(opening) >= 3:
                        chat_state["sent_bol_sans"] = True

                    chat_state["prev_material"] = _material_balance(board, my_is_white) if my_is_white is not None else 0
                    game_status = state.get("status", "")
                    _process_chat(game_id, board, my_is_white, moves_list, chat, chat_state, initial_fen, game_status)
                    _save_chat_state(game_id, chat_state)

                    wtime = state.get("wtime", 60000)
                    btime = state.get("btime", 60000)
                    winc = state.get("winc", 0)
                    binc = state.get("binc", 0)
                    _play_if_our_turn(game_id, board, engine, my_is_white, wtime, btime, winc, binc)

                elif t == "gameState":
                    moves = data.get("moves", "")
                    moves_list = moves.split() if moves else []
                    if initial_fen and initial_fen != "startpos":
                        board = chess.Board(initial_fen, chess960=is_chess960)
                    else:
                        board = chess.Board(chess960=is_chess960)
                    for m in moves_list:
                        try:
                            board.push_uci(m)
                        except chess.IllegalMoveError:
                            print(f"[{game_id}] Geçersiz hamle atlandı: {m!r} (chess960={is_chess960})")

                    game_status = data.get("status", "")
                    _process_chat(game_id, board, my_is_white, moves_list, chat, chat_state, initial_fen, game_status)
                    _save_chat_state(game_id, chat_state)

                    wtime = data.get("wtime", 60000)
                    btime = data.get("btime", 60000)
                    winc = data.get("winc", 0)
                    binc = data.get("binc", 0)
                    _play_if_our_turn(game_id, board, engine, my_is_white, wtime, btime, winc, binc)
                elif t == "chatLine":
                    continue
    finally:
        pass


def _play_if_our_turn(
    game_id: str,
    board: chess.Board,
    engine: YourStyleEngine,
    my_is_white: bool | None,
    wtime: int = 60000,
    btime: int = 60000,
    winc: int = 0,
    binc: int = 0,
) -> None:
    if board.is_game_over() or my_is_white is None:
        return

    my_turn = (board.turn and my_is_white) or (not board.turn and not my_is_white)
    if not my_turn:
        return

    move = engine.pick_move(board)
    our_time = wtime if my_is_white else btime
    our_inc = winc if my_is_white else binc
    delay = human_like_think_seconds(our_time, our_inc, board, move)
    if delay > 0:
        time.sleep(delay)
    make_move(game_id, move.uci())


def run_bot(config_path: Path) -> None:
    engine = load_engine_from_config(config_path)
    my_id = get_my_bot_id()

    challenge_humans: List[str] = []
    challenge_interval: int = 1200
    challenge_rated: bool = False
    challenge_declined_delay: int = 60
    chat_config: Dict[str, object] = {}
    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        challenge_humans = [u.strip() for u in cfg.get("challenge_humans", []) if isinstance(u, str) and u.strip()]
        challenge_interval = max(30, int(cfg.get("challenge_interval_seconds", 1200)))
        challenge_rated = bool(cfg.get("challenge_rated", False))
        challenge_declined_delay = max(15, int(cfg.get("challenge_declined_delay_seconds", 20)))
        chat_config = _load_chat_config(cfg.get("chat", {}))
    except Exception:
        pass

    # Declined'da hemen tekrar; kabul sonrası 20 dk bekle
    pending_challenge_id: Optional[str] = None
    retry_immediately = threading.Event()
    next_challenge_time: float = 0.0
    challenge_lock = threading.Lock()

    print(f"YourStyleEngine yüklendi. Lichess id: {my_id}")
    print(f"Bota oynamak için: https://lichess.org/@{my_id}  (sayfadan 'Meydan oku')")
    if challenge_humans:
        print(f"İnsanlara meydan okuma listesi: {challenge_humans}")
    print(f"Meydan okuma aralığı: {challenge_interval} sn (kabul sonrası); declined'da {challenge_declined_delay} sn bekle")
    print(f"Meydan okuma türü: {'Puanlı' if challenge_rated else 'Puansız'}")
    print("Lichess event stream dinleniyor...")

    def challenge_loop() -> None:
        nonlocal pending_challenge_id, next_challenge_time
        while True:
            try:
                now = time.time()
                if now < next_challenge_time:
                    wait = min(5, next_challenge_time - now)
                    retry_immediately.wait(timeout=wait)
                    continue

                retry_immediately.clear()
                targets: List[str] = []
                if challenge_humans:
                    targets.append(random.choice(challenge_humans))
                bots = get_online_bots()
                others = [b.get("id") for b in bots if b.get("id") and b.get("id") != my_id]
                if others:
                    targets.append(random.choice(others))

                if not targets:
                    print("Hedef yok, bekleniyor...")
                    time.sleep(60)
                    continue

                target = random.choice(targets)
                name, clk, inc, var, d = get_current_challenge_config()
                result = challenge_user(target, rated=challenge_rated, clock_limit=clk, clock_increment=inc, variant=var, days=d)
                if result is not None:
                    with challenge_lock:
                        pending_challenge_id = result or ""
                    tc = f"{clk//60}+{inc}" if d == 0 else f"{d}g"
                    print(f"{target} → {name} ({tc} {var})")
                else:
                    time.sleep(30)
            except Exception as exc:
                print(f"Meydan okuma hatası: {exc}")
                time.sleep(30)

    challenge_thread = threading.Thread(target=challenge_loop, daemon=True)
    challenge_thread.start()

    for event in stream_events():
        t = event.get("type")
        print(f"[Event] {t}")

        if t == "challenge":
            challenge = event["challenge"]
            ch_id = challenge["id"]
            challenger_id = challenge.get("challenger", {}).get("id", "")
            if challenger_id == my_id:
                print(f"Kendi meydan okumamız ({ch_id}), karşı tarafın kabulünü bekliyoruz.")
                continue
            print(f"Yeni challenge: {ch_id} (gönderen: {challenger_id}), kabul ediliyor...")
            accept_challenge(ch_id)

        elif t == "challengeDeclined":
            decl = event.get("challenge", {})
            decl_id = decl.get("id", "")
            with challenge_lock:
                if decl_id and decl_id == pending_challenge_id:
                    print(f"Meydan okumamız reddedildi ({decl_id}), {challenge_declined_delay} sn sonra tekrar.")
                    next_challenge_time = time.time() + challenge_declined_delay
                    retry_immediately.set()
                    pending_challenge_id = None

        elif t == "gameStart":
            game_id = event["game"]["id"]
            with challenge_lock:
                if pending_challenge_id:
                    advance_challenge_rotation()
                    pending_challenge_id = None
                    next_challenge_time = time.time() + challenge_interval
            print(f"Oyun başladı: {game_id}")
            thread = threading.Thread(
                target=stream_game,
                args=(game_id, engine, my_id, chat_config),
                daemon=True,
            )
            thread.start()


if __name__ == "__main__":
    cfg = Path("config.json")
    if not cfg.exists():
        raise SystemExit(
            "config.json bulunamadı.\n"
            "Örnek bir config için README.md dosyasına bak."
        )
    try:
        run_bot(cfg)
    except KeyboardInterrupt:
        print("\nBot durduruldu (Ctrl+C).")

