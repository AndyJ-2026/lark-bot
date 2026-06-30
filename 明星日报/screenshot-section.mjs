#!/usr/bin/env node
/**
 * 用 CDP 连已开的 Chrome，对指定 DataWind 看板分栏的"指定文字所在 section"截整片图。
 *
 * 用法（环境变量驱动，避免参数解析复杂度）：
 *   CDP_PORT=56802 \
 *   TARGET_URL_RE='dashboard/39716' \
 *   NAV_URL='https://datawind.xiaoxiame.com/bi/#/dashboard/39716?appId=2&sheetId=<eftd-sheet>&snapshotId=6300' \
 *   SECTION_TEXT='异动下钻 - eFTD' \
 *   OUT=/Users/jaker/lark-bot/明星日报/exports/eftd-section.png \
 *   node screenshot-section.mjs
 */

import puppeteer from "/Users/jaker/datawind-picker/node_modules/puppeteer-core/lib/puppeteer/puppeteer-core.js";

const CDP_PORT = process.env.CDP_PORT || "56802";
const TARGET_URL_RE = new RegExp(process.env.TARGET_URL_RE || "datawind.*dashboard/39716");
const NAV_URL = process.env.NAV_URL || "";
const SECTION_TEXT = process.env.SECTION_TEXT || "异动下钻 - eFTD";
const OUT = process.env.OUT || "/tmp/section.png";
const WAIT_MS = Number(process.env.WAIT_MS || 6000);

async function main() {
  const browserWSEndpoint = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)
    .then((r) => r.json())
    .then((j) => j.webSocketDebuggerUrl);

  const browser = await puppeteer.connect({ browserWSEndpoint, defaultViewport: null });

  const pages = await browser.pages();
  let page = pages.find((p) => TARGET_URL_RE.test(p.url()));
  if (!page) {
    console.error(`未找到匹配的 tab (${TARGET_URL_RE})。在 Chrome 里先打开 DataWind 看板。`);
    process.exit(2);
  }

  if (NAV_URL && page.url() !== NAV_URL) {
    console.error(`导航到：${NAV_URL}`);
    await page.goto(NAV_URL, { waitUntil: "networkidle2", timeout: 60000 }).catch(() => {});
  }

  await page.bringToFront();
  await new Promise((r) => setTimeout(r, WAIT_MS));

  // 找包含目标文字的最近 section 容器（往上找父节点直到一个"看上去像区块"的卡片）
  const clip = await page.evaluate((text) => {
    function findEl() {
      const all = document.querySelectorAll("body *");
      for (const el of all) {
        if (el.children.length === 0 && el.textContent && el.textContent.includes(text)) return el;
      }
      return null;
    }
    const start = findEl();
    if (!start) return null;
    // 往上走找最近的"容器节点"：宽度 > 800
    let node = start;
    for (let i = 0; i < 12 && node; i++) {
      const r = node.getBoundingClientRect();
      if (r.width >= 900 && r.height >= 300) {
        // 往父节点再上一层，把章节标题+图表+表格都包进来
        node = node.parentElement || node;
        const rr = node.getBoundingClientRect();
        return { x: rr.x, y: rr.y, width: rr.width, height: rr.height };
      }
      node = node.parentElement;
    }
    return null;
  }, SECTION_TEXT);

  if (!clip) {
    console.error(`找不到包含「${SECTION_TEXT}」的容器`);
    process.exit(3);
  }
  console.error(`section clip: ${JSON.stringify(clip)}`);

  // 用 viewport 做兜底：若 clip 高度过大，限制到屏幕高度
  const viewport = page.viewport() || { width: 1440, height: 900 };
  const safeClip = {
    x: Math.max(0, Math.floor(clip.x)),
    y: Math.max(0, Math.floor(clip.y)),
    width: Math.min(Math.ceil(clip.width), viewport.width || 1920),
    height: Math.min(Math.ceil(clip.height), 1100),
  };

  await page.screenshot({ path: OUT, clip: safeClip });
  console.error(`saved → ${OUT}`);
  await browser.disconnect();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
