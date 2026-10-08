// Records the README demo GIF and screenshots from a running `plant-ai demo` (default http://127.0.0.1:8000).
//
//   node scripts/record_demo.mjs [url]
//
// Needs the docs-kit helpers (playwright-core, gifenc) from D:/Portfolio/tools/docs-kit or DOCS_KIT.
import { mkdirSync } from 'node:fs'

const KIT = process.env.DOCS_KIT || 'D:/Portfolio/tools/docs-kit'
const { launch, GifRecorder, clickVisibly, smoothScroll } = await import(`file:///${KIT}/gif.mjs`)

const url = process.argv[2] || 'http://127.0.0.1:8000'
const shots = 'docs/screenshots'
mkdirSync(shots, { recursive: true })

const { browser, page } = await launch({ width: 1280, height: 800 })
await page.goto(url, { waitUntil: 'networkidle' })
await page.waitForTimeout(2500)

// Still screenshots first (full page at a wider viewport).
await page.setViewportSize({ width: 1440, height: 900 })
await page.waitForTimeout(800)
await page.screenshot({ path: `${shots}/dashboard.png`, fullPage: true })
await page.locator('#overview').screenshot({ path: `${shots}/mimic.png` })
await page.locator('#incidents').screenshot({ path: `${shots}/incidents.png` })
await page.setViewportSize({ width: 1280, height: 800 })
await page.evaluate(() => window.scrollTo(0, 0))
await page.waitForTimeout(800)

const rec = new GifRecorder(page, { fps: 5 })
rec.start()
await page.waitForTimeout(2200)

// 1. Plant a blower failure from the training panel.
await smoothScroll(page, null, 1400, { steps: 18, delay: 35 })
await page.waitForTimeout(500)
await clickVisibly(page, page.locator('[data-fault="blower_fail"]'))
await page.waitForTimeout(1200)

// 2. Back to the mimic: the blower shows FAIL and dissolved oxygen turns red.
await smoothScroll(page, null, -1400, { steps: 18, delay: 35 })
await page.waitForTimeout(10000)

// 3. The alarm list: explain the new blower alarm.
await smoothScroll(page, null, 520, { steps: 12, delay: 35 })
await page.waitForTimeout(800)
const explain = page.locator('#alarmrows tr', { hasText: 'Blower B-301 commanded on' }).first().locator('[data-explain]')
await clickVisibly(page, explain)
await page.waitForFunction(() => document.querySelector('#asst-body .summary') !== null, null, { timeout: 60000 })
await page.waitForTimeout(600)

// 4. Read the answer: summary, checks, handover draft, citations and guard results.
await smoothScroll(page, null, 520, { steps: 16, delay: 40 })
await page.waitForTimeout(3500)
await smoothScroll(page, null, 300, { steps: 10, delay: 40 })
await page.waitForTimeout(3000)
await rec.stop()
const res = rec.save('docs/demo.gif', { dumpDir: process.env.GIF_FRAMES || null, every: 15 })
console.log('gif', res)

await page.setViewportSize({ width: 1440, height: 1000 })
await page.waitForTimeout(600)
await page.locator('#assistant').screenshot({ path: `${shots}/assistant.png` })
await page.locator('#alarms').screenshot({ path: `${shots}/alarms.png` })

// Reports open in their own pages.
const rp = await browser.newPage({ viewport: { width: 1100, height: 900 } })
await rp.goto(`${url}/reports/compliance`, { waitUntil: 'networkidle' })
await rp.screenshot({ path: `${shots}/compliance-report.png`, fullPage: true })
await rp.goto(`${url}/reports/shift?which=previous`, { waitUntil: 'networkidle' })
await rp.screenshot({ path: `${shots}/shift-report.png`, fullPage: true })
await browser.close()
console.log('screenshots saved in', shots)
