import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';

// サーバはクローラ向けに規則の本文を #prerender として同梱している
// （server/seo.py）。React は #root しか触らないので衝突しないが、
// 二重表示になるのでマウント前に外す。
document.getElementById('prerender')?.remove();

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Could not find root element to mount to');
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
