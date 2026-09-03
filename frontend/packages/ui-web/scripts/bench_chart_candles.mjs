#!/usr/bin/env node
/**
 * CH-0 fork-candidate benchmark: renders 100k synthetic OHLCV candles in a real
 * headless Chrome page and measures initial render + pan/zoom frame time (p50/p95)
 * for each candidate library. See docs/design/CHART_ENGINE_FORK_EVAL.md for results.
 *
 * CH-0 is a documentation/eval leaf only: this script must not add dependencies to
 * frontend/package.json and must not vendor any candidate into the repo (that is
 * CH-1's job, gated on CA fork approval). To actually execute a candidate, install
 * it plus puppeteer-core/esbuild in a SCRATCH directory outside this repo, e.g.:
 *
 *   mkdir /tmp/ch0-bench && cd /tmp/ch0-bench && npm init -y
 *   npm install puppeteer-core esbuild \
 *     klinecharts lightweight-charts react react-dom react-financial-charts night-vision
 *   node <repo>/frontend/packages/ui-web/scripts/bench_chart_candles.mjs --lib=klinecharts
 *   # --lib= one of: klinecharts | lightweight-charts | react-financial-charts | night-vision | all (default)
 *   # --candles=100000 (default) --steps=60 (per zoom/pan phase, default 60)
 *
 * If esbuild/puppeteer-core/the candidate itself are not resolvable (the normal case
 * in this repo, since nothing is installed by this leaf), the candidate is reported
 * as { status: "UNMEASURED", reason: "<actual error>" } — never a fabricated number.
 */

import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import path from 'node:path'

const require = createRequire(import.meta.url)
// Resolve npm packages relative to the CALLER's cwd (the scratch dir where the
// candidate libs were installed), not this script's own location in the repo —
// this script has no node_modules of its own (CH-0 adds no dependencies).
const cwdRequire = createRequire(path.join(process.cwd(), 'package.json'))
async function importFromCwd(specifier) {
  const resolved = cwdRequire.resolve(specifier)
  return import(pathToFileURL(resolved).href)
}

