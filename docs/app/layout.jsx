import { Footer, LastUpdated, Layout, Navbar } from 'nextra-theme-docs'
import { Head, Search } from 'nextra/components'
import { getPageMap } from 'nextra/page-map'
import NavLogo from '../components/nav-logo'
import '../styles.css'

export const metadata = {
  title: {
    default: 'OpenMRS GitHub Actions Workflows',
    template: '%s | OpenMRS GitHub Actions Workflows',
  },
  description: 'Reusable GitHub Actions workflows for building and releasing OpenMRS modules.',
}

export default async function RootLayout({ children }) {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || ''

  return (
    <html lang="en" dir="ltr" suppressHydrationWarning>
      <Head>
        <link rel="icon" href={`${basePath}/favicon.ico`} sizes="any" />
        <link rel="icon" type="image/png" sizes="32x32" href={`${basePath}/favicon-32x32.png`} />
        <link rel="icon" type="image/png" sizes="16x16" href={`${basePath}/favicon-16x16.png`} />
        <link rel="apple-touch-icon" sizes="180x180" href={`${basePath}/apple-icon-180x180.png`} />
      </Head>
      <body>
        <Layout
          navbar={
            <Navbar
              logo={<NavLogo />}
              projectLink="https://github.com/openmrs/openmrs-contrib-gha-workflows"
            >
              <a href="https://talk.openmrs.org" target="_blank" rel="noreferrer" className="nx-p-2">
                Talk
              </a>
            </Navbar>
          }
          footer={<Footer>© {new Date().getFullYear()} OpenMRS</Footer>}
          docsRepositoryBase="https://github.com/openmrs/openmrs-contrib-gha-workflows/blob/main/docs"
          sidebar={{ autoCollapse: true, defaultMenuCollapseLevel: 1 }}
          toc={{ backToTop: 'Scroll to top', float: true }}
          lastUpdated={<LastUpdated>Last updated on</LastUpdated>}
          search={<Search />}
          nextThemes={{ defaultTheme: 'system' }}
          pageMap={await getPageMap()}
        >
          {children}
        </Layout>
      </body>
    </html>
  )
}
