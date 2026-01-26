import React, { useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { Regulation } from '../types';

interface Props {
  data: Regulation[];
}

export const StatsChart: React.FC<Props> = ({ data }) => {
  const chartData = useMemo(() => {
    const counts: Record<string, number> = {};
    data.forEach(reg => {
      // Simplify category names for chart labels
      let cat = reg.category;
      if (cat.includes("規則・細則")) cat = "規則・細則";
      if (cat.includes("車両規則")) cat = "車両規則";
      if (cat.includes("カート")) cat = "カート";
      if (cat.includes("統一規則")) cat = "統一規則";
      if (cat.includes("ガイドライン")) cat = "ガイドライン";
      
      counts[cat] = (counts[cat] || 0) + 1;
    });

    return Object.keys(counts).map(key => ({
      name: key,
      count: counts[key]
    }));
  }, [data]);

  const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8'];

  return (
    <div className="bg-white p-6 rounded-xl shadow-sm border border-slate-200 h-[400px]">
      <h3 className="text-lg font-bold text-slate-800 mb-6">カテゴリ別 規則数</h3>
      <ResponsiveContainer width="100%" height="85%">
        <BarChart
          data={chartData}
          margin={{ top: 5, right: 30, left: 0, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#f1f5f9" />
          <XAxis 
            dataKey="name" 
            tick={{fontSize: 12, fill: '#64748b'}} 
            axisLine={false}
            tickLine={false}
          />
          <YAxis 
            tick={{fontSize: 12, fill: '#64748b'}} 
            axisLine={false}
            tickLine={false}
          />
          <Tooltip 
            cursor={{fill: '#f8fafc'}}
            contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
          />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>
            {chartData.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};