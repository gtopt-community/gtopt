import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GTOpt Web Service",
  description:
    "Upload and run GTOpt cases — optimization and analysis of generation, transmission and storage",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
