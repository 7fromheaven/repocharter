import "./globals.css";

const title = "RepoCharter — One repo charter. Every coding agent.";
const description =
  "Keep project instructions, on-demand context, and tested guardrails in one portable repository-native system.";

export const metadata = {
  metadataBase: new URL("https://repocharter.com"),
  title,
  description,
  applicationName: "RepoCharter",
  keywords: [
    "coding agents",
    "AGENTS.md",
    "Claude Code",
    "Codex",
    "Cursor Agent",
    "agent context",
    "AI developer tools",
  ],
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    url: "/",
    siteName: "RepoCharter",
    title,
    description,
    images: [
      {
        url: "/opengraph-image",
        width: 1200,
        height: 630,
        alt: "RepoCharter — One repo charter. Every coding agent.",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: ["/opengraph-image"],
  },
  icons: {
    icon: "/icon.svg",
  },
};

export const viewport = {
  colorScheme: "dark",
  themeColor: "#080b10",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
