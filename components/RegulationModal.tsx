import React, { useEffect } from 'react';
import Markdown from 'react-markdown';
import { Regulation } from '../types';

interface Props {
  regulation: Regulation | null;
  onClose: () => void;
}

export const RegulationModal: React.FC<Props> = ({ regulation, onClose }) => {
  // Prevent body scroll when modal is open
  useEffect(() => {
    if (regulation) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = 'unset';
    }
    return () => {
      document.body.style.overflow = 'unset';
    };
  }, [regulation]);

  if (!regulation) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div 
        className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm transition-opacity" 
        onClick={onClose}
      ></div>
      <div className="relative bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] overflow-y-auto flex flex-col animate-in fade-in zoom-in duration-200">
        
        {/* Header */}
        <div className="p-6 border-b border-slate-100 sticky top-0 bg-white/95 backdrop-blur z-10 flex justify-between items-start">
          <div>
            <div className="flex flex-wrap gap-2 mb-2">
              <span className="text-xs font-bold text-blue-600 bg-blue-50 px-2.5 py-1 rounded-full border border-blue-100">
                {regulation.category}
              </span>
              {regulation.subCategory && (
                <span className="text-xs font-medium text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full">
                  {regulation.subCategory}
                </span>
              )}
            </div>
            <h2 className="text-xl font-bold text-slate-800 leading-snug pr-8">
              {regulation.title}
            </h2>
          </div>
          <button 
            onClick={onClose} 
            className="text-slate-400 hover:text-slate-600 p-1 rounded-full hover:bg-slate-100 transition-colors"
          >
            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-8">
          
          {/* 概要 */}
          <div>
            <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">概要</h4>
            <p className="text-slate-700 leading-relaxed text-sm">
              {regulation.summary}
            </p>
          </div>

          {/* Markdown Viewer セクション */}
          <div className="border-t border-slate-100 pt-6">
            <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-4 flex items-center">
              <svg className="w-4 h-4 mr-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              規則本文（プレビュー）
            </h4>
            <div className="prose prose-blue prose-sm max-w-none bg-slate-50 p-6 rounded-xl border border-slate-200 overflow-x-auto">
              <Markdown>{regulation.content_markdown}</Markdown>
            </div>
          </div>

          {/* タグとメタデータ */}
          <div>
            <h4 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">関連タグ</h4>
            <div className="flex flex-wrap gap-2">
              {regulation.content_tags.map(tag => (
                <span key={tag} className="text-xs text-slate-600 bg-slate-100 px-3 py-1 rounded-full border border-slate-200 hover:bg-slate-200 transition-colors cursor-default">
                  #{tag}
                </span>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-4 text-xs text-slate-500 bg-slate-50 p-3 rounded-lg border border-slate-100">
             <div className="flex items-center">
                <svg className="w-4 h-4 mr-1.5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                更新日: {regulation.updateDate}
             </div>
             <div className="flex items-center">
                <svg className="w-4 h-4 mr-1.5 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
                ID: {regulation.id}
             </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-6 border-t border-slate-100 bg-slate-50 rounded-b-2xl sticky bottom-0 z-10">
          <a
            href={regulation.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-4 rounded-xl transition-all shadow-lg shadow-blue-200 hover:shadow-blue-300 transform hover:-translate-y-0.5"
          >
            <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
            出典元PDF（JAF公式サイト）を閲覧
          </a>
        </div>
      </div>
    </div>
  );
};