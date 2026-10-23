/** Static export (ADR 0005): no server, served from S3 behind CloudFront under /inbox. */
const nextConfig = {
  output: "export",
  basePath: "/inbox",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  poweredByHeader: false,
};
export default nextConfig;
