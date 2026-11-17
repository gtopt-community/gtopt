import nextConfig from "eslint-config-next";

export default [
  ...nextConfig,
  {
    rules: {
      // The App Router root layout (src/app/layout.tsx) applies to every
      // page, so this pages/_document.js-oriented rule does not apply.
      "@next/next/no-page-custom-font": "off",
    },
  },
  {
    ignores: [".next/**", "node_modules/**", "coverage/**"],
  },
];
