import type { SVGProps } from 'react';

export type IconName =
  | 'alert'
  | 'archive'
  | 'calendar'
  | 'check'
  | 'chevronDown'
  | 'chevronLeft'
  | 'chevronRight'
  | 'close'
  | 'columns'
  | 'download'
  | 'edit'
  | 'empty'
  | 'filter'
  | 'home'
  | 'menu'
  | 'more'
  | 'package'
  | 'plus'
  | 'receipt'
  | 'refresh'
  | 'repair'
  | 'return'
  | 'sales'
  | 'search'
  | 'trash'
  | 'upload'
  | 'warehouse';

const paths: Record<IconName, React.ReactNode> = {
  alert: (
    <>
      <path d="M12 3 2.8 20h18.4L12 3Z" />
      <path d="M12 9v5m0 3h.01" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="4" width="18" height="5" rx="1.5" />
      <path d="M5 9v10h14V9M10 13h4" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M16 3v4M8 3v4M3 10h18" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  chevronDown: <path d="m7 10 5 5 5-5" />,
  chevronLeft: <path d="m15 18-6-6 6-6" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  columns: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M10 4v16M15 4v16" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12m0 0 4-4m-4 4-4-4" />
      <path d="M5 20h14" />
    </>
  ),
  edit: (
    <>
      <path d="M13.5 6.5 17.5 10.5M4 20l4.5-1 10-10a2.8 2.8 0 0 0-4-4l-10 10L4 20Z" />
    </>
  ),
  empty: (
    <>
      <path d="M4 7h16v12H4V7Z" />
      <path d="M8 7V4h8v3M9 12h6" />
    </>
  ),
  filter: <path d="M4 6h16l-6.5 7.1V19l-3 1v-6.9L4 6Z" />,
  home: (
    <>
      <path d="m3 11 9-8 9 8" />
      <path d="M5 10v10h14V10M10 20v-6h4v6" />
    </>
  ),
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  more: (
    <>
      <circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  package: (
    <>
      <path d="m4 8 8-4 8 4-8 4-8-4Z" />
      <path d="M4 8v9l8 4 8-4V8M12 12v9" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  receipt: (
    <>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 8h6M9 12h6M9 16h3" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 7v5h-5" />
      <path d="M18.2 17A8 8 0 1 1 20 12" />
    </>
  ),
  repair: (
    <>
      <path d="M14.5 6.5a4 4 0 0 0-5-5l2.2 2.2-3 3L6.5 4.5a4 4 0 0 0 5 5L19 17a2 2 0 1 1-3 3l-7.5-7.5" />
    </>
  ),
  return: (
    <>
      <path d="m9 7-5 5 5 5" />
      <path d="M4 12h10a6 6 0 0 1 6 6v1" />
    </>
  ),
  sales: (
    <>
      <path d="M4 19V9m5 10V5m5 14v-7m5 7V3" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m16 16 5 5" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" />
    </>
  ),
  upload: (
    <>
      <path d="M12 16V4m0 0L8 8m4-4 4 4" />
      <path d="M5 20h14" />
    </>
  ),
  warehouse: (
    <>
      <path d="M3 9 12 4l9 5v11H3V9Z" />
      <path d="M8 20v-7h8v7M3 10h18" />
    </>
  ),
};

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
}

export function Icon({ name, ...props }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="20"
      viewBox="0 0 24 24"
      width="20"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
