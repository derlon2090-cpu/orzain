import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';

export const metadata: Metadata = { title: 'TRADEGEN AI — Controlled trading', description: 'A risk-first trading workspace. Demo mode only.' };
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
