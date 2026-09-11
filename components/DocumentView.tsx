import React, { useEffect, useMemo, useState } from 'react';
import type { DocumentHistory, RegulationDocument, SearchResponse } from '../types';
import { fetchDocument, fetchDocumentHistory, search } from '../lib/api';
import { DiffBanner, EditionBanner, HistoryDialog } from './History';
import { Notices } from './Notices';
import { Hit } from './SearchResults';

/** 目次。原本の該当ページへ送る。
 *
 *  全文を配信しないので、この画面の役目は「どの条がどのページにあるか」を
 *  示して原本へ渡すこと。項番まで全部出すと数百行になるので、既定では
 *  条・章までにする。 */
const Toc: React.FC<{ doc: RegulationDocument }> = ({ doc }) => {
  const [deep, setDeep] = useState(false);
  const shallow = doc.toc.filter((t) => t.level <= 4);
  const items = deep || shallow.length < 3 ? doc.toc : shallow;

  if (doc.toc.length === 0) {
    return (
      <p className="empty-toc">
        この規則は見出しを自動で読み取れませんでした。上の検索か、原本 PDF をご覧ください。
      </p>
    );
  }

  return (
    <section className="toc-panel">
      <div className="toc-head">
        <h2>条文の一覧</h2>
        <span className="hint">見出しを押すと原本 PDF の該当ページが開きます</span>
        {doc.toc.length > shallow.length && (
          <button className="linkish" onClick={() => setDeep(!deep)}>
            {deep ? `条・章だけにする（${shallow.length}）` : `項番まで出す（${doc.toc.length}）`}
          </button>
        )}
      </div>
      <ol className="toc-list">
        {items.map((item, i) => (
          <li key={`${item.page}-${i}`} className={`l${Math.min(5, item.level)}`}>
            {doc.pdfUrl && item.page ? (
              <a
                href={`${doc.pdfUrl}#page=${item.page}`}
                target="_blank"
                rel="noreferrer nofollow"
              >
                <span className="t">{item.text}</span>
                <span className="p">P.{item.page}</span>
              </a>
            ) : (
              <span className="t">{item.text}</span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
};

/** この規則の中だけを検索する。全文を読ませない代わりの主役。 */
const InDocumentSearch: React.FC<{ doc: RegulationDocument }> = ({ doc }) => {
  const [input, setInput] = useState('');
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setInput('');
    setQuery('');
    setResult(null);
  }, [doc.docId]);

  useEffect(() => {
    if (!query.trim()) {
      setResult(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    search(query, { limit: 20, doc: doc.docId }, controller.signal)
      .then(setResult)
      .catch((err) => {
        if (err.name !== 'AbortError') setError(String(err.message || err));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [query, doc.docId]);

  return (
    <section className="in-doc-search">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(input);
        }}
      >
        <label htmlFor="in-doc-q">この規則の中を検索</label>
        <div className="row">
          <input
            id="in-doc-q"
            type="search"
            value={input}
            placeholder="例: 安全ベルト、ロールケージ、第12条"
            onChange={(e) => setInput(e.target.value)}
          />
          <button type="submit">検索</button>
        </div>
      </form>
      {loading && <div className="loading">検索中…</div>}
      {error && <div className="error">検索に失敗しました：{error}</div>}
      {result && result.total === 0 && (
        <div className="empty">この規則には一致する条文がありませんでした。</div>
      )}
      {result && result.total > 0 && (
        <>
          <p className="page-sub">
            {result.total.toLocaleString()} 箇所
            {result.total > result.items.length && `（上位 ${result.items.length} 件を表示）`}
          </p>
          {result.items.map((hit, i) => (
            <Hit key={`${hit.anchor ?? hit.page}-${i}`} hit={hit} />
          ))}
        </>
      )}
    </section>
  );
};

export const DocumentView: React.FC<{ docId: string }> = ({ docId }) => {
  const [doc, setDoc] = useState<RegulationDocument | null>(null);
  const [history, setHistory] = useState<DocumentHistory | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDoc(null);
    setHistory(null);
    setHistoryOpen(false); // 別年度版へ移動したときにポップアップを残さない
    setError(null);
    fetchDocument(docId, controller.signal)
      .then(setDoc)
      .catch((err) => {
        if (err.name !== 'AbortError') setError(String(err.message || err));
      });
    // 履歴は無くてもこの画面は成り立つので、失敗しても黙って諦める
    fetchDocumentHistory(docId, controller.signal)
      .then(setHistory)
      .catch(() => undefined);
    return () => controller.abort();
  }, [docId]);

  const pdfLabel = useMemo(
    () => (doc?.pageCount ? `JAF の原本 PDF を開く（${doc.pageCount} ページ）↗` : 'JAF の原本 PDF を開く ↗'),
    [doc],
  );

  if (error) {
    return <div className="error">規則を読み込めませんでした：{error}</div>;
  }
  if (!doc) return <div className="loading">読み込み中…</div>;

  return (
    <article>
      <header className="doc-header">
        <div className="breadcrumb">{[doc.section, doc.group].filter(Boolean).join(' ／ ')}</div>
        <h1>{doc.title}</h1>
        <div className="meta">
          {doc.uploadDate && <span>アップロード日 {doc.uploadDate}</span>}
          <span>{doc.pageCount} ページ</span>
          {doc.figures > 0 && <span>図版 {doc.figures}</span>}
          {doc.tables > 0 && <span>表 {doc.tables}</span>}
          {history && history.events.length > 0 && (
            <button type="button" className="hist-open" onClick={() => setHistoryOpen(true)}>
              更新履歴
            </button>
          )}
        </div>
      </header>

      {history && <DiffBanner history={history} />}
      {history && <EditionBanner history={history} />}

      {/* 全文を載せない理由と、どこを見ればよいかを最初に伝える */}
      <p className="source-note">
        <strong>JAF の公式サイトではありません。</strong>
        条文の本文は掲載していません（JAF の
        <a
          href="https://jaf.or.jp/common/websitepolicy"
          target="_blank"
          rel="noreferrer nofollow"
        >
          サイトポリシー
        </a>
        に沿い、資料の再配布を行っていません）。このページは
        <strong>どの条がどのページにあるかを探すためのもの</strong>です。本文は
        {doc.pdfUrl ? (
          <a href={doc.pdfUrl} target="_blank" rel="noreferrer nofollow">
            JAF の原本 PDF
          </a>
        ) : (
          <a
            href="https://motorsports.jaf.or.jp/regulations/information"
            target="_blank"
            rel="noreferrer nofollow"
          >
            JAF のサイト
          </a>
        )}
        でご確認ください。
      </p>

      {doc.pdfUrl && (
        <p className="pdf-cta">
          <a href={doc.pdfUrl} target="_blank" rel="noreferrer nofollow">
            {pdfLabel}
          </a>
        </p>
      )}

      <InDocumentSearch doc={doc} />

      <Toc doc={doc} />

      {history && <Notices notices={history.announcements} />}

      <footer className="doc-footer">
        原本: {doc.title}（一般社団法人日本自動車連盟）
        {doc.uploadDate && ` ／ JAF 掲載日 ${doc.uploadDate}`}
      </footer>

      {history && (
        <HistoryDialog
          history={history}
          title={doc.title}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </article>
  );
};
