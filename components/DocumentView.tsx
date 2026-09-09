import React, { useEffect, useState } from 'react';
import type { RegulationDocument } from '../types';
import { fetchDocument } from '../lib/api';
import { Blocks } from './Blocks';

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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setDoc(null);
    setError(null);
    fetchDocument(docId, controller.signal)
      .then(setDoc)
      .catch((err) => {
        if (err.name !== 'AbortError') setError(String(err.message || err));
      });
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
        </div>
      </header>

      {doc.warnings.length > 0 && (
        <p className="notice">
          この文書には自動変換で完全に読み取れなかったページが {doc.warnings.length} 件あります。
          正式な判断は必ず JAF の原本 PDF をご確認ください。
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
    </article>
  );
};
