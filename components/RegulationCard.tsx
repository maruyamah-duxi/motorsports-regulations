import React from 'react';
import { Regulation } from '../types';

interface Props {
  regulation: Regulation;
  onClick: (reg: Regulation) => void;
}

export const RegulationCard: React.FC<Props> = ({ regulation, onClick }) => {
  return (
    <div 
      onClick={() => onClick(regulation)}
      className="group bg-white rounded-lg border border-slate-200 p-4 hover:shadow-md hover:border-blue-300 transition-all cursor-pointer flex flex-col h-full relative overflow-hidden"
    >
      <div className="absolute top-0 left-0 w-1 h-full bg-blue-500 opacity-0 group-hover:opacity-100 transition-opacity"></div>
      
      <div className="mb-2 flex items-center justify-between">
        {regulation.subCategory ? (
          <span className="text-[10px] uppercase font-bold text-slate-500 tracking-wider">
            {regulation.subCategory}
          </span>
        ) : (
          <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider">
            Regulation
          </span>
        )}
        <span className="text-[10px] text-slate-400 bg-slate-50 px-1.5 py-0.5 rounded">
          {regulation.updateDate}
        </span>
      </div>

      <h3 className="text-base font-bold text-slate-800 mb-2 leading-snug group-hover:text-blue-700 transition-colors line-clamp-2">
        {regulation.title}
      </h3>

      <p className="text-xs text-slate-500 mb-3 line-clamp-2 leading-relaxed">
        {regulation.summary}
      </p>

      <div className="mt-auto pt-2 flex items-center justify-between border-t border-slate-50">
        <div className="flex gap-1 overflow-hidden">
          {regulation.content_tags.slice(0, 2).map(tag => (
            <span key={tag} className="text-[10px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded whitespace-nowrap">
              #{tag}
            </span>
          ))}
          {regulation.content_tags.length > 2 && (
            <span className="text-[10px] text-slate-400 px-1">+</span>
          )}
        </div>
        <span className="text-blue-600 text-xs font-medium flex items-center group-hover:translate-x-1 transition-transform">
          詳細
          <svg className="w-3 h-3 ml-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </span>
      </div>
    </div>
  );
};