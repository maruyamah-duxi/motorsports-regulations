import React, { useMemo } from 'react';
import type { DocumentSummary } from '../types';
import { Link } from './Link';

const SOURCE_LABEL: Record<string, string> = {
  internal: '国内モータースポーツ諸規則',
  international: '国際モータースポーツ諸規則',
};

const SOURCE_SHORT: Record<string, string> = {
  internal: '国内',
  international: '国際',
};

/** 大分類の見出しにつけるアンカー。左のカテゴリからここへ飛ぶ。 */
export function sectionAnchor(source: string, section: string): string {
  return `sec-${source}-${section.replace(/[^0-9A-Za-z一-鿿ぁ-ヿー]+/g, '-')}`;
}

const Card: React.FC<{ doc: DocumentSummary; showSource?: boolean }> = ({ doc, showSource }) => (
  <Link className="doc-card" href={`/doc/${encodeURIComponent(doc.docId)}`}>
    <div className="title">{doc.title}</div>
    <div className="meta">
      {showSource && <span>{SOURCE_SHORT[doc.source] ?? doc.source}</span>}
      {doc.group && <span>{doc.group}</span>}
      <span>{doc.pageCount} ページ</span>
      {doc.figures > 0 && <span>図版 {doc.figures}</span>}
      {doc.tables > 0 && <span>表 {doc.tables}</span>}
      {doc.uploadDate && <span>更新 {doc.uploadDate}</span>}
    </div>
  </Link>
);

interface Grouped {
  source: string;
  sections: { section: string; groups: { group: string; docs: DocumentSummary[] }[]; total: number }[];
}

function groupDocuments(docs: DocumentSummary[]): Grouped[] {
  const bySource = new Map<string, Map<string, Map<string, DocumentSummary[]>>>();
  for (const doc of docs) {
    const sections = bySource.get(doc.source) ?? new Map();
    bySource.set(doc.source, sections);
    const groups = sections.get(doc.section || 'その他') ?? new Map();
    sections.set(doc.section || 'その他', groups);
    const list = groups.get(doc.group || '') ?? [];
    groups.set(doc.group || '', list);
    list.push(doc);
  }
  return [...bySource].map(([source, sections]) => ({
    source,
    sections: [...sections].map(([section, groups]) => ({
      section,
      groups: [...groups].map(([group, docs]) => ({ group, docs })),
      total: [...groups.values()].reduce((n, d) => n + d.length, 0),
    })),
  }));
}

export const DocumentList: React.FC<{ documents: DocumentSummary[] }> = ({ documents }) => {
  const grouped = useMemo(() => groupDocuments(documents), [documents]);
  const recent = useMemo(
    () =>
      [...documents]
        .filter((d) => d.uploadDate)
        .sort((a, b) => (b.uploadDate ?? '').localeCompare(a.uploadDate ?? ''))
        .slice(0, 8),
    [documents],
  );
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
        <section className="home-section">
          <h2>最近更新された規則</h2>
          <p className="page-sub">JAF のアップロード日が新しいものから {recent.length} 件。</p>
          <div className="doc-list">
            {recent.map((doc) => (
              <Card key={doc.docId} doc={doc} showSource />
            ))}
          </div>
        </section>
      )}

      {grouped.map((node) => (
        <section className="home-section" key={node.source}>
          <h2>{SOURCE_LABEL[node.source] ?? node.source}</h2>
          {node.sections.map((sec) => (
            <div key={sec.section} id={sectionAnchor(node.source, sec.section)} className="home-cat">
              <h3>
                {sec.section}
                <span className="count">{sec.total} 件</span>
              </h3>
              {sec.groups.map((g) => (
                <div className="home-group" key={g.group}>
                  {g.group && <h4>{g.group}</h4>}
                  <div className="doc-list">
                    {g.docs.map((doc) => (
                      <Card key={doc.docId} doc={doc} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </section>
      ))}
    </>
  );
};
