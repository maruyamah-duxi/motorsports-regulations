"""参照をたどる仕組みの回帰テスト.

    python tests/test_crossref.py

`server/crossref.py` は FastAPI に依存しないので、pipeline の依存だけの
環境でも走る。実データの構造（同じ条番号が章ごとに繰り返す、折り返した
本文が見出しとして混ざる）を写したフィクスチャで確かめる。
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.crossref import find_references, follow, title_keys  # noqa: E402

# 第2編ラリー車両規定 5.2 の実際の文面（抜粋）
REF_TEXT = (
    "5.2 )RJ車両は、JAF国内競技車両規則第1編レース車両規定第4章公認車両および"
    "登録車両に関する安全規定に従ったロールケージを装着し、かつ運転席および"
    "助手席側に左右対称に構成されたドアバーの装着が義務付けられる。"
    "第1編レース車両規定第4章6.3.2.1.4)については適用せず、推奨とする。"
    # 実際の 5.2 はこのあとで第6条を名指しする。ここが追う手がかりになる
    "また、JAF国内競技車両規則第1編レース車両規定第4章公認車両および"
    "登録車両に関する安全規定第6条による。"
)


def _db(tmp: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp / "t.db")
    con.executescript(
        """
        CREATE TABLE docs (doc_id TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id TEXT, heading TEXT,
                             text TEXT, text_norm TEXT);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(text_norm, content='chunks',
                             content_rowid='id', tokenize='trigram');
        """
    )
    con.execute("INSERT INTO docs VALUES ('v1','第1編レース車両規定_20260101')")
    con.execute("INSERT INTO docs VALUES ('v2','第2編ラリー車両規定_20260101')")
    rows = [
        # 別の章の 6.1。条の先頭としてこれを選んではいけない
        (10, "v1", "6.1 ）ホイール", "6.1 )ホイール ホイールは自由。"),
        # 第4章 第6条 ロールケージ。ここが正解
        (20, "v1", "6.1 ）全　般", "6.1 )全 般 ロールケージの取り付けが義務付けられる。"
                                   "a)6.2項以降の条項に記された要件に従い製作されたもの"
                                   "b)JAFまたは他のASNが公認あるいは認証したもの"),
        (21, "v1", "6.3.1 ）基本構造", "6.3.1 )基本構造 メインロールバー1本+フロントロールバー1本"),
        # 折り返した本文が見出しとして混ざったもの。選んではいけない
        (22, "v1", "第253条4に合致しなければならない。マスターシリンダー",
             "第253条4に合致しなければならない。マスターシリンダー ロールバー"),
        (30, "v2", "5.2 ）ロールケージ", REF_TEXT),
    ]
    con.executemany(
        "INSERT INTO chunks (id, doc_id, heading, text, text_norm) VALUES (?,?,?,?,?)",
        [(i, d, h, t, t) for i, d, h, t in rows],
    )
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    con.commit()
    return con


def test_finds_the_referenced_document_and_article() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        con = _db(Path(tmp))
        refs = find_references(REF_TEXT, title_keys(con))
        labels = [r.label for r in refs]
        assert any(
            r.doc_key == "第1編レース車両規定" and r.prefix == "6" for r in refs
        ), labels
        # 点付き番号も拾う
        assert any(r.prefix == "6.3.2.1.4" for r in refs), labels


def test_ignores_articles_with_no_document_nearby() -> None:
    """規則名が近くに無い条番号は追わない（同じ文書内の参照は一段目で拾える）."""
    refs = find_references("第12条の規定によること。", {"第1編レース車両規定": ["v1"]})
    assert refs == []


def test_follow_lands_in_the_right_chapter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        con = _db(Path(tmp))
        got = follow(con, [REF_TEXT], '"ロールバー" OR "ロールケージ"', exclude={30})
        ids = [cid for cid, _ in got]
        assert ids, "参照先を足せていない"
        # 第4章の 6.1）全般（条の入口）が入る
        assert 20 in ids, ids
        # 別の章の 6.1）ホイールは入らない
        assert 10 not in ids, ids
        # 折り返した本文を見出しと誤検出したものは入らない
        assert 22 not in ids, ids
        # どの参照から来たかが分かる
        assert all("第1編レース車両規定" in label for _, label in got), got


def test_follow_needs_a_query() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        con = _db(Path(tmp))
        assert follow(con, [REF_TEXT], "", exclude=set()) == []


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