// ---------------------------------------------------------------------------
// Deterministic synthetic candle generator (seeded PRNG, no Date.now/Math.random)
// ---------------------------------------------------------------------------
function mulberry32(seed) {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function genCandles(n, seed = 42) {
  const rnd = mulberry32(seed)
  const candles = []
  let price = 100
  const startTime = 1700000000 // fixed epoch seconds
  for (let i = 0; i < n; i++) {
    const drift = (rnd() - 0.5) * 0.6
    const open = price
    const close = Math.max(0.01, open + drift)
    const high = Math.max(open, close) + rnd() * 0.4
    const low = Math.min(open, close) - rnd() * 0.4
    const volume = 10 + rnd() * 500
    candles.push({ time: startTime + i * 60, open, high, low, close, volume })
    price = close
  }
  return candles
}

function percentile(arr, p) {
  const s = [...arr].sort((a, b) => a - b)
  const idx = Math.min(s.length - 1, Math.floor((p / 100) * s.length))
  return Math.round(s[idx] * 1000) / 1000
}

// ---------------------------------------------------------------------------
// Per-candidate browser-side entry sources. Each defines window.runBench(candles, id)
// -> Promise<{ initialRenderMs, frameTimesMs?: number[], callTimesMs?: number[],
//              panZoomUnmeasuredReason?: string }>
// Methodology (same for every candidate): render N candles into a 1200x600 container,
// then run 2*steps interaction calls (zoom phase: shrink visible range from full to
// ~200 bars; pan phase: slide a ~200-bar window across the dataset), one rAF apart.
// frameTimesMs = wall time between consecutive rAF callbacks (interaction smoothness,
// vsync-bound). callTimesMs = synchronous time spent inside the API call itself
// (compute cost, not vsync-bound) — reported when the candidate's API is synchronous.
// ---------------------------------------------------------------------------
const ENTRIES = {
  klinecharts: (steps) => `
import { init, dispose } from 'klinecharts'
window.runBench = async function (candles, containerId) {
  const data = candles.map(c => ({ timestamp: c.time * 1000, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume }))
  const raf = () => new Promise(r => requestAnimationFrame(r))
  const t0 = performance.now()
  const chart = init(containerId)
  chart.setDataLoader({ getBars: ({ callback }) => callback(data) })
  chart.setSymbol({ ticker: 'BENCH', pricePrecision: 2, volumePrecision: 0 })
  chart.setPeriod({ span: 1, type: 'minute' })
  await raf(); await raf()
  const initialRenderMs = performance.now() - t0
  const frameTimes = [], callTimes = []
  let prev = performance.now()
  chart.setBarSpace(1)
  for (let i = 0; i < ${steps}; i++) {
    const space = Math.max(0.2, 6 - i * (6 / ${steps}))
    const cs = performance.now(); chart.setBarSpace(space); callTimes.push(performance.now() - cs)
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  chart.setBarSpace(6)
  for (let i = 0; i < ${steps}; i++) {
    const cs = performance.now(); chart.scrollByDistance(-400, 0); callTimes.push(performance.now() - cs)
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  dispose(containerId)
  return { initialRenderMs, frameTimesMs: frameTimes.slice(1), callTimesMs: callTimes }
}`,

  'lightweight-charts': (steps) => `
import { createChart, CandlestickSeries } from 'lightweight-charts'
window.runBench = async function (candles, containerId) {
  const data = candles.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close }))
  const container = document.getElementById(containerId)
  const raf = () => new Promise(r => requestAnimationFrame(r))
  const t0 = performance.now()
  const chart = createChart(container, { width: container.clientWidth, height: container.clientHeight })
  const series = chart.addSeries(CandlestickSeries)
  series.setData(data)
  chart.timeScale().fitContent()
  await raf(); await raf()
  const initialRenderMs = performance.now() - t0
  const n = data.length
  const frameTimes = [], callTimes = []
  let prev = performance.now()
  for (let i = 0; i < ${steps}; i++) {
    const half = Math.max(100, (n / 2) * (1 - i / ${steps}))
    const cs = performance.now(); chart.timeScale().setVisibleLogicalRange({ from: n / 2 - half, to: n / 2 + half }); callTimes.push(performance.now() - cs)
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  for (let i = 0; i < ${steps}; i++) {
    const center = 150 + (i / ${steps}) * (n - 300)
    const cs = performance.now(); chart.timeScale().setVisibleLogicalRange({ from: center - 100, to: center + 100 }); callTimes.push(performance.now() - cs)
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  chart.remove()
  return { initialRenderMs, frameTimesMs: frameTimes.slice(1), callTimesMs: callTimes }
}`,

  'react-financial-charts': (steps) => `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { ChartCanvas, Chart, XAxis, YAxis, CandlestickSeries, discontinuousTimeScaleProviderBuilder } from 'react-financial-charts'
const h = React.createElement
window.runBench = async function (candles, containerId) {
  const raw = candles.map(c => ({ ...c, date: new Date(c.time * 1000) }))
  const scaleProvider = discontinuousTimeScaleProviderBuilder().inputDateAccessor(d => d.date)
  const { data, xScale, xAccessor, displayXAccessor } = scaleProvider(raw)
  const n = data.length
  const container = document.getElementById(containerId)
  const width = container.clientWidth, height = container.clientHeight
  const root = createRoot(container)
  const raf = () => new Promise(r => requestAnimationFrame(r))
  function render(xExtents) {
    root.render(h(ChartCanvas, { height, width, ratio: 1, margin: { left: 40, right: 40, top: 10, bottom: 30 }, data, seriesName: 'bench', xScale, xAccessor, displayXAccessor, xExtents },
      h(Chart, { id: 1, yExtents: d => [d.high, d.low] }, h(XAxis, null), h(YAxis, null), h(CandlestickSeries, null))))
  }
  const t0 = performance.now()
  render([xAccessor(data[0]), xAccessor(data[n - 1])])
  await raf(); await raf()
  const initialRenderMs = performance.now() - t0
  const frameTimes = []
  let prev = performance.now()
  for (let i = 0; i < ${steps}; i++) {
    const half = Math.max(100, (n / 2) * (1 - i / ${steps}))
    const lo = Math.max(0, Math.floor(n / 2 - half)), hi = Math.min(n - 1, Math.ceil(n / 2 + half))
    render([xAccessor(data[lo]), xAccessor(data[hi])])
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  for (let i = 0; i < ${steps}; i++) {
    const center = Math.floor(150 + (i / ${steps}) * (n - 300))
    const lo = Math.max(0, center - 100), hi = Math.min(n - 1, center + 100)
    render([xAccessor(data[lo]), xAccessor(data[hi])])
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  root.unmount()
  return { initialRenderMs, frameTimesMs: frameTimes.slice(1) }
}`,

  'night-vision': (steps) => `
import { NightVision } from 'night-vision'
window.runBench = async function (candles, containerId) {
  const ohlcv = candles.map(c => [c.time * 1000, c.open, c.high, c.low, c.close, c.volume])
  const raf = () => new Promise(r => requestAnimationFrame(r))
  const t0 = performance.now()
  const chart = new NightVision(containerId, {
    data: { panes: [{ overlays: [{ name: 'bench', type: 'Candles', main: true, data: ohlcv }] }] },
    autoResize: false,
    width: document.getElementById(containerId).clientWidth,
    height: document.getElementById(containerId).clientHeight,
  })
  await raf(); await raf()
  const initialRenderMs = performance.now() - t0
  // 'range' is an undocumented (not in README) get/set accessor found by reading
  // dist/index-*.js — delegates to internal chart.setRange()/getRange(), units [tMs, tMs].
  const tFirst = ohlcv[0][0], tLast = ohlcv[ohlcv.length - 1][0], full = tLast - tFirst
  const frameTimes = [], callTimes = []
  let prev = performance.now()
  for (let i = 0; i < ${steps}; i++) {
    const half = Math.max(full * 0.001, (full / 2) * (1 - i / ${steps}))
    const cs = performance.now(); chart.range = [tFirst + full / 2 - half, tFirst + full / 2 + half]; callTimes.push(performance.now() - cs)
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  const windowSpan = full * 0.002
  for (let i = 0; i < ${steps}; i++) {
    const center = tFirst + windowSpan / 2 + (i / ${steps}) * (full - windowSpan)
    const cs = performance.now(); chart.range = [center - windowSpan / 2, center + windowSpan / 2]; callTimes.push(performance.now() - cs)
    await raf(); const now = performance.now(); frameTimes.push(now - prev); prev = now
  }
  return { initialRenderMs, frameTimesMs: frameTimes.slice(1), callTimesMs: callTimes }
}`,
}

function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH
  const candidates = [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium-browser',
  ]
  const fs = require('fs')
  return candidates.find((p) => fs.existsSync(p)) ?? null
}

