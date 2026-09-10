import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
export const metadata: Metadata = { title: 'SideFX Lab', description: 'Run declared capabilities and inspect their outcomes.', robots: { index: false, follow: false } };
export default function Layout({ children }: { children: ReactNode }) {
  return <html lang="en"><body><a className="skip-link" href="#main">Skip to content</a><main id="main">{children}</main></body></html>;
}
