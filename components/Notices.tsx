import React, { useState } from 'react';
import type { Notice } from '../types';

/** JAF の公示へ案内する。
 *
 *  対比表（新旧対照表）は **JAF 自身が新旧を並べたもの**で、こちらの自動
 *  差分より確かです。だから対比表があるものを先に、目立たせて出します。
 *  自動差分は「JAF が対比表を出していない改正」を埋めるためのものという
 *  位置づけになります。 */
const Row: React.FC<{ notice: Notice }> = ({ notice }) => (
  <li className={`notice-row${notice.hasComparison ? ' has-comparison' : ''}`}>
    <div className="notice-head">
      {notice.date && <time dateTime={notice.date}>{notice.date}</time>}
      {notice.noticeNo && <span className="notice-no">公示 No.{notice.noticeNo}</span>}
      <a href={notice.url} target="_blank" rel="noreferrer nofollow">
        {notice.title}
      </a>
    </div>
    {notice.attachments.length > 0 && (
      <ul className="notice-files">
        {notice.attachments.map((a) => (
          <li key={a.url}>
            <a href={a.url} target="_blank" rel="noreferrer nofollow">
              {a.comparison && <span className="notice-badge">新旧対照</span>}
              {a.text || 'PDF'}
            </a>
          </li>
        ))}
      </ul>
    )}
  </li>
);

const INITIAL = 3;

export const Notices: React.FC<{ notices: Notice[]; heading?: string }> = ({
  notices,
  heading = 'JAF の公示',
}) => {
  const [expanded, setExpanded] = useState(false);
  if (notices.length === 0) return null;

  // 対比表があるものを先に出す（JAF 自身の新旧対照なので価値が高い）
  const sorted = [...notices].sort((a, b) => {
    if (a.hasComparison !== b.hasComparison) return a.hasComparison ? -1 : 1;
    return (b.date ?? '').localeCompare(a.date ?? '');
  });
  const shown = expanded ? sorted : sorted.slice(0, INITIAL);

  return (
    <section className="notices">
      <h2>
        {heading}
        <span className="count">{notices.length} 件</span>
      </h2>
      <ul>
        {shown.map((n) => (
          <Row key={n.id} notice={n} />
        ))}
      </ul>
      {sorted.length > INITIAL && (
        <button type="button" className="notice-more" onClick={() => setExpanded(!expanded)}>
          {expanded ? '折りたたむ' : `残り ${sorted.length - INITIAL} 件を表示`}
        </button>
      )}
    </section>
  );
};

/** 差分ページの冒頭に出す案内。
 *  自動差分を見せる前に「JAF の対比表がある」と伝えるほうが誠実。 */
export const ComparisonHint: React.FC<{ notices: Notice[] }> = ({ notices }) => {
  const withComparison = notices.filter((n) => n.hasComparison);
  if (withComparison.length === 0) return null;
  const first = withComparison[0];
  const file = first.attachments.find((a) => a.comparison) ?? first.attachments[0];
  return (
    <p className="comparison-hint">
      <span>
        この規則には <strong>JAF 自身が出した新旧対照表</strong> があります。
        下の自動差分より確実です。
      </span>
      {file && (
        <a href={file.url} target="_blank" rel="noreferrer nofollow">
          JAF の対比表を開く ↗
        </a>
      )}
    </p>
  );
};
