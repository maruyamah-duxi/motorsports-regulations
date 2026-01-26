import React, { useState, useMemo } from 'react';
import { REGULATIONS_DATA } from './data';
import { RegulationCard } from './components/RegulationCard';
import { RegulationModal } from './components/RegulationModal';
import { ChatInterface } from './components/ChatInterface';
import { StatsChart } from './components/StatsChart';
import { Regulation, ViewMode } from './types';

const App: React.FC = () => {
  const [viewMode, setViewMode] = useState<ViewMode>(ViewMode.LIST);
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedRegulation, setSelectedRegulation] = useState<Regulation | null>(null);

  // Grouping Logic
  const groupedRegulations = useMemo(() => {
    const lowerTerm = searchTerm.toLowerCase();
    
    // 1. Filter
    const filtered = REGULATIONS_DATA.filter(reg => 
      reg.title.toLowerCase().includes(lowerTerm) || 
      reg.summary.toLowerCase().includes(lowerTerm) ||
      reg.content_markdown.toLowerCase().includes(lowerTerm) ||
      reg.content_tags.some(tag => tag.toLowerCase().includes(lowerTerm))
    );

    // 2. Group by Category
    const groups: Record<string, Regulation[]> = {};
    filtered.forEach(reg => {
      if (!groups[reg.category]) {
        groups[reg.category] = [];
      }
      groups[reg.category].push(reg);
    });

    return groups;
  }, [searchTerm]);

  const hasResults = Object.keys(groupedRegulations).length > 0;

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-20 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="bg-blue-600 p-1.5 rounded-lg">
              <svg className="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
              </svg>
            </div>
            <h1 className="text-lg font-bold text-slate-800 tracking-tight">JAF Motorsports Rules</h1>
          </div>
          
          <div className="flex space-x-1 bg-slate-100 p-1 rounded-lg">
            <button 
              onClick={() => setViewMode(ViewMode.LIST)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all ${viewMode === ViewMode.LIST ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            >
              一覧
            </button>
            <button 
              onClick={() => setViewMode(ViewMode.ANALYTICS)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition-all ${viewMode === ViewMode.ANALYTICS ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            >
              統計
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
          
          {/* Main Content Area */}
          <div className="lg:col-span-8 space-y-8">
            
            {viewMode === ViewMode.LIST ? (
              <>
                {/* Search Bar */}
                <div className="relative group">
                  <div className="absolute -inset-1 bg-gradient-to-r from-blue-600 to-indigo-600 rounded-lg blur opacity-25 group-hover:opacity-50 transition duration-1000 group-hover:duration-200"></div>
                  <div className="relative bg-white rounded-lg shadow-sm">
                    <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
                      <svg className="h-5 w-5 text-slate-400" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clipRule="evenodd" />
                      </svg>
                    </div>
                    <input
                      type="text"
                      className="block w-full pl-11 pr-4 py-4 text-base border-0 rounded-lg bg-transparent placeholder-slate-400 focus:ring-2 focus:ring-blue-500 focus:outline-none"
                      placeholder="キーワードで規則を検索 (例: 第1条, ペナルティ, 安全基準)"
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                    />
                  </div>
                </div>

                {/* Grouped Results */}
                {hasResults ? (
                  <div className="space-y-10">
                    {Object.entries(groupedRegulations).map(([category, regulations]) => (
                      <div key={category} className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                        <div className="flex items-center gap-3 mb-4">
                          <h2 className="text-lg font-bold text-slate-800 border-l-4 border-blue-600 pl-3">
                            {category}
                          </h2>
                          <span className="bg-slate-100 text-slate-500 text-xs font-medium px-2 py-0.5 rounded-full">
                            {regulations.length}
                          </span>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 gap-4">
                          {regulations.map(reg => (
                            <RegulationCard 
                              key={reg.id} 
                              regulation={reg} 
                              onClick={setSelectedRegulation}
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-20 bg-white rounded-2xl border border-dashed border-slate-300">
                    <svg className="mx-auto h-12 w-12 text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <h3 className="mt-2 text-sm font-medium text-slate-900">見つかりませんでした</h3>
                    <p className="mt-1 text-sm text-slate-500">検索条件を変更して再度お試しください。</p>
                  </div>
                )}
              </>
            ) : (
              <StatsChart data={REGULATIONS_DATA} />
            )}
          </div>

          {/* Right Sidebar */}
          <div className="lg:col-span-4 space-y-6">
            <div className="sticky top-24 space-y-6">
              <ChatInterface />
              
              <div className="bg-slate-900 rounded-xl p-5 text-slate-300 text-sm shadow-lg">
                <h4 className="font-bold text-white mb-3 flex items-center">
                  <svg className="w-4 h-4 mr-2 text-yellow-400" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                  </svg>
                  ご利用ガイド
                </h4>
                <ul className="space-y-2 list-disc list-inside marker:text-slate-600">
                  <li>カードをクリックすると詳細が表示されます。</li>
                  <li>右下のボタンからPDFを直接開けます。</li>
                  <li>AIチャットで自然言語での質問が可能です。</li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* Detail Modal */}
      <RegulationModal 
        regulation={selectedRegulation} 
        onClose={() => setSelectedRegulation(null)} 
      />
    </div>
  );
};

export default App;