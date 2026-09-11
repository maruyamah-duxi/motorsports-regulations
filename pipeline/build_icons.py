"""favicon / アプリアイコンを作り直す.

    python pipeline/build_icons.py

正本は `public/favicon.svg`（虫めがねのレンズにチェッカーフラッグ）。
ここでは**同じ図形を PIL で描き直して** PNG と ICO を作る。SVG を
ラスタライズしないのは、そのためだけに cairo 系の依存を増やしたくない
からで、座標は SVG と 1 対 1 で対応させてある（下の定数）。
どちらかを直したら、もう一方も直すこと。

色はアプリの `--accent`（styles.css）と揃えている。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "public"

ACCENT = (31, 79, 216, 255)  # styles.css の --accent #1f4fd8
WHITE = (255, 255, 255, 255)

# --- favicon.svg と同じ座標（viewBox 0 0 512 512）-------------------------
S = 512
CORNER = 112  # rect rx
CX, CY = 222, 218  # circle cx / cy
R_OUT, RING = 132, 36  # 枠の外半径と太さ（SVG は r=114 + stroke-width=36）
HANDLE_END = (404, 400)  # line x2 / y2
HANDLE_W = 58  # line stroke-width
CELL = 62  # レンズ内のチェッカー 1 マス


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=CORNER, fill=ACCENT)

    # 柄。SVG の stroke-linecap="round" にあたる丸を両端に置く
    k = int(R_OUT * 0.707)
    start = (CX + k, CY + k)
    d.line([start, HANDLE_END], fill=WHITE, width=HANDLE_W)
    cap = HANDLE_W // 2
    for x, y in (start, HANDLE_END):
        d.ellipse([x - cap, y - cap, x + cap, y + cap], fill=WHITE)

    # レンズの枠
    d.ellipse([CX - R_OUT, CY - R_OUT, CX + R_OUT, CY + R_OUT], fill=WHITE)
    inner = R_OUT - RING
    d.ellipse([CX - inner, CY - inner, CX + inner, CY + inner], fill=ACCENT)

    # レンズの中のチェッカー（白 2 マスが対角）
    d.rectangle([CX - CELL, CY - CELL, CX - 1, CY - 1], fill=WHITE)
    d.rectangle([CX, CY, CX + CELL - 1, CY + CELL - 1], fill=WHITE)
    return img


def sized(size: int) -> Image.Image:
    return draw().resize((size, size), Image.LANCZOS)


def opaque(img: Image.Image) -> Image.Image:
    """角丸の外を下地色で埋める。透過を残すと iOS で黒い角が出る."""
    bg = Image.new("RGBA", img.size, ACCENT)
    bg.alpha_composite(img)
    return bg


def main() -> None:
    OUT.mkdir(exist_ok=True)

    # 古いブラウザとブックマーク用。16/32/48 を 1 ファイルに入れる
    sized(256).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])

    # Android / PWA
    sized(192).save(OUT / "icon-192.png")
    sized(512).save(OUT / "icon-512.png")

    # iOS のホーム画面。角丸は OS が付けるので四角のまま、透過なしで出す
    opaque(sized(180)).save(OUT / "apple-touch-icon.png")

    # maskable は安全域（内側 80%）に収める。外側は下地色で埋める
    canvas = Image.new("RGBA", (S, S), ACCENT)
    canvas.alpha_composite(sized(410), (51, 51))
    canvas.save(OUT / "icon-maskable-512.png")

    for path in sorted(OUT.iterdir()):
        print(f"  {path.name:26} {path.stat().st_size:>7,} bytes")


if __name__ == "__main__":
    main()
