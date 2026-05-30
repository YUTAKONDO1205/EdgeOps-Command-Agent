/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    // The backend URL. In production set NEXT_PUBLIC_API_BASE to the
    // FastAPI service's external URL.
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000",
  },
};
export default nextConfig;
