import React, { useEffect, useRef, useState } from 'react';
import type { AskSource, AskStatus } from '../types';
import { askStream, fetchAskStatus } from '../lib/api';
import { Link } from './Link';

interface Turn {
  role: 'user' | 'assistant';
  text: string;
  sources?: AskSource[];
  error?: string;
  streaming?: boolean;
  /** 同じ質問への回答を使い回した（API を呼んでいない） */
  cached?: boolean;
}

const EXAMPLES = [
  'ロールケージの溶接に関する規定は？',
  'ラリー車両のスペアホイールは何本まで積める？',
  'HANSデバイスを使うときのシートベルトのアジャスター位置は？',
  '国内格式のラリーでレーシングスーツに求められる条件は？',
];

/** 回答の最小限のマークダウンを描く。
 *  system instruction で書式を絞っているので、箇条書き・強調・[n] の出典番号だけ扱う。 */
function renderInline(text: string, sources: AskSource[], keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // **強調** と [1] / [1][3] を拾う
  const pattern = /\*\*(.+?)\*\*|\[(\d{1,2})\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = pattern.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[1] !== undefined) {
      nodes.push(<strong key={`${keyBase}-b${i++}`}>{m[1]}</strong>);
    } else {
      const n = Number(m[2]);
      const source = sources.find((s) => s.index === n);
      nodes.push(
        source ? (
          <Link
            key={`${keyBase}-c${i++}`}
            className="cite"
            href={source.url}
            title={`${source.title} ／ ${source.headingPath || source.heading} ／ P.${source.page}`}
          >
            {n}
          </Link>
        ) : (
          <span key={`${keyBase}-c${i++}`} className="cite is-dangling">
            {n}
          </span>
        ),
      );
    }
    last = pattern.lastIndex;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

const Answer: React.FC<{ text: string; sources: AskSource[] }> = ({ text, sources }) => {
  const blocks: React.ReactNode[] = [];
  const lines = text.split('\n');
  let bullets: string[] = [];

  const flushBullets = () => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`}>
        {bullets.map((b, i) => (
          <li key={i}>{renderInline(b, sources, `ul${blocks.length}-${i}`)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  lines.forEach((raw, index) => {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*・]\s+(.*)$/);
    if (bullet) {
      bullets.push(bullet[1]);
      return;
    }
    flushBullets();
    if (!line.trim()) return;
    const heading = line.match(/^\s*#{1,4}\s+(.*)$/);
    if (heading) {
      blocks.push(<h3 key={`h-${index}`}>{renderInline(heading[1], sources, `h${index}`)}</h3>);
      return;
    }
    blocks.push(<p key={`p-${index}`}>{renderInline(line, sources, `p${index}`)}</p>);
  });
  flushBullets();

  return <>{blocks}</>;
};

const SourceList: React.FC<{ sources: AskSource[] }> = ({ sources }) => (
  <details className="sources" open={false}>
    <summary>根拠にした条文 {sources.length} 件</summary>
    <ol>
      {sources.map((s) => (
        <li key={s.index} value={s.index}>
          <Link href={s.url}>{s.title}</Link>
          <div className="where">
            {(s.headingPath || s.heading) && <>{s.headingPath || s.heading} ／ </>}P.{s.page}
            {s.pdfUrl && (
              <>
                {' ／ '}
                <a href={s.pdfUrl} target="_blank" rel="noreferrer nofollow">
                  原本 PDF ↗
                </a>
              </>
            )}
          </div>
          <div className="excerpt">{s.excerpt}</div>
        </li>
      ))}
    </ol>
  </details>
);

export const Ask: React.FC<{ initialQuestion?: string }> = ({ initialQuestion }) => {
  const [status, setStatus] = useState<AskStatus | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState(initialQuestion ?? '');
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchAskStatus(controller.signal)
      .then(setStatus)
      .catch(() => setStatus(null));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [turns]);

  const send = async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed || busy) return;

    const history = turns
      .filter((t) => !t.error && t.text.trim())
      .map((t) => ({ role: t.role, text: t.text }));

    setInput('');
    setBusy(true);
    setTurns((prev) => [
      ...prev,
      { role: 'user', text: trimmed },
      { role: 'assistant', text: '', streaming: true },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    const patchLast = (patch: Partial<Turn>) =>
      setTurns((prev) => {
        const next = [...prev];
        const i = next.length - 1;
        next[i] = { ...next[i], ...patch };
        return next;
      });

    try {
      await askStream(
        trimmed,
        history,
        {
          onSources: (sources) => patchLast({ sources }),
          onDelta: (delta) =>
            setTurns((prev) => {
              const next = [...prev];
              const i = next.length - 1;
              next[i] = { ...next[i], text: next[i].text + delta };
              return next;
            }),
          onError: (message) => patchLast({ error: message, streaming: false }),
          onDone: (info) => patchLast({ streaming: false, cached: info?.cached }),
        },
        controller.signal,
      );
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        patchLast({ error: String((err as Error).message || err), streaming: false });
      }
    } finally {
      patchLast({ streaming: false });
      setBusy(false);
      abortRef.current = null;
    }
  };

  if (status && !status.available) {
    return (
      <>
        <h1 className="page-title">規則について質問する</h1>
        <div className="empty">
          AI による回答は現在利用できません。
          <br />
          {status.hasApiKey
            ? '検索インデックスの準備ができていません。'
            : 'サーバに API キーが設定されていません。'}
          <br />
          <br />
          上の検索窓からの全文検索はご利用いただけます。
        </div>
      </>
    );
  }

  return (
    <>
      <h1 className="page-title">規則について質問する</h1>
      <p className="page-sub">
        質問に関係する条文を検索し、<strong>その条文に書かれていることだけ</strong>
        を根拠に答えます。答えには出典（規則名・条・ページ）が付き、該当箇所を直接開けます。
        条文に無いことは答えません。
        {status && !status.hybrid && (
          <>
            <br />
            <span style={{ color: 'var(--fg-faint)' }}>
              （現在は全文検索のみで根拠を探しています。言い換えに弱い場合があります）
            </span>
          </>
        )}
      </p>

      {turns.length === 0 && (
        <div className="ask-examples">
          <div className="label">たとえばこんな質問ができます</div>
          {EXAMPLES.map((ex) => (
            <button key={ex} onClick={() => send(ex)} disabled={busy}>
              {ex}
            </button>
          ))}
        </div>
      )}

      <div className="ask-thread">
        {turns.map((turn, i) =>
          turn.role === 'user' ? (
            <div className="ask-q" key={i}>
              {turn.text}
            </div>
          ) : (
            <div className="ask-a" key={i}>
              {turn.error ? (
                <div className="error">{turn.error}</div>
              ) : (
                <>
                  {turn.text ? (
                    <Answer text={turn.text} sources={turn.sources ?? []} />
                  ) : (
                    <p className="loading">
                      {turn.sources ? '回答を作成しています…' : '関係する条文を探しています…'}
                    </p>
                  )}
                  {turn.streaming && turn.text && <span className="caret" aria-hidden="true" />}
                  {!turn.streaming && (turn.sources?.length ?? 0) > 0 && (
                    <SourceList sources={turn.sources!} />
                  )}
                  {!turn.streaming && turn.cached && (
                    <p className="cache-note">
                      同じ質問への回答を再利用しました（AI は呼び出していません）
                    </p>
                  )}
                </>
              )}
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>

      <form
        className="ask-form"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              send(input);
            }
          }}
          placeholder="規則について質問してください（例: ロールケージの材質は？）"
          rows={2}
          maxLength={400}
          aria-label="質問"
        />
        <div className="ask-form-actions">
          <span className="hint">⌘/Ctrl + Enter で送信</span>
          {busy ? (
            <button type="button" onClick={() => abortRef.current?.abort()}>
              中止
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()}>
              質問する
            </button>
          )}
        </div>
      </form>

      <p className="ask-disclaimer">
        AI の回答は参考情報です。自動変換した本文をもとにしているため誤りが含まれる可能性があります。
        競技における判断は必ず JAF の原本 PDF をご確認ください。
        {status?.chatModel && <> ／ モデル {status.chatModel}</>}
      </p>
    </>
  );
};
