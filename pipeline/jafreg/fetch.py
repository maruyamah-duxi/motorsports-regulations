"""JAF サイトからの取得層.

重要な前提（実測済み）:
  * PDF のレスポンスヘッダには **ETag も Last-Modified も無い**
    （`cache-control: no-cache, no-store` のみ）。条件付き GET は使えない。
  * したがって更新検知は
      (1) 一覧ページの「アップロード日」の変化
      (2) ダウンロードした本体の SHA-256 の変化
    の 2 段構えで行う。(1) で拾えない差し替えを (2) が拾う。
  * robots.txt は 404（明示的な禁止は無い）。それでも 1 リクエストずつ
    間隔を空け、User-Agent に連絡先を入れて礼儀正しく巡回する。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_UA = (
    "jp-motorsports-regulations-bot/0.1 "
    "(+https://jp.motorsports-regulations.org; JAF諸規則のHTML化アーカイブ)"
)


@dataclass
class FetchResult:
    path: Path
    sha256: str
    bytes: int
    from_cache: bool


class JafClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        user_agent: str = DEFAULT_UA,
        delay_sec: float = 1.5,
        timeout: float = 60.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay_sec = delay_sec
        self._last_request = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "ja,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
        )

    def _throttle(self) -> None:
        wait = self.delay_sec - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get_text(self, url: str) -> str:
        self._throttle()
        r = self._client.get(url)
        r.raise_for_status()
        return r.text

    def download_pdf(self, url: str, doc_id: str) -> FetchResult:
        """PDF を取得し SHA-256 を計算する.

        キャッシュに同じ内容があれば再ダウンロードはするが（条件付き GET が
        使えないため）、ハッシュが一致すれば `from_cache=True` を返し、
        後続の変換をスキップできるようにする。
        """
        self._throttle()
        dest = self.cache_dir / f"{doc_id}.pdf"
        previous = _sha256_of(dest) if dest.exists() else None

        with self._client.stream("GET", url) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            tmp = dest.with_suffix(".pdf.part")
            h = hashlib.sha256()
            size = 0
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(65536):
                    h.update(chunk)
                    size += len(chunk)
                    f.write(chunk)

        digest = h.hexdigest()
        head = tmp.open("rb").read(5)
        if not head.startswith(b"%PDF") and "pdf" not in ctype.lower():
            tmp.unlink(missing_ok=True)
            raise ValueError(f"PDF ではないレスポンス: {url} (content-type={ctype!r})")

        tmp.replace(dest)
        return FetchResult(path=dest, sha256=digest, bytes=size, from_cache=(digest == previous))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JafClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
