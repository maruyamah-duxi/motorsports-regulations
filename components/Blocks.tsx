import React from 'react';
import type { Block, RegulationDocument } from '../types';
import { assetUrl } from '../lib/api';

/** 1 行目が見出し行か推定する（pipeline/jafreg/render.py と同じ規則）。
 *  JAF の表は見出し行を持たないものが多く、無条件に <th> にすると
 *  本文の 1 行目が太字になってしまう。 */
function looksLikeHeaderRow(rows: (string | null)[][]): boolean {
  if (rows.length < 2) return false;
  const first = rows[0].map((c) => String(c ?? ''));
  if (first.some((c) => !c) || first.some((c) => c.length > 12)) return false;
  const restMax = Math.max(
    0,
    ...rows.slice(1).flatMap((r) => r.map((c) => String(c ?? '').length)),
  );
  return restMax > 20;
}

const TableBlock: React.FC<{ rows: (string | null)[][] }> = ({ rows }) => {
  const header = looksLikeHeaderRow(rows);
  return (
    <div className="table-wrap">
      <table>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) =>
                header && i === 0 ? (
                  <th key={j}>{cell ?? ''}</th>
                ) : (
                  <td key={j}>{cell ?? ''}</td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const PageMark: React.FC<{ page: number }> = ({ page }) => (
  <span className="page-mark" id={`p${page}`} aria-hidden="true">
    P.{page}
  </span>
);

/** 構造化ブロックを HTML に落とす。原本のページ番号を右肩に添える。 */
export const Blocks: React.FC<{ doc: RegulationDocument }> = ({ doc }) => {
  let lastPage: number | null = null;

  return (
    <>
      {doc.blocks.map((block: Block, index: number) => {
        // キャプションは figure 側に取り込み済み
        if (block.type === 'caption') return null;

        const mark = block.page !== lastPage ? <PageMark page={block.page} /> : null;
        if (block.page !== lastPage) lastPage = block.page;
        const key = `${index}-${block.type}`;

        switch (block.type) {
          case 'heading': {
            const level = Math.min(6, Math.max(2, (block.level ?? 3) + 1));
            const Tag = `h${level}` as 'h2';
            return (
              <Tag key={key} id={block.id}>
                {mark}
                {block.text}
              </Tag>
            );
          }
          case 'paragraph':
            return (
              <p key={key}>
                {mark}
                {block.text}
              </p>
            );
          case 'figure': {
            // 原本の切り出し矩形から縦横比を出して先に領域を確保する。
            // これをしないと画像の読み込みで本文が下にずれ、アンカーで
            // 飛んだ位置が狂う。
            const [x0, y0, x1, y1] = block.bbox;
            const ratio = y1 - y0 > 0 ? (x1 - x0) / (y1 - y0) : undefined;
            return (
              <figure key={key}>
                {mark}
                <img
                  src={assetUrl(doc.docId, block.asset ?? '')}
                  alt={block.caption ?? `${doc.title} P.${block.page} の図版`}
                  loading="lazy"
                  style={ratio ? { aspectRatio: String(ratio) } : undefined}
                />
                {block.caption && <figcaption>{block.caption}</figcaption>}
              </figure>
            );
          }
          case 'table':
            return (
              <React.Fragment key={key}>
                {mark}
                <TableBlock rows={block.rows ?? []} />
              </React.Fragment>
            );
          default:
            return null;
        }
      })}
    </>
  );
};
