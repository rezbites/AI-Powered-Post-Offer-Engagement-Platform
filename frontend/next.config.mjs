/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output copies only the files the server actually needs, which
  // keeps the runtime image small and free of build tooling.
  output: "standalone",
};

export default nextConfig;
