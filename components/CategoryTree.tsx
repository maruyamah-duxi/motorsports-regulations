import React, { useMemo, useState } from 'react';
import type { DocumentSummary } from '../types';
import { Link } from './Link';

const SOURCE_LABEL: Record<string, string> = {
  internal: '国内モータースポーツ諸規則',
  international: '国際モータースポーツ諸規則',
};

interface Node {
  source: string;
  sections: { section: string; groups: { group: string; docs: DocumentSummary[] }[]; total: number }[];
}

function buildTree(docs: DocumentSummary[]): Node[] {
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

interface Props {
  documents: DocumentSummary[];
  activeDocId?: string;
}

export const CategoryTree: React.FC<Props> = ({ documents, activeDocId }) => {
  const tree = useMemo(() => buildTree(documents), [documents]);
  const activeSection = useMemo(
    () => documents.find((d) => d.docId === activeDocId)?.section,
    [documents, activeDocId],
  );
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const isOpen = (key: string, section: string) =>
    open[key] ?? (activeSection ? section === activeSection : false);

  return (
    <nav className="sidebar" aria-label="規則のカテゴリ">
      <h2>カテゴリ</h2>
      {tree.map((node) => (
        <div className="tree-source" key={node.source}>
          <div className="label">{SOURCE_LABEL[node.source] ?? node.source}</div>
          {node.sections.map((sec) => {
            const key = `${node.source}/${sec.section}`;
            const opened = isOpen(key, sec.section);
            return (
              <div className={`tree-section${opened ? ' is-open' : ''}`} key={key}>
                <button
                  aria-expanded={opened}
                  onClick={() => setOpen({ ...open, [key]: !opened })}
                >
                  <span aria-hidden="true">{opened ? '▾' : '▸'}</span>
                  <span>{sec.section}</span>
                  <span className="count">{sec.total}</span>
                </button>
                {opened && (
                  <div className="tree-group">
                    {sec.groups.map((g) => (
                      <div key={g.group}>
                        {g.group && <div className="label">{g.group}</div>}
                        {g.docs.map((doc) => (
                          <Link
                            key={doc.docId}
                            href={`/doc/${encodeURIComponent(doc.docId)}`}
                            className={doc.docId === activeDocId ? 'is-active' : undefined}
                          >
                            {doc.title}
                          </Link>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </nav>
  );
};
