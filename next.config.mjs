/** @type {import('next').NextConfig} */
const nextConfig = {
  // Next 16 otherwise appends framework guidance to the repository's canonical AGENTS.md
  // every time `next dev` starts. RepoCharter owns that file; framework docs stay on demand.
  agentRules: false,
  poweredByHeader: false,
  reactStrictMode: true,
};

export default nextConfig;
