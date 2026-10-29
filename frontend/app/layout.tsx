import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';

export const metadata: Metadata = { title: 'Tradegen AI — Paper Trading Workspace', description: 'Review, approve and monitor paper trading strategies in a risk-controlled workspace.' };
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
