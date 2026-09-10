import React, { useEffect, useState } from 'react';
import type { DocumentHistory, RegulationDocument } from '../types';
import { fetchDocument, fetchDocumentHistory } from '../lib/api';
import { Blocks } from './Blocks';
import { EditionBanner, HistoryDialog } from './History';

const Toc: React.FC<{ doc: RegulationDocument }> = ({ doc }) => {
  // 条・章までを既定の目次とする。項番まで全部出すと数百行になる。
  const shallow = doc.toc.filter((t) => t.level <= 4);
  const items = shallow.length >= 3 ? shallow : doc.toc;
  if (items.length < 3) return null;
  return (
    <details className="toc" open={items.length <= 40}>
      <summary>目次（{items.length}項目）</summary>
      <ol>
        {items.map((item) => (
          <li key={item.id} className={`l${Math.min(5, item.level)}`}>
            <a href={`#${encodeURIComponent(item.id)}`}>{item.text}</a>
          </li>
        ))}
      </ol>
    </details>
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
    // 履歴は本文より軽く、無くても本文は読めるので失敗しても黙って諦める
    fetchDocumentHistory(docId, controller.signal)
      .then(setHistory)
      .catch(() => undefined);
    return () => controller.abort();
  }, [docId]);

  // 本文を描いたあとで、URL のアンカー位置まで移動する。
  // 図版は縦横比で領域を確保しているが、フォントの適用などで多少ずれる
  // ことがあるので、描画直後ともう一度あとで位置を合わせる。
  useEffect(() => {
    if (!doc || !window.location.hash) return;
    const id = decodeURIComponent(window.location.hash.slice(1));
    const jump = () => document.getElementById(id)?.scrollIntoView({ block: 'start' });
    jump();
    const timer = window.setTimeout(jump, 300);
    return () => window.clearTimeout(timer);
  }, [doc]);

  if (error) {
    return (
      <div className="error">
        規則を読み込めませんでした：{error}
      </div>
    );
  }
  if (!doc) return <div className="loading">読み込み中…</div>;

  return (
    <article>
      <header className="doc-header">
        <div className="breadcrumb">
          {[doc.section, doc.group].filter(Boolean).join(' ／ ')}
        </div>
        <h1>{doc.title}</h1>
        <div className="meta">
          {doc.uploadDate && <span>アップロード日 {doc.uploadDate}</span>}
          <span>{doc.pageCount} ページ</span>
          <span>図版 {doc.stats.figures}</span>
          <span>表 {doc.stats.tables}</span>
          {doc.pdfUrl && (
            <a href={doc.pdfUrl} target="_blank" rel="noreferrer nofollow">
              JAF の原本 PDF を開く ↗
            </a>
          )}
          {history && history.events.length > 0 && (
            <button type="button" className="hist-open" onClick={() => setHistoryOpen(true)}>
              更新履歴
            </button>
          )}
        </div>
      </header>

      {history && <EditionBanner history={history} />}

      {/* 本文を読む面なので、警告の有無に関わらず常に出す。
          汎用の JAF トップではなく「この規則の PDF」へ直接飛ばす。 */}
      <p className="source-note">
        <strong>JAF の公式サイトではありません。</strong>
        JAF が公開する PDF を自動変換した非公式の検索用アーカイブです。
        記載内容は必ず
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
        で出典をご確認ください。
      </p>

      {doc.warnings.length > 0 && (
        <p className="notice">
          この文書には自動変換で完全に読み取れなかったページが {doc.warnings.length} 件あります
          （該当ページは本文中に印を付けています）。
        </p>
      )}

      <Toc doc={doc} />

      <div className="doc-body">
        <Blocks doc={doc} />
      </div>

      <footer className="doc-footer">
        原本: {doc.title}（JAF）／ 自動変換 {doc.convertedAt?.slice(0, 10)} ／
        パイプライン {doc.pipelineVersion}
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