async function benchOne(lib, candles, steps) {
  let esbuild, puppeteer
  try {
    esbuild = (await importFromCwd('esbuild')).default
  } catch (err) {
    return { status: 'UNMEASURED', reason: `esbuild not installed in cwd ${process.cwd()}: ${err.message.split('\n')[0]}` }
  }
  try {
    puppeteer = (await importFromCwd('puppeteer-core')).default
  } catch (err) {
    return { status: 'UNMEASURED', reason: `puppeteer-core not installed in cwd ${process.cwd()}: ${err.message.split('\n')[0]}` }
  }
  const chromePath = findChrome()
  if (!chromePath) {
    return { status: 'UNMEASURED', reason: 'no local Chrome/Edge executable found (set CHROME_PATH)' }
  }

  let bundle
  try {
    const built = await esbuild.build({
      stdin: { contents: ENTRIES[lib](steps), resolveDir: process.cwd(), loader: 'js' },
      bundle: true,
      format: 'iife',
      platform: 'browser',
      write: false,
      logLevel: 'silent',
    })
    bundle = built.outputFiles[0].text
  } catch (err) {
    return { status: 'UNMEASURED', reason: `bundle failed (candidate likely not installed): ${err.message.split('\n')[0]}` }
  }

  const browser = await puppeteer.launch({ executablePath: chromePath, headless: true })
  try {
    const page = await browser.newPage()
    await page.setViewport({ width: 1280, height: 720 })
    await page.setContent('<!doctype html><html><body><div id="chart" style="width:1200px;height:600px"></div></body></html>')
    await page.addScriptTag({ content: bundle })
    await page.waitForFunction('typeof window.runBench === "function"', { timeout: 5000 })
    const r = await page.evaluate((c) => window.runBench(c, 'chart'), candles)
    const out = { status: 'MEASURED', candleCount: candles.length, initialRenderMs: Math.round(r.initialRenderMs * 100) / 100 }
    if (r.frameTimesMs) {
      out.panZoomFrameMsP50 = percentile(r.frameTimesMs, 50)
      out.panZoomFrameMsP95 = percentile(r.frameTimesMs, 95)
      out.panZoomSampleCount = r.frameTimesMs.length
      if (r.callTimesMs) {
        out.panZoomCallMsP50 = percentile(r.callTimesMs, 50)
        out.panZoomCallMsP95 = percentile(r.callTimesMs, 95)
      }
    }
    return out
  } catch (err) {
    return { status: 'UNMEASURED', reason: err.message.split('\n')[0] }
  } finally {
    await browser.close()
  }
}

async function main() {
  const args = Object.fromEntries(
    process.argv.slice(2).map((a) => {
      const [k, v] = a.replace(/^--/, '').split('=')
      return [k, v ?? true]
    }),
  )
  const libArg = args.lib ?? 'all'
  const n = Number(args.candles ?? 100000)
  const steps = Number(args.steps ?? 60)
  const libs = libArg === 'all' ? Object.keys(ENTRIES) : [libArg]

  const candles = genCandles(n, 42)
  const results = {}
  for (const lib of libs) {
    if (!ENTRIES[lib]) {
      results[lib] = { status: 'UNMEASURED', reason: `unknown candidate '${lib}'` }
      continue
    }
    results[lib] = await benchOne(lib, candles, steps)
  }
  console.log(JSON.stringify(results, null, 2))
}

main()
