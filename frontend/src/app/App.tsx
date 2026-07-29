import { Route, Routes } from 'react-router-dom';

import { InfrastructurePage } from '../pages/InfrastructurePage';
import { NotMigratedPage } from '../pages/NotMigratedPage';
import { ProductsPage } from '../features/products/ProductsPage';
import { ReceiptsPage } from '../features/receipts/ReceiptsPage';
import { SalesPage } from '../features/sales/SalesPage';

export function App() {
  return (
    <Routes>
      <Route index element={<InfrastructurePage />} />
      <Route path="products" element={<ProductsPage />} />
      <Route path="receipts" element={<ReceiptsPage />} />
      <Route path="sales" element={<SalesPage />} />
      <Route path="*" element={<NotMigratedPage />} />
    </Routes>
  );
}
