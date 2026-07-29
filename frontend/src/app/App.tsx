import { Route, Routes } from 'react-router-dom';

import { InfrastructurePage } from '../pages/InfrastructurePage';
import { NotMigratedPage } from '../pages/NotMigratedPage';
import { ProductsPage } from '../features/products/ProductsPage';

export function App() {
  return (
    <Routes>
      <Route index element={<InfrastructurePage />} />
      <Route path="products" element={<ProductsPage />} />
      <Route path="*" element={<NotMigratedPage />} />
    </Routes>
  );
}
