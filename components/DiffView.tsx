import React, { useEffect, useMemo, useState } from 'react';
import type { DiffChange, DiffStatus, DocumentDiff } from '../types';
import { fetchDocumentDiff } from '../lib/api';
import { Link } from './Link';

const STATUS_LABEL: Record<DiffStatus, string> = {
  changed: '変更',
  year_only: '年号のみ',
  added: '追加',
  removed: '削除',
};

/** 差分の 1 条。
 *
 *  年号だけの変更にも差分の行は必ず出す。ラベルで畳んでしまうと、
 *  判定を誤ったときに利用者が気づけない。 */
const Change: React.FC<{ change: DiffChange; docId: string }> = ({ change, docId }) => (
  <section className={`diff-clause is-${change.status}`}>
    <header>
      <span className={`diff-tag is-${change.status}`}>{STATUS_LABEL[change.status]}</span>
      <span className="diff-key">{change.key}</span>
      {change.anchor ? (
        <Link className="diff-jump" href={`/doc/${docId}#${encodeURIComponent(change.anchor)}`}>
          {change.heading || '本文を開く'}
        </Link>
      ) : (
        <span className="diff-heading">{change.heading}</span>
      )}
      {change.page != null && <span className="diff-page">P.{change.page}</span>}
    </header>
    <div className="diff-lines">
      {change.lines.map((line, i) => (
        <p key={i} className={`diff-line is-${line.op}`}>
          <span aria-hidden="true">{line.op === 'del' ? '−' : '＋'}</span>
          <span className="sr">{line.op === 'del' ? '削除: ' : '追加: '}</span>
          {line.text}
        </p>
      ))}
    </div>
  </section>
);

export const DiffView: React.FC<{ docId: string; baseDocId: string }> = ({
  docId,
  baseDocId,
}) => {
  const [diff, setDiff] = useState<DocumentDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hideYearOnly, setHideYearOnly] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setDiff(null);
    setError(null);
    fetchDocumentDiff(docId, baseDocId, controller.signal)
      .then(setDiff)
      .catch((err) => {
        if (err.name !== 'AbortError') setError(String(err.message || err));
      });
    return () => controller.abort();
  }, [docId, baseDocId]);

  const shown = useMemo(
    () => (diff ? diff.changes.filter((c) => !hideYearOnly || c.status !== 'year_only') : []),
    [diff, hideYearOnly],
  );

  if (error) return <div className="error">差分を読み込めませんでした：{error}</div>;
  if (!diff) return <div className="loading">読み込み中…</div>;

  const s = diff.summary;
  const substantive = s.changed + s.added + s.removed;

  return (
    <article>
      <header className="doc-header">
        <div className="breadcrumb">改正差分（条単位）</div>
        <h1>{diff.target.title}</h1>
        <div className="meta">
          <span>
            比較元 {diff.base.title}
            {diff.base.uploadDate ? `（${diff.base.uploadDate}）` : ''}
          </span>
          {diff.target.docId && (
            <Link href={`/doc/${diff.target.docId}`}>この規則の本文 →</Link>
          )}
          {diff.base.docId && diff.kind === 'edition' && (
            <Link href={`/doc/${diff.base.docId}`}>比較元の本文 →</Link>
          )}
        </div>
      </header>

      <p className="source-note">
        <strong>この差分は自動生成です。</strong>
        条項番号で新旧を突き合わせた結果で、JAF による正式な改正の告知ではありません。
        自動変換した本文をもとにしているため、変換の誤りが差分として現れることがあります。
        改正の内容は必ず JAF の原本 PDF でご確認ください。
      </p>

      <div className="diff-summary">
        <span className="big">{substantive}</span>
        <span>条に実質的な変更</span>
        <span className="rest">
          変更 {s.changed} ／ 追加 {s.added} ／ 削除 {s.removed} ／ 年号のみ {s.yearOnly} ／
          変更なし {s.unchanged}（全 {s.clauses} 条）
        </span>
      </div>

      {s.yearOnly > 0 && (
        <label className="diff-filter">
          <input
            type="checkbox"
            checked={hideYearOnly}
            onChange={(e) => setHideYearOnly(e.target.checked)}
          />
          年号の表記だけが変わった {s.yearOnly} 条を隠す
        </label>
      )}

      {shown.length === 0 ? (
        <div className="empty">表示する差分がありません。</div>
      ) : (
        shown.map((c) => <Change key={`${c.status}-${c.key}`} change={c} docId={docId} />)
      )}
    </article>
  );
};
