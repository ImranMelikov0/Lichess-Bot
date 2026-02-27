from __future__ import annotations

import argparse
from pathlib import Path

import chess

from engine import load_engine_from_config


def play_interactive(config_path: Path, color: str) -> None:
    """
    Terminal üzerinden motorla karşılıklı oyna.
    color: "white" ise sen beyaz, "black" ise sen siyah.
    """
    engine = load_engine_from_config(config_path)
    board = chess.Board()

    human_is_white = color.lower() == "white"

    print("Yeni oyun başladı.")
    print("Sen:", "Beyaz" if human_is_white else "Siyah")
    print(board)

    while not board.is_game_over():
        human_turn = board.turn and human_is_white or (not board.turn and not human_is_white)

        if human_turn:
            move = _ask_human_move(board)
            if move is None:
                print("Oyundan çıkılıyor.")
                return
            # SAN gösterimini push'tan önce hesapla
            human_san = board.san(move)
            board.push(move)
            print("\nSenin hamlen:", move.uci(), f"({human_san})")
        else:
            move = engine.pick_move(board)
            bot_san = board.san(move)
            board.push(move)
            print("\nBotun hamlesi:", move.uci(), f"({bot_san})")

        print(board)
        print("Durum:", board.result() if board.is_game_over() else "Oyun devam ediyor.")

    print("Oyun bitti. Sonuç:", board.result())


def _ask_human_move(board: chess.Board) -> chess.Move | None:
    """
    Kullanıcıdan hamle al (SAN veya UCI). 'quit' yazarsa None döner.
    """
    while True:
        raw = input("Hamlen (SAN veya UCI, çıkmak için 'quit'): ").strip()
        if raw.lower() in {"quit", "q", "exit"}:
            return None
        if not raw:
            continue

        # Önce SAN, sonra UCI dene
        try:
            move = board.parse_san(raw)
        except ValueError:
            try:
                move = chess.Move.from_uci(raw)
            except ValueError:
                print("Geçersiz hamle formatı. Örnek: e4, Nf3, e2e4")
                continue

        if move not in board.legal_moves:
            print("Bu hamle yasal değil, başka bir hamle dene.")
            continue

        return move


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline modda 'senin gibi oynayan' motorla oyna."
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.json",
        help="Motor yapılandırma dosyası (config.json). Varsayılan: config.json",
    )
    parser.add_argument(
        "--color",
        type=str,
        choices=["white", "black"],
        default="white",
        help="Senin taş rengin: white veya black. Varsayılan: white",
    )

    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        raise SystemExit(f"Config dosyası bulunamadı: {config_path}")

    play_interactive(config_path, args.color)


if __name__ == "__main__":
    main()

