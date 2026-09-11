import React, { useEffect, useMemo, useState } from 'react';
import type { SearchDocCount, SearchHit, SearchResponse } from '../types';
import { search } from '../lib/api';
import { Link } from './Link';

const PAGE_SIZE = 20;
const HL_START = '\u0001';
const HL_END = '\u0002';

/** サーバは抜粋のハイライトを制御文字で囲んで返す（HTML は返さない）。
 *  ここで React 要素に変換するので、本文に "<" があっても安全。 */
const Snippet: React.FC<{ text: string }> = ({ text }) => {
  const parts: React.ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (true) {
    const start = rest.indexOf(HL_START);
    if (start === -1) break;
    const end = rest.indexOf(HL_END, start + 1);
    if (end === -1) break;
    if (start > 0) parts.push(rest.slice(0, start));
    parts.push(<mark key={key++}>{rest.slice(start + 1, end)}</mark>);
    rest = rest.slice(end + 1);
  }
  parts.push(rest);
  return <p className="snippet">{parts}</p>;
};

/** 「この語がどの規則に散らばっているか」。装備品や車両規定を調べるとき、
 *  この散らばり自体が知りたい情報なので結果の先頭に出す。区分ごとにまとめ、
 *  行を押すとその規則だけに絞り込む。 */
const Spread: React.FC<{
  docs: SearchDocCount[];
  total: number;
  activeDoc: string | null;
  onPick: (docId: string | null) => void;
}> = ({ docs, total, activeDoc, onPick }) => {
  const [open, setOpen] = useState(false);
  const bySection = useMemo(() => {
    const map = new Map<string, SearchDocCount[]>();
    docs.forEach((d) => {
      const key = d.section || 'その他';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(d);
    });
    return [...map.entries()];
  }, [docs]);

  if (docs.length === 0) return null;
  const shown = open || docs.length <= 8 ? bySection : [];

  return (
    <section className="spread">
      <div className="spread-head">
        <strong>
          {docs.length.toLocaleString()} の規則に {total.toLocaleString()} 箇所
        </strong>
        <span className="hint">
          {bySection.length} 区分にまたがっています。PDF を 1 本ずつ開いても追えない部分です。
        </span>
        {docs.length > 8 && (
          <button className="linkish" onClick={() => setOpen(!open)}>
            {open ? '内訳を閉じる' : '内訳を見る'}
          </button>
        )}
      </div>
      {activeDoc && (
        <p className="spread-filter">
          この規則だけを表示しています。
          <button className="linkish" onClick={() => onPick(null)}>
            すべてに戻す
          </button>
        </p>
      )}
      {shown.map(([section, items]) => (
        <div className="spread-group" key={section}>
          <div className="spread-section">{section}</div>
          <ul>
            {items.map((d) => (
              <li key={d.docId}>
                <button
                  className={`spread-doc${d.docId === activeDoc ? ' is-active' : ''}`}
                  onClick={() => onPick(d.docId === activeDoc ? null : d.docId)}
                >
                  <span className="t">{d.title}</span>
                  <span className="n">{d.count}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
};

const Hit: React.FC<{ hit: SearchHit }> = ({ hit }) => {
  const anchor = hit.anchor ? `#${encodeURIComponent(hit.anchor)}` : `#p${hit.page}`;
  return (
    <div className="hit">
      <div className="path">
        {hit.headingPath || hit.heading}
        {` ／ P.${hit.page}`}
      </div>
      <Snippet text={hit.snippet} />
      {hit.figures.length > 0 && (
        <div className="hit-figures">
          {hit.figures.map((f) => (
            <figure key={f.url}>
              <img src={f.url} alt={f.caption || `P.${f.page} の図`} loading="lazy" />
              {f.caption && <figcaption>{f.caption}</figcaption>}
            </figure>
          ))}
        </div>
      )}
      <div className="actions">
        {/* 前後や全文は JAF の原本で読む。該当ページに直行する。 */}
        <a className="primary" href={hit.pdfPageUrl || hit.pdfUrl || '#'} target="_blank" rel="noreferrer nofollow">
          JAF の原本 P.{hit.page} を開く ↗
        </a>
        <Link href={`/doc/${encodeURIComponent(hit.docId)}${anchor}`}>アプリで該当箇所を見る</Link>
      </div>
    </div>
  );
};

export const SearchResults: React.FC<{ query: string }> = ({ query }) => {
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [doc, setDoc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setOffset(0);
    setDoc(null);
  }, [query]);

  useEffect(() => {
    if (!query.trim()) {
      setResult(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    search(query, { limit: PAGE_SIZE, offset, doc: doc ?? undefined }, controller.signal)
      .then(setResult)
      .catch((err) => {
        if (err.name !== 'AbortError') setError(String(err.message || err));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [query, offset, doc]);

  // 表示中のページを規則ごとにまとめる（並び順は関連度のまま）
  const groups = useMemo(() => {
    const out: { docId: string; title: string; section: string; hits: SearchHit[] }[] = [];
    (result?.items ?? []).forEach((hit) => {
      const last = out[out.length - 1];
      if (last && last.docId === hit.docId) {
        last.hits.push(hit);
        return;
      }
      const existing = out.find((g) => g.docId === hit.docId);
      if (existing) {
        existing.hits.push(hit);
        return;
      }
      out.push({ docId: hit.docId, title: hit.title, section: hit.section, hits: [hit] });
    });
    return out;
  }, [result]);

  if (!query.trim()) {
    return <div className="empty">キーワードを入力してください。</div>;
  }
  if (error) return <div className="error">検索に失敗しました：{error}</div>;
  if (!result && loading) return <div className="loading">検索中…</div>;
  if (!result) return null;

  if (result.total === 0) {
    return (
      <>
        <h1 className="page-title">「{query}」の検索結果</h1>
        <div className="empty">
          一致する条文が見つかりませんでした。
          <br />
          言い回しを変えるか、語を短くしてお試しください（例:「安全ベルト」→「ベルト」）。
        </div>
      </>
    );
  }

  const shown = Math.min(result.total, offset + result.items.length);
  return (
    <>
      <h1 className="page-title">「{query}」の検索結果</h1>
      <Spread
        docs={result.byDoc}
        total={result.total}
        activeDoc={doc}
        onPick={(next) => {
          setDoc(next);
          setOffset(0);
        }}
      />
      <p className="page-sub">
        {result.total.toLocaleString()} 件中 {offset + 1}–{shown} 件を表示
      </p>
      {groups.map((g) => (
        <section className="hit-group" key={`${g.docId}-${g.hits[0].page}`}>
          <h2 className="hit-group-head">
            <Link href={`/doc/${encodeURIComponent(g.docId)}`}>{g.title}</Link>
            <span className="sec">{g.section}</span>
          </h2>
          {g.hits.map((hit, i) => (
            <Hit key={`${hit.anchor ?? hit.page}-${i}`} hit={hit} />
          ))}
        </section>
      ))}
      {result.total > PAGE_SIZE && (
        <div className="pager">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            ← 前
          </button>
          <button
            disabled={offset + PAGE_SIZE >= result.total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            次 →
          </button>
          <span className="info">{loading ? '読み込み中…' : `${shown} / ${result.total}`}</span>
        </div>
      )}
    </>
  );
};
