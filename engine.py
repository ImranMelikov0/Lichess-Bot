from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import chess
import chess.engine

from trainer import FenStats, load_fen_stats


@dataclass
class YourStyleEngineConfig:
    model_path: Path
    stockfish_path: Optional[Path] = None
    stockfish_depth: int = 12
    # Oyun sonunda (az taş kaldığında) daha yüksek derinlik
    stockfish_endgame_depth: int = 20
    endgame_piece_count: int = 10  # Bu sayıdan az taş varsa oyun sonu sayılır
    # Stockfish'i sadece "blunder filtresi" olarak kullan
    use_stockfish_filter: bool = True
    # Senin en çok oynadığın ilk kaç hamleyi kontrol edelim
    max_candidate_moves: int = 3
    # En iyi hamleden kaç centipawn daha kötü olursa blunder sayalım
    blunder_threshold_cp: int = 200
    # Modelde hiç veri olmayan konumlarda Stockfish'e tamamen güvenelim mi?
    use_stockfish_fallback: bool = True


class YourStyleEngine:
    """
    Basit bir hibrit motor:

    1. Eğer eldeki FEN için senin PGN'lerinden hamle istatistiği varsa,
       en çok oynadığın hamleyi (veya ağırlıklı rastgele) oynar.
    2. Eğer FEN bilinmiyorsa ve Stockfish tanımlıysa, Stockfish'ten en iyi hamleyi alır.
    3. Aksi halde rastgele yasal bir hamle oynar.
    """

    def __init__(self, config: YourStyleEngineConfig):
        self.config = config
        self.stats: FenStats = load_fen_stats(config.model_path)
        self.engine: Optional[chess.engine.SimpleEngine] = None

        if config.stockfish_path is not None:
            self.engine = chess.engine.SimpleEngine.popen_uci(str(config.stockfish_path))

    def __del__(self) -> None:
        if self.engine is not None:
            try:
                self.engine.quit()
            except Exception:
                pass

    def pick_move(self, board: chess.Board) -> chess.Move:
        """
        Verilen tahtada bir hamle seç.
        Önce senin PGN modelini, sonra gerekiyorsa Stockfish'i, en son rastgeleliği kullanır.
        """
        move = self._from_your_stats(board)
        if move is not None:
            return move

        if self.config.use_stockfish_fallback:
            move = self._from_stockfish(board)
            if move is not None:
                return move

        # Fallback: rastgele bir yasal hamle
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            raise ValueError("Oynanacak yasal hamle yok.")
        return legal_moves[0]

    def _from_your_stats(self, board: chess.Board) -> Optional[chess.Move]:
        fen = board.fen()
        moves_stats = self.stats.get(fen)
        if not moves_stats:
            return None
        # İstatistiklerdeki hamleleri yasal hamlelere çevir
        legal_candidates = []
        for san, count in moves_stats.items():
            try:
                move = board.parse_san(san)
            except ValueError:
                continue
            if move not in board.legal_moves:
                continue
            legal_candidates.append((move, count))

        if not legal_candidates:
            return None

        # Stockfish kullanılmıyorsa veya filtre devre dışıysa:
        # tamamen senin en çok oynadığın hamleyi seç.
        if self.engine is None or not self.config.use_stockfish_filter:
            best_move, _ = max(legal_candidates, key=lambda mc: mc[1])
            return best_move

        # Stockfish aktifse: sadece senin en çok oynadığın birkaç hamleyi
        # kısa analizle kontrol edip "facia" olanları ele.
        legal_candidates.sort(key=lambda mc: mc[1], reverse=True)
        to_eval = legal_candidates[: max(1, self.config.max_candidate_moves)]

        scored = []
        our_color = board.turn

        depth = self._stockfish_depth_for_position(board)
        for move, count in to_eval:
            tmp_board = board.copy(stack=False)
            tmp_board.push(move)
            try:
                info = self.engine.analyse(
                    tmp_board,
                    chess.engine.Limit(depth=max(4, depth // 2)),
                )
                score_obj = info.get("score")
                if score_obj is None:
                    continue
                score_cp = score_obj.pov(our_color).score(mate_score=100000)
            except Exception:
                continue

            scored.append((move, count, score_cp))

        # Analiz yapılamadıysa frekansa geri dön
        if not scored:
            best_move, _ = max(legal_candidates, key=lambda mc: mc[1])
            return best_move

        best_eval = max(scored, key=lambda mcs: mcs[2])[2]
        threshold = best_eval - self.config.blunder_threshold_cp

        # En iyi hamleden çok daha kötü olanları ele
        filtered = [mcs for mcs in scored if mcs[2] >= threshold]

        # Hepsi çok kötü ise en iyi eval'lı olanı seç
        if not filtered:
            best_move, _, _ = max(scored, key=lambda mcs: mcs[2])
            return best_move

        # Kalanlar arasından SENİN en çok oynadığın hamleyi seç
        best_move, _, _ = max(filtered, key=lambda mcs: mcs[1])
        return best_move

    def _piece_count(self, board: chess.Board) -> int:
        """Tahtadaki toplam taş sayısı."""
        return sum(1 for _ in board.piece_map().values())

    def _stockfish_depth_for_position(self, board: chess.Board) -> int:
        """Oyun sonunda daha yüksek derinlik kullan."""
        if self._piece_count(board) <= self.config.endgame_piece_count:
            return self.config.stockfish_endgame_depth
        return self.config.stockfish_depth

    def _from_stockfish(self, board: chess.Board) -> Optional[chess.Move]:
        if self.engine is None:
            return None
        depth = self._stockfish_depth_for_position(board)
        try:
            result = self.engine.play(
                board, chess.engine.Limit(depth=depth)
            )
            return result.move
        except Exception:
            return None


def load_engine_from_config(config_path: Path) -> YourStyleEngine:
    """
    JSON config'ten motor oluşturmak için yardımcı fonksiyon.

    Örnek config (config.json):
    {
      "model_path": "data/fen_stats.json",
      "stockfish_path": "/usr/local/bin/stockfish",
      "stockfish_depth": 12
    }
    """
    with config_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    model_path = Path(data["model_path"])
    stockfish_path_str = data.get("stockfish_path")
    stockfish_path = Path(stockfish_path_str) if stockfish_path_str else None
    depth = int(data.get("stockfish_depth", 12))
    endgame_depth = int(data.get("stockfish_endgame_depth", 20))
    endgame_pieces = int(data.get("endgame_piece_count", 10))
    use_stockfish_filter = bool(data.get("use_stockfish_filter", True))
    max_candidate_moves = int(data.get("max_candidate_moves", 3))
    blunder_threshold_cp = int(data.get("blunder_threshold_cp", 200))
    use_stockfish_fallback = bool(data.get("use_stockfish_fallback", True))

    return YourStyleEngine(
        YourStyleEngineConfig(
            model_path=model_path,
            stockfish_path=stockfish_path,
            stockfish_depth=depth,
            stockfish_endgame_depth=endgame_depth,
            endgame_piece_count=endgame_pieces,
            use_stockfish_filter=use_stockfish_filter,
            max_candidate_moves=max_candidate_moves,
            blunder_threshold_cp=blunder_threshold_cp,
            use_stockfish_fallback=use_stockfish_fallback,
        )
    )


if __name__ == "__main__":
    # Küçük lokal test: boş board'da motoru çalıştırmaz ama config yüklemeyi dener.
    cfg_path = Path("config.json")
    if cfg_path.exists():
        engine = load_engine_from_config(cfg_path)
        print("Motor başarıyla yüklendi.")
    else:
        print("config.json bulunamadı, sadece modül olarak kullanın.")

