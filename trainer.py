import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

import chess
import chess.pgn


FenStats = Dict[str, Dict[str, int]]


def _iter_games(pgn_paths: Iterable[Path]):
    """Yield games from a list of PGN files."""
    for pgn_path in pgn_paths:
        with pgn_path.open("r", encoding="utf-8") as f:
            while True:
                game = chess.pgn.read_game(f)
                if game is None:
                    break
                yield game


def build_fen_stats(pgn_paths: Iterable[Path]) -> FenStats:
    """
    Build statistics: for each FEN, which SAN moves you played and how often.
    """
    fen_to_moves: Dict[str, Counter] = defaultdict(Counter)

    for game in _iter_games(pgn_paths):
        board = game.board()

        # We don't filter by color here; both sides are "you" for now.
        for move in game.mainline_moves():
            fen_before = board.fen()
            san = board.san(move)
            fen_to_moves[fen_before][san] += 1
            board.push(move)

    # Convert Counter objects to plain dicts for JSON serialization
    serializable: FenStats = {
        fen: dict(counter) for fen, counter in fen_to_moves.items()
    }
    return serializable


def save_fen_stats(stats: FenStats, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False)


def load_fen_stats(path: Path) -> FenStats:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Type hint: keys are FEN, values are {san: count}
    return {fen: {san: int(count) for san, count in moves.items()} for fen, moves in data.items()}


def parse_pgn_inputs(pgn_input: str) -> List[Path]:
    """
    Accept either:
    - a single PGN file
    - a directory (all *.pgn files inside, non-recursive)
    - a text file listing PGN paths (one per line)
    """
    p = Path(pgn_input)

    if p.is_dir():
        return sorted(p.glob("*.pgn"))

    if p.is_file() and p.suffix.lower() == ".pgn":
        return [p]

    if p.is_file() and p.suffix.lower() in {".txt", ".lst"}:
        paths: List[Path] = []
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                paths.append(Path(line))
        return paths

    raise SystemExit(f"PGN girdisi anlaşılamadı: {pgn_input}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PGN oyunlarından 'senin gibi oynayan' FEN->hamle istatistik modeli üretir."
    )
    parser.add_argument(
        "pgn_input",
        help="PGN dosyası, PGN klasörü veya PGN dosya listesi (satır satır).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="data/fen_stats.json",
        help="Çıktı model dosyası (JSON). Varsayılan: data/fen_stats.json",
    )

    args = parser.parse_args()
    pgn_paths = parse_pgn_inputs(args.pgn_input)

    if not pgn_paths:
        raise SystemExit("Hiç PGN dosyası bulunamadı.")

    print(f"{len(pgn_paths)} adet PGN dosyasından istatistik çıkarılıyor...")
    stats = build_fen_stats(pgn_paths)
    out_path = Path(args.output)
    save_fen_stats(stats, out_path)
    print(f"Model kaydedildi: {out_path} (toplam {len(stats)} farklı FEN)")


if __name__ == "__main__":
    main()

