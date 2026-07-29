import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { LoadingState } from '../components/Layout';
import { NotMigratedPage } from '../pages/NotMigratedPage';

const ProductsPage = lazy(() =>
  import('../features/products/ProductsPage').then((module) => ({
    default: module.ProductsPage,
  })),
);
const ReceiptsPage = lazy(() =>
  import('../features/receipts/ReceiptsPage').then((module) => ({
    default: module.ReceiptsPage,
  })),
);
const RepairsPage = lazy(() =>
  import('../features/repairs/RepairsPage').then((module) => ({
    default: module.RepairsPage,
  })),
);
const SalesPage = lazy(() =>
  import('../features/sales/SalesPage').then((module) => ({
    default: module.SalesPage,
  })),
);

export function App() {
  return (
    <Suspense fallback={<LoadingState label="Открываем раздел…" />}>
      <Routes>
        <Route index element={<Navigate replace to="/products" />} />
        <Route path="products" element={<ProductsPage />} />
        <Route path="receipts" element={<ReceiptsPage />} />
        <Route path="sales" element={<SalesPage />} />
        <Route path="repairs" element={<RepairsPage />} />
        <Route path="*" element={<NotMigratedPage />} />
      </Routes>
    </Suspense>
  );
}
