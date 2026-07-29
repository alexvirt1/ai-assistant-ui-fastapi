/** @type {import('next').NextConfig} */
const nextConfig = {
  // Lets a test build write somewhere other than the .next a running
  // `next start` is serving from. Defaults to the normal location.
  distDir: process.env.NEXT_DIST_DIR || ".next",
/**  async rewrites() {
    return [
      {
        source: "/api/chat",
        destination: "http://127.0.0.1:8000/api/chat",
      },
    ];
  },*/
};

export default nextConfig;
