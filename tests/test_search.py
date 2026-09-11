"""検索結果の抜粋の回帰テスト.

    python tests/test_search.py

位置合わせの細工が入っていて壊れやすい部分だけを単体で見る。
`server/excerpt.py` は FastAPI に依存していないので、pipeline の依存だけを
入れた環境でも走る。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.excerpt import (  # noqa: E402
    HIGHLIGHT_END,
    HIGHLIGHT_START,
    clause_at,
    excerpt,
    first_match,
    norm_map,
)

S, E = HIGHLIGHT_START, HIGHLIGHT_END


def test_marks_every_occurrence() -> None:
    text = "安全ベルトは、ＦＩＡ公認の安全ベルトを使うこと。"
    out = excerpt(text, ["安全ベルト"])
    assert out == f"{S}安全ベルト{E}は、ＦＩＡ公認の{S}安全ベルト{E}を使うこと。"
    # 原文の表記を変えない（ＦＩＡ を fia にしてはいけない。過去の事故）
    assert "ＦＩＡ" in out


def test_zenkaku_original_is_preserved() -> None:
    """全角の原文を半角のクエリで引いても、返すのは原文の表記."""
    text = "登録料は１０６,７００円とする。"
    out = excerpt(text, ["106,700"])
    assert out == f"登録料は{S}１０６,７００{E}円とする。"


def test_window_and_ellipsis() -> None:
    text = "あ" * 400 + "安全ベルト" + "い" * 400
    out = excerpt(text, ["安全ベルト"], before=10, after=20)
    assert out.startswith("… ")
    assert out.endswith(" …")
    assert f"{S}安全ベルト{E}" in out
    # 前 10 字 + 語 + 後ろの残り、で窓に収まっている
    body = out.removeprefix("… ").removesuffix(" …").replace(S, "").replace(E, "")
    assert len(body) == 30, len(body)


def test_no_ellipsis_when_whole_text_fits() -> None:
    out = excerpt("第1条 目的", ["目的"])
    assert "…" not in out


def test_falls_back_to_head_when_not_found() -> None:
    text = "あ" * 500
    out = excerpt(text, ["見つからない語"], before=10, after=20)
    assert out == "あ" * 30 + " …"
    assert S not in out


def test_norm_map_handles_expanding_characters() -> None:
    """NFKC で 1 文字が複数文字になる場合でも、原文の位置に戻せること."""
    text = "㍿安全ベルト"  # ㍿ は NFKC で「株式会社」に開く
    normalized, back = norm_map(text)
    assert normalized == "株式会社安全ベルト"
    assert len(back) == len(normalized)
    # 「安全ベルト」は正規化後 4 文字目から。原文では 1 文字目
    assert back[4] == 1
    out = excerpt(text, ["安全ベルト"])
    assert out == f"㍿{S}安全ベルト{E}"


def test_empty_inputs() -> None:
    assert excerpt("", ["x"]) == ""
    assert excerpt("本文", []) == "本文"
    assert excerpt("本文", [""]) == "本文"


# 付則J項の実データを写したもの。見出しの検出が効かず、1 つの見出しが
# 20 ページ分の本文を抱えているチャンク（part=5、P.41-60）。
APX_J = (
    "3.3)\u3000自動燃料遮断装置すべてのグループについて推奨：\n"
    "ロールバーメインロールバーと同様なものであるが、その形状は…\n"
    "8.2.4)\u3000サイドロールバーコクピットの左右に沿って配置された、ほぼ縦方向…\n"
    "8.2.5)\u3000ハーフ・サイドロールバーリアピラーのないサイドロールバーに等しい。\n"
)
HEAD = "3.3)\u3000自動燃料遮断装置すべてのグループについて推奨："


def test_first_match_offset() -> None:
    text = "あ" * 50 + "安全ベルト" + "い" * 50
    assert first_match(text, ["安全ベルト"]) == 50
    # 全角の原文を半角で引いても、返るのは原文側の位置
    assert first_match("料金は１０６,７００円", ["106,700"]) == 3
    assert first_match("本文", ["無い語"]) is None


def test_clause_at_finds_the_real_clause() -> None:
    """引き継いだ見出しではなく、一致箇所の手前にある条項を返すこと."""
    i = APX_J.index("サイドロールバーコクピット")
    got = clause_at(APX_J, i, HEAD)
    assert got is not None and got.startswith("8.2.4)"), got
    i = APX_J.index("ハーフ・サイド")
    got = clause_at(APX_J, i, HEAD)
    assert got is not None and got.startswith("8.2.5)"), got


def test_clause_at_skips_the_inherited_heading() -> None:
    """先頭行は引き継いだ見出し。これを拾うと間違いをそのまま出すことになる."""
    i = APX_J.index("ロールバーメインロールバー")
    assert clause_at(APX_J, i, HEAD) is None


def test_clause_at_rejects_false_positives() -> None:
    # 目印の直後に空白が無いもの（文の途中で折り返した行）
    assert clause_at("9)のうちの1つを選ぶ", 5) is None
    assert clause_at("第253条4に合致しなければならない。", 5) is None
    # 表を平坦化した行（セルを " | " で繋いだもの）
    assert clause_at("031) | オリジナル車両\nロールバー", 25) is None
    # 正しい形はちゃんと拾う
    got = clause_at("第12条　安全ベルト\n肩部ストラップは…", 12)
    assert got is not None and got.startswith("第12条"), got


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
