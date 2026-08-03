"""bronze 層への書き出し（原則1 ── 生データは不変）。

JSON Lines を zstd で固める。ここに書いたものは以後**一切変更しない**。
パーサや判定ロジックにバグが見つかっても bronze は触らず、silver 以降を
再生成する。これにより、1年後にフィンガープリント辞書を拾充したとき、
過去12か月分を遡って再判定できる。

追記のみ。既存ファイルを開き直して書き換える経路は作らない。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType

import zstandard

from ..contracts import RawResponse

#: 1ファイルあたりの行数。大きすぎると部分読みができず、小さすぎると
#: ファイル数が爆発する。30,000ドメイン × 約20クエリで数十ファイルになる目安
DEFAULT_PART_SIZE = 50_000


class BronzeWriter:
    """method ごとにパーティションを分けて JSONL.zst を書く。

        bronze/method=zdns/part-0000.jsonl.zst
        bronze/method=dnspython/part-0000.jsonl.zst

    手法ごとに分けるのは、同一対象を複数手法で引いて差分を出すという
    要件のためである（DESIGN.md P4「バックエンドの抽象化」）。
    """

    def __init__(
        self,
        bronze_dir: Path,
        method: str,
        *,
        part_size: int = DEFAULT_PART_SIZE,
        level: int = 10,
    ) -> None:
        self.dir = Path(bronze_dir) / f"method={method}"
        self.method = method
        self.part_size = part_size
        self.level = level
        self.records = 0
        self._part = 0
        self._in_part = 0
        self._fh = None
        self._writer = None
        self.parts: list[dict[str, object]] = []
        #: この実行の前から存在していたパート。再実行の検出に使う
        self.preexisting_parts: list[str] = []

    def __enter__(self) -> BronzeWriter:
        self.dir.mkdir(parents=True, exist_ok=True)
        # 既存のパートには触らず、その次の番号から書く。
        # bronze は「不変・追記のみ」（DESIGN.md 5.3）なので、再実行時に
        # 上書きするのではなく足す。原則1 と 原則6 の両立はこれで取る。
        existing = sorted(self.dir.glob("part-*.jsonl.zst"))
        self.preexisting_parts = [p.name for p in existing]
        if existing:
            last = max(int(p.stem.split("-")[1].split(".")[0]) for p in existing)
            self._part = last + 1
        self._open_part()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._close_part()
        return False

    def _part_path(self) -> Path:
        return self.dir / f"part-{self._part:04d}.jsonl.zst"

    def _open_part(self) -> None:
        path = self._part_path()
        if path.exists():
            # 原則1。既存の bronze を上書きしない
            raise FileExistsError(
                f"{path} が既に存在します。bronze は不変です。"
                "再計測するなら別の run_id を使ってください"
            )
        self._fh = path.open("wb")
        self._writer = zstandard.ZstdCompressor(level=self.level).stream_writer(self._fh)
        self._in_part = 0

    def _close_part(self) -> None:
        if self._writer is None:
            return
        self._writer.flush(zstandard.FLUSH_FRAME)
        self._writer.close()
        assert self._fh is not None
        self._fh.close()
        path = self._part_path()
        self.parts.append(
            {
                "path": f"method={self.method}/{path.name}",
                "records": self._in_part,
                "bytes": path.stat().st_size if path.exists() else 0,
            }
        )
        self._writer = None
        self._fh = None

    def write(self, response: RawResponse) -> None:
        if self._writer is None:
            raise RuntimeError("BronzeWriter は with 文の中で使ってください")
        if self._in_part >= self.part_size:
            self._close_part()
            self._part += 1
            self._open_part()
        line = json.dumps(response.model_dump(mode="json"), ensure_ascii=False, default=str)
        self._writer.write((line + "\n").encode("utf-8"))
        self.records += 1
        self._in_part += 1


def read_bronze(path: Path) -> list[dict]:
    """bronze を読み戻す。P5 がこれを使う。"""
    out: list[dict] = []
    with Path(path).open("rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh)
        buffered = b""
        while True:
            chunk = reader.read(1 << 16)
            if not chunk:
                break
            buffered += chunk
            *lines, buffered = buffered.split(b"\n")
            for line in lines:
                if line.strip():
                    out.append(json.loads(line))
        if buffered.strip():
            out.append(json.loads(buffered))
    return out


def iter_bronze_files(bronze_dir: Path) -> list[Path]:
    root = Path(bronze_dir)
    if not root.is_dir():
        return []
    return sorted(root.glob("method=*/part-*.jsonl.zst"))
