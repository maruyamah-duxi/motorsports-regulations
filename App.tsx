import React, { useEffect, useMemo, useState } from 'react';
import type { DocumentsResponse } from './types';
import { fetchDocuments } from './lib/api';
import { navigate, parseRoute, useLocation } from './lib/router';
import { Ask } from './components/Ask';
import { CategoryTree } from './components/CategoryTree';
import { DocumentList } from './components/DocumentList';
import { DocumentView } from './components/DocumentView';
import { DiffView } from './components/DiffView';
import { SearchResults } from './components/SearchResults';
import { Link } from './components/Link';

const SearchBar: React.FC<{ value: string; onSubmit: (q: string) => void }> = ({
  value,
  onSubmit,
}) => {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);

  return (
    <form
      className="searchbar"
      role="search"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(text);
      }}
    >
      <input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="条文を全文検索（例: ロールケージ 溶接、安全ベルト 取付角度）"
        aria-label="条文を全文検索"
      />
      {text && (
        <button
          type="button"
          className="clear"
          aria-label="検索語を消す"
          onClick={() => {
            setText('');
            onSubmit('');
          }}
        >
          ✕
        </button>
      )}
    </form>
  );
};

const App: React.FC = () => {
  const location = useLocation();
  const route = useMemo(() => parseRoute(location), [location]);
  const [docs, setDocs] = useState<DocumentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchDocuments(controller.signal)
      .then(setDocs)
      .catch((err) => {
        if (err.name !== 'AbortError') setError(String(err.message || err));
      });
    return () => controller.abort();
  }, []);

  // サーバは初回レスポンスの <title> を規則ごとに差し替えている
  // （server/seo.py）。ここは SPA 内で移動したときの追従で、表記を
  // サーバ側と揃えておく（末尾の _20260101 は落とす）。
  useEffect(() => {
    const site = 'JAF モータースポーツ諸規則ビューア（非公式）';
    const titleOf = (docId?: string) =>
      (docs?.items.find((d) => d.docId === docId)?.title ?? '規則').replace(/[_-]\d{8}$/, '');
    if (route.name === 'document' && docs) {
      document.title = `${titleOf(route.docId)}｜${site}`;
    } else if (route.name === 'diff' && docs) {
      document.title = `${titleOf(route.docId)} の改正点｜${site}`;
    } else if (route.name === 'ask') {
      document.title = `規則について質問する｜${site}`;
    } else if (route.name === 'search' && route.query) {
      document.title = `「${route.query}」の検索結果｜${site}`;
    } else {
      document.title = 'JAF モータースポーツ諸規則を全文検索｜非公式ビューア';
    }
  }, [route, docs]);

  const onSearch = (q: string) => {
    navigate(q.trim() ? `/search?q=${encodeURIComponent(q.trim())}` : '/');
    window.scrollTo({ top: 0 });
  };

  const totalPages = docs?.items.reduce((n, d) => n + d.pageCount, 0) ?? 0;

  return (
    <>
      <header className="header">
        <Link className="brand" href="/">
          <strong>JAF モータースポーツ諸規則</strong>
          <span>非公式ビューア</span>
        </Link>
        <SearchBar value={route.name === 'search' ? (route.query ?? '') : ''} onSubmit={onSearch} />
        <Link className={`nav-ask${route.name === 'ask' ? ' is-active' : ''}`} href="/ask">
          AI に質問
        </Link>
        {docs && (
          <div className="stats">
            {docs.count} 規則 / {totalPages.toLocaleString()} ページ
          </div>
        )}
      </header>

      <div className="layout">
        {docs ? (
          <CategoryTree documents={docs.items} activeDocId={route.docId} />
        ) : (
          <div className="sidebar" />
        )}

        <main>
          {error && <div className="error">データを読み込めませんでした：{error}</div>}
          {!error && !docs && <div className="loading">読み込み中…</div>}
          {docs && route.name === 'home' && <DocumentList documents={docs.items} />}
          {docs && route.name === 'search' && <SearchResults query={route.query ?? ''} />}
          {route.name === 'ask' && <Ask initialQuestion={route.query} />}
          {route.name === 'document' && route.docId && <DocumentView docId={route.docId} />}
          {route.name === 'diff' && route.docId && route.baseDocId && (
            <DiffView docId={route.docId} baseDocId={route.baseDocId} />
          )}
        </main>
      </div>

      <footer className="site-footer">
        <strong>これは JAF の公式サイトではありません。</strong>
        JAF が公開する諸規則 PDF を検索できるようにした非公式のサービスです。
        <br />
        本文は自動変換したもので、誤りが含まれる可能性があります。
        競技における判断は必ず
        <a
          href="https://motorsports.jaf.or.jp/regulations/information"
          target="_blank"
          rel="noreferrer nofollow"
        >
          JAF の原本
        </a>
        をご確認ください。
        {docs?.builtAt && <> ／ データ生成 {docs.builtAt.slice(0, 10)}</>}
      </footer>
    </>
  );
};

export default App;
