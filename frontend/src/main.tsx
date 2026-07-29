import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './app/App';
import { AppProviders } from './app/providers';
import './styles/legacy-tokens.css';
import './styles/global.css';

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('React root element is missing');
}

createRoot(rootElement).render(
  <StrictMode>
    <BrowserRouter basename="/app">
      <AppProviders>
        <App />
      </AppProviders>
    </BrowserRouter>
  </StrictMode>,
);
