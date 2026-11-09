// Runs after `next build`. GitHub Pages branch deploys run Jekyll, which drops
// directories starting with an underscore (_next, _pagefind); an empty
// .nojekyll file turns that off.

import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

writeFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'out', '.nojekyll'), '')
