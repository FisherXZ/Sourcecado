import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The system prompt is read from personas/*.md at runtime. Next's tracer can't
  // follow a path built at call time, so the files must be included explicitly
  // or the deployed bundle throws "Persona not found" on the first chat request.
  outputFileTracingIncludes: {
    "/api/**": ["./personas/**"],
  },
};

export default nextConfig;
