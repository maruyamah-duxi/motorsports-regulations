"""favicon / アプリアイコン / OG カードを作り直す.

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

from PIL import Image, ImageDraw, ImageFont

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

# --- OG カード（SNS でリンクを展開したときの画像）------------------------
#
# 規則ごとに 160 枚作る案は採らなかった。Slack・LINE・X は og:title と
# og:description を画像の横にテキストで出すので、規則名を画像にも焼き込むのは
# 重複になる。そのために週次同期で 160 枚を作り直す手間は釣り合わない。
#
# 文言に件数（160 規則、5,148 ページ）を入れないのも意図的で、規則が増えた
# ときに画像だけ古くなるのを避けている。
OG_W, OG_H = 1200, 630
OG_BG = (250, 251, 252, 255)
OG_FG = (20, 24, 31, 255)
OG_MUTED = (92, 102, 114, 255)
OG_FAINT = (138, 147, 158, 255)

# 日本語が出るフォント。連携先の Linux VM と手元の mac の両方で見つかるよう
# 候補を並べる。太さは揃っていないことがある（実測: 連携先の VM には
# NotoSansCJK の Bold と Regular しか無く、Medium が無い）ので、
# 見つからない太さは近いものに落とす。
_FONT_DIRS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-{weight}.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W{hiragino}.ttc",
)
_HIRAGINO = {"Bold": "6", "Medium": "5", "Regular": "3"}
# 要求した太さが無いときに試す順
_WEIGHT_CHAIN = {
    "Bold": ("Bold", "Black", "Medium", "Regular"),
    "Medium": ("Medium", "Regular", "Bold"),
    "Regular": ("Regular", "DemiLight", "Medium"),
}


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in _WEIGHT_CHAIN[weight]:
        for template in _FONT_DIRS:
            path = Path(
                template.format(
                    weight=candidate, hiragino=_HIRAGINO.get(candidate, "3")
                )
            )
            if path.exists():
                # .ttc の 0 番が JP（NotoSansCJK / ヒラギノ ともに）
                return ImageFont.truetype(str(path), size, index=0)
    raise SystemExit(
        "日本語フォントが見つかりません。OG カードの生成には CJK フォントが"
        f"必要です（探した場所: {list(_FONT_DIRS)}）"
    )


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


def og_card() -> Image.Image:
    """SNS でリンクを展開したときの画像（全規則で共通の 1 枚）."""
    img = Image.new("RGBA", (OG_W, OG_H), OG_BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, OG_W, 10], fill=ACCENT)

    # 右下にチェッカーの模様をうっすら
    layer = Image.new("RGBA", (OG_W, OG_H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    cell = 46
    for r in range(4):
        for c in range(6):
            if (r + c) % 2 == 0:
                x = OG_W - (6 - c) * cell - 40
                y = OG_H - (4 - r) * cell - 36
                ld.rectangle([x, y, x + cell - 1, y + cell - 1], fill=ACCENT[:3] + (18,))
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)

    mark = sized(140)
    img.paste(mark, (72, 158), mark)

    d.text((248, 172), "JAF モータースポーツ諸規則", font=_font("Bold", 62), fill=OG_FG)
    d.text((248, 254), "横断検索（非公式）", font=_font("Bold", 62), fill=ACCENT)
    d.text(
        (72, 424),
        "国内モータースポーツ諸規則を条文単位で横断し、該当条文を探せます",
        font=_font("Medium", 30),
        fill=OG_MUTED,
    )
    d.text(
        (72, 476),
        "本文は JAF の原本 PDF でご確認ください。JAF の公式サイトではありません。",
        font=_font("Regular", 25),
        fill=OG_FAINT,
    )
    return img


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

    # SNS のカード（og:image）。全規則で共通の 1 枚
    og_card().convert("RGB").save(OUT / "og-card.png")

    for path in sorted(OUT.iterdir()):
        print(f"  {path.name:26} {path.stat().st_size:>7,} bytes")


if __name__ == "__main__":
    main()
