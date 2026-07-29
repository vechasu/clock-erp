import { Route, Routes } from 'react-router-dom';

import { InfrastructurePage } from '../pages/InfrastructurePage';
import { NotMigratedPage } from '../pages/NotMigratedPage';

export function App() {
  return (
    <Routes>
      <Route index element={<InfrastructurePage />} />
      <Route path="*" element={<NotMigratedPage />} />
    </Routes>
  );
}
