import React, { useEffect, useRef } from 'react';
import type { DocumentHistory, HistoryEvent } from '../types';
import { Link } from './Link';

/** 履歴の種別ごとの見せ方。
 *  日付は JAF の掲載日。こちらの再変換（pipelineVersion だけの差）は
 *  build_history.py の段階で除いてあるので、ここには来ない。 */
const LABEL: Record<HistoryEvent['type'], string> = {
  listed: 'JAF に掲載',
  updated: 'JAF が更新',
  edition: '別の年度版が公開',
  removed: 'JAF の一覧から削除',
};

const Row: React.FC<{ event: HistoryEvent; onNavigate: () => void }> = ({
  event,
  onNavigate,
}) => (
  <li className={`hist-row is-${event.type}`}>
    <time dateTime={event.date}>{event.date}</time>
    <span className="what">
      {event.type === 'edition' && event.docId ? (
        <>
          {event.edition ?? '別の年度版'}が公開{' '}
          <Link href={`/doc/${event.docId}`} onClick={onNavigate}>
            開く
          </Link>
        </>
      ) : (
        LABEL[event.type]
      )}
    </span>
  </li>
);

/** 更新履歴のポップアップ。
 *  <dialog> を使うと Esc で閉じる・背景をクリックで閉じる・フォーカスの
 *  閉じ込めがブラウザ側で効くので、自前で実装しない。 */
export const HistoryDialog: React.FC<{
  history: DocumentHistory;
  title: string;
  open: boolean;
  onClose: () => void;
}> = ({ history, title, open, onClose }) => {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  // Esc やバックドロップで閉じたときも親の状態を戻す
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.addEventListener('close', onClose);
    return () => el.removeEventListener('close', onClose);
  }, [onClose]);

  const events = [...history.events].reverse(); // 新しい順

  return (
    <dialog
      className="hist-dialog"
      ref={ref}
      onClick={(e) => {
        // バックドロップ（dialog 自身）のクリックで閉じる
        if (e.target === ref.current) ref.current?.close();
      }}
    >
      <div className="hist-head">
        <strong>更新履歴</strong>
        <button type="button" onClick={() => ref.current?.close()} aria-label="閉じる">
          ✕
        </button>
      </div>
      <p className="hist-doc">{title}</p>
      {events.length === 0 ? (
        <p className="hist-empty">記録がありません。</p>
      ) : (
        <ol className="hist-list">
          {events.map((e, i) => (
            <Row key={`${e.date}-${e.type}-${i}`} event={e} onNavigate={onClose} />
          ))}
        </ol>
      )}
      <p className="hist-note">
        日付は JAF の掲載日です。掲載日が変わらない差し替えは追跡できません。
      </p>
    </dialog>
  );
};

/** 同じ規則の別年度版への相互リンク。
 *  JAF は年度が変わると別ファイルとして公開するため、こちらでは別文書に
 *  なる。関係を示さないと、同じセクションに 2026 年版と 2027 年版が並んで
 *  どちらが有効か分からない。 */
export const EditionBanner: React.FC<{ history: DocumentHistory }> = ({ history }) => {
  const others = history.editions.filter((e) => !e.current);
  if (others.length === 0) return null;
  return (
    <p className="edition-banner">
      <span>
        この規則には別の年度版があります
        {history.edition ? `（いま見ているのは${history.edition}）` : ''}。
      </span>
      {others.map((e) => (
        <Link key={e.docId} className="edition-link" href={`/doc/${e.docId}`}>
          {e.edition || e.title} を開く
        </Link>
      ))}
    </p>
  );
};
