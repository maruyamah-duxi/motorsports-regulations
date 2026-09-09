import React from 'react';
import type { DocumentSummary } from '../types';
import { Link } from './Link';

const SOURCE_LABEL: Record<string, string> = {
  internal: '国内',
  international: '国際',
};

const Card: React.FC<{ doc: DocumentSummary }> = ({ doc }) => (
  <Link className="doc-card" href={`/doc/${encodeURIComponent(doc.docId)}`}>
    <div className="title">{doc.title}</div>
    <div className="meta">
      <span>{SOURCE_LABEL[doc.source] ?? doc.source}</span>
      {doc.group && <span>{doc.group}</span>}
      <span>{doc.pageCount} ページ</span>
      {doc.figures > 0 && <span>図版 {doc.figures}</span>}
      {doc.uploadDate && <span>更新 {doc.uploadDate}</span>}
    </div>
  </Link>
);

export const DocumentList: React.FC<{ documents: DocumentSummary[] }> = ({ documents }) => {
  const recent = [...documents]
    .filter((d) => d.uploadDate)
    .sort((a, b) => (b.uploadDate ?? '').localeCompare(a.uploadDate ?? ''))
    .slice(0, 12);

  const totalPages = documents.reduce((n, d) => n + d.pageCount, 0);

  return (
    <>
      <h1 className="page-title">JAF モータースポーツ諸規則</h1>
      <p className="page-sub">
        JAF が PDF で公開している諸規則 {documents.length} 件（{totalPages.toLocaleString()} ページ）を、
        条文単位で検索できる HTML に変換したものです。図版と表は原本の位置のまま収めています。
        上の検索窓から全文検索できます。
      </p>

      {recent.length > 0 && (
        <>
          <h2 className="page-title" style={{ fontSize: '1rem' }}>
            最近更新された規則
          </h2>
          <p className="page-sub">JAF のアップロード日が新しいものから {recent.length} 件。</p>
          <div className="doc-list">
            {recent.map((doc) => (
              <Card key={doc.docId} doc={doc} />
            ))}
          </div>
        </>
      )}

      <p className="page-sub" style={{ marginTop: '2rem' }}>
        すべての規則は左のカテゴリから辿れます。
      </p>
    </>
  );
};
