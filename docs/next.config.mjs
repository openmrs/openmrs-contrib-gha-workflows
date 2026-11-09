import nextra from 'nextra'

// Set when the site is served from a subpath, e.g. a GitHub Pages project URL
// (/openmrs-contrib-gha-workflows); empty when served from a domain root.
const basePath = process.env.BASE_PATH || ''

const withNextra = nextra({
  defaultShowCopyCode: true,
  search: { codeblocks: false },
})

export default withNextra({
  // GitHub Pages serves static files only.
  output: 'export',
  images: { unoptimized: true },
  // Each route becomes a directory with an index.html, which Pages resolves without rewrites.
  trailingSlash: true,
  basePath,
  // Absolute asset URLs (e.g. favicons) are not basePath-rewritten, so components prefix them.
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
  reactStrictMode: true,
  poweredByHeader: false,
})
