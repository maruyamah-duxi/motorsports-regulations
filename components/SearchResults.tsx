import React, { useEffect, useState } from 'react';
import type { SearchHit, SearchResponse } from '../types';
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

const Hit: React.FC<{ hit: SearchHit }> = ({ hit }) => {
  const anchor = hit.anchor ? `#${encodeURIComponent(hit.anchor)}` : `#p${hit.page}`;
  return (
    <div className="hit">
      <div className="path">
        <Link className="doc" href={`/doc/${encodeURIComponent(hit.docId)}${anchor}`}>
          {hit.title}
        </Link>
        {hit.headingPath && <> ／ {hit.headingPath}</>}
        {` ／ P.${hit.page}`}
      </div>
      <Snippet text={hit.snippet} />
      <div className="actions">
        <Link href={`/doc/${encodeURIComponent(hit.docId)}${anchor}`}>該当箇所を開く</Link>
        {hit.pdfUrl && (
          <a href={hit.pdfUrl} target="_blank" rel="noreferrer nofollow">
            原本 PDF ↗
          </a>
        )}
      </div>
    </div>
  );
};

export const SearchResults: React.FC<{ query: string }> = ({ query }) => {
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => setOffset(0), [query]);

  useEffect(() => {
    if (!query.trim()) {
      setResult(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    search(query, { limit: PAGE_SIZE, offset }, controller.signal)
      .then(setResult)
      .catch((err) => {
        if (err.name !== 'AbortError') setError(String(err.message || err));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [query, offset]);

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
      <p className="page-sub">
        {result.total.toLocaleString()} 件中 {offset + 1}–{shown} 件を表示
      </p>
      {result.items.map((hit, i) => (
        <Hit key={`${hit.docId}-${hit.anchor ?? hit.page}-${i}`} hit={hit} />
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
