/** 規則 1 件のメタデータと目次。**本文は含まない**。
 *
 *  JAF のサイトポリシーが資料の再配布を認めていないため、全文の配信を
 *  やめた（docs/architecture.md 9 章）。本文はサーバ側に残り、検索の抜粋と
 *  AI 回答の根拠としてだけ使う。読者には「どの条がどのページか」を返し、
 *  本文は原本 PDF の該当ページへ送る。 */
export interface RegulationDocument {
  docId: string;
  title: string;
  source: string;
  section: string;
  group: string;
  pdfUrl: string | null;
  uploadDate: string | null;
  pageCount: number;
  chars: number;
  figures: number;
  tables: number;
  series: string | null;
  edition: string | null;
  toc: DocumentTocItem[];
}

/** 目次の 1 行。原本のページ番号を持つ（URL は pdfUrl と組み合わせて作る） */
export interface DocumentTocItem {
  level: number;
  text: string;
  page: number | null;
}

/** /api/documents の 1 件（本文は含まない軽量版） */
export interface DocumentSummary {
  docId: string;
  title: string;
  source: string;
  section: string;
  group: string;
  uploadDate: string | null;
  pdfUrl: string | null;
  pageCount: number;
  chars: number;
  figures: number;
  tables: number;
}

/** 更新履歴の 1 件。日付は JAF の掲載日で、こちらが検出した日ではない。 */
export interface HistoryEvent {
  date: string;
  /** listed: JAF に掲載された / updated: JAF が差し替えた
   *  edition: 同じ規則の別年度版が公開された / removed: 一覧から消えた */
  type: 'listed' | 'updated' | 'edition' | 'removed';
  /** type が edition のときだけ。切り替わり先の版 */
  docId?: string;
  edition?: string;
}

/** 同じ規則の年度版 1 件 */
export interface EditionRef {
  docId: string;
  title: string;
  edition: string | null;
  uploadDate: string | null;
  current: boolean;
}

/** 差分の集計。「軽微」という区分は置かない。
 *  類似度で軽重を決めると、走行距離が 30km→20分 に変わった条が
 *  類似度 0.971 で「軽微」に分類されてしまうため（実測）。
 *  yearOnly は「年号を伏せたら同一」という厳密な条件で判定している。 */
export interface DiffSummary {
  clauses: number;
  unchanged: number;
  changed: number;
  yearOnly: number;
  added: number;
  removed: number;
}

/** 差分の一覧の 1 件（規則ページから差分ページへ案内するのに使う） */
export interface DiffRef {
  baseDocId: string;
  baseTitle: string | null;
  baseUploadDate: string | null;
  /** revision: 同じ規則の前の版 / edition: 前年度版 */
  kind: 'revision' | 'edition';
  summary: DiffSummary;
}

export type DiffStatus = 'changed' | 'year_only' | 'added' | 'removed';

export interface DiffLine {
  op: 'add' | 'del';
  text: string;
}

export interface DiffChange {
  status: DiffStatus;
  /** 条項番号の階層（第1章 > 第4条） */
  key: string;
  heading: string;
  anchor: string | null;
  page?: number | null;
  previousPage?: number | null;
  similarity?: number;
  lines: DiffLine[];
}

/** /api/documents/{docId}/diff/{baseDocId} */
export interface DocumentDiff {
  kind: 'revision' | 'edition';
  generatedAt: string;
  base: { docId: string | null; title: string | null; uploadDate: string | null };
  target: { docId: string | null; title: string | null; uploadDate: string | null };
  summary: DiffSummary;
  changes: DiffChange[];
  /** 条ごとに原本の該当ページへ送るための PDF URL */
  pdfUrl: string | null;
}

/** JAF の公示に添付された PDF。対比表なら JAF 自身の新旧対照。 */
export interface NoticeAttachment {
  text: string;
  url: string;
  comparison: boolean;
}

/** JAF の公示 1 件（規則に紐づけたもの） */
export interface Notice {
  id: string;
  date: string | null;
  noticeNo: string | null;
  title: string;
  url: string;
  attachments: NoticeAttachment[];
  hasComparison: boolean;
}

/** /api/documents/{docId}/history */
export interface DocumentHistory {
  docId: string;
  series: string | null;
  edition: string | null;
  events: HistoryEvent[];
  /** 年度版が 1 つしかなければ空配列 */
  editions: EditionRef[];
  /** 条単位の改正差分が取れる相手 */
  diffs: DiffRef[];
  /** この規則に関する JAF の公示（新しい順） */
  announcements: Notice[];
}

export interface DocumentsResponse {
  builtAt: string | null;
  count: number;
  items: DocumentSummary[];
}

/** /api/search のヒット 1 件 */
/** 該当条文に紐づく図版（条とページが対応している範囲のものだけ） */
export interface SearchFigure {
  url: string;
  caption: string | null;
  page: number | null;
}

export interface SearchHit {
  docId: string;
  title: string;
  section: string;
  group: string;
  heading: string;
  headingPath: string;
  clause: string | null;
  /** 一致箇所が実際にある原本のページ（チャンクの先頭ページではない） */
  page: number;
  /** 条見出しが一致箇所のものと言えるか */
  headingReliable: boolean;
  /** headingReliable が false のとき、本文から拾った実際の条項 */
  clauseAtMatch: string | null;
  anchor: string | null;
  /** 制御文字でハイライト済みの抜粋（該当語の前後） */
  snippet: string;
  uploadDate: string | null;
  pdfUrl: string | null;
  /** 原本 PDF の該当ページ（…pdf#page=4） */
  pdfPageUrl: string | null;
  figures: SearchFigure[];
  url: string;
}

/** 「どの規則に何件あるか」。表示中のページではなく全ヒットの集計 */
export interface SearchDocCount {
  docId: string;
  title: string;
  section: string;
  group: string;
  pdfUrl: string | null;
  count: number;
}

export interface SearchResponse {
  query: string;
  total: number;
  limit: number;
  offset: number;
  items: SearchHit[];
  byDoc: SearchDocCount[];
}

/** /api/ask が最初に返す根拠チャンク */
export interface AskSource {
  index: number;
  docId: string;
  title: string;
  heading: string;
  headingPath: string;
  page: number;
  anchor: string | null;
  /** アプリ内の該当箇所への直リンク */
  url: string;
  pdfUrl: string | null;
  /** 原本 PDF の該当ページ */
  pdfPageUrl: string | null;
  excerpt: string;
}

export interface AskStatus {
  available: boolean;
  hasApiKey: boolean;
  /** ベクトル検索が有効なチャンク数（0 なら全文検索のみ） */
  vectors: number;
  hybrid: boolean;
  chatModel: string;
  embedModel: string;
}
