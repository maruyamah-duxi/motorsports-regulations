"""検索結果の抜粋の回帰テスト.

    python tests/test_search.py

位置合わせの細工が入っていて壊れやすい部分だけを単体で見る。
`server/excerpt.py` は FastAPI に依存していないので、pipeline の依存だけを
入れた環境でも走る。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.excerpt import HIGHLIGHT_END, HIGHLIGHT_START, excerpt, norm_map  # noqa: E402

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
