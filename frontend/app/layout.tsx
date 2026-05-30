import "./globals.css";
import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "EdgeOps Command Agent",
  description: "点検データを、判断・行動・報告に変換する保全AIエージェント",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <div className="topbar">
          <div className="topbar-inner">
            <span className="topbar-title">🛠 EdgeOps Command Agent</span>
            <span className="topbar-subtitle">点検データを、判断・行動・報告に変換する保全AIエージェント</span>
          </div>
        </div>
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
