import {readFile, writeFile, rename} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {installFontObserver} from './catalog_font_observer.mjs';

function strings(value) {
 if (typeof value === 'string') return [value];
 if (value && typeof value === 'object') return Object.values(value).flatMap(strings);
 return [];
}

function objects(value) {
 if (!value || typeof value !== 'object') return [];
 return [value, ...Object.values(value).flatMap(objects)];
}

export function readiness(specimen, fonts) {
 let loaded = new Set();
 return {
  observe(events, navigation = false) {
   for (const event of objects(events)) {
    if (navigation && event.method === 'Network.loadingFailed' &&
     event.params?.type === 'Document' && event.params?.canceled === true &&
     event.params?.errorText === 'net::ERR_ABORTED') continue;
    const details = event.params?.exceptionDetails;
    const stack = details?.stackTrace;
    const frames = stack?.callFrames;
    if (event.method === 'Runtime.exceptionThrown' &&
     details?.url?.startsWith('chrome-extension://') && stack &&
     !('parent' in stack) && !('parentId' in stack) && frames?.length &&
     frames.every(frame => frame.url?.startsWith('chrome-extension://'))) continue;
    if (event.method === 'Network.loadingFailed' || event.method === 'Runtime.exceptionThrown' ||
     (event.method === 'Network.responseReceived' && event.params?.response?.status >= 400)) {
     throw new Error('Renderer resource/runtime failure: ' + JSON.stringify(event));
    }
   }
   for (const message of strings(events)) {
    if (/Error loading font|Failed to (?:load|parse) font|Unable to load asset|ImageCodecException/.test(message)) {
     throw new Error('Flutter font loading failed: ' + message);
    }
   }
  },
  fonts(proof) {
   if (proof?.error) throw new Error('Native font observer: ' + proof.error);
   loaded = new Set(proof?.installed ? proof.families.filter(name => fonts.includes(name)) : []);
  },
  ready(tree) {
   if (loaded.size !== fonts.length) return false;
   const nodes = tree.nodes.filter(n => !n.ignored);
   const names = nodes.map(n => n.name?.value || '');
   if (!names.some(n => n.includes('Catalog specimen ' + specimen.key))) return false;
   if (specimen.overlay && !names.some(n => n.includes('Retry'))) return false;
   if (specimen.focus && !nodes.some(n =>
    ['button', 'textbox', 'checkbox', 'switch'].includes(n.role?.value) &&
    n.properties?.some(p => p.name === 'focused' && p.value?.value === true))) return false;
   return true;
  },
  get loaded() { return loaded.size; },
  get families() { return [...loaded].sort(); }
 };
}

export async function capture(settings, taskSpace) {
 const task = await taskSpace('Design system catalog captures');
 console.log({spaceId: task.spaceId});
 const page = task.page('p1');
 await page.goto('about:blank');
 await page.cdp('Runtime.disable');
 await page.cdp('Network.disable');
 await page.events();
 await page.cdp('egoAutomated.disable');
 await page.cdp('Runtime.enable');
 await page.cdp('Page.addScriptToEvaluateOnNewDocument', {
  source: '(' + installFontObserver.toString() + ')(window);'
 });
 await page.cdp('Network.enable');
 await page.cdp('Network.setBlockedURLs', {urls: ['https://*']});
 const browser = await page.evaluate(() => navigator.userAgent);
 const images = [];
 let validateLast = async () => {};
 for (const specimen of settings.cases) {
  readiness(specimen, []).observe(await page.events());
  const state = readiness(specimen, settings.fonts);
  await page.cdp('Emulation.setDeviceMetricsOverride', {
   width: specimen.width, height: 1000, deviceScaleFactor: 1, mobile: false
  });
  await page.goto(settings.origin + '/?case=' + encodeURIComponent(specimen.key));
  await page.waitForFunction(expected => location.origin === expected.origin &&
   document.title === expected.key && !!document.querySelector('flt-glass-pane'),
   {origin: settings.origin, key: specimen.key}, {timeout: 15000});
  await page.evaluate(() => {
   function find(root) {
    const placeholder = root.querySelector('flt-semantics-placeholder');
    if (placeholder) return placeholder;
    for (const element of root.querySelectorAll('*')) {
     if (element.shadowRoot) {
      const nested = find(element.shadowRoot);
      if (nested) return nested;
     }
    }
   }
   find(document)?.click();
  });
  let lastNative = null;
  async function current(navigation = false) {
   state.observe(await page.events(), navigation);
   const tree = await page.cdp('Accessibility.getFullAXTree');
   const native = await page.cdp('Runtime.evaluate', {
    expression: 'window.__catalogFontProof', returnByValue: true
   });
   lastNative = native.result?.value ?? null;
   state.fonts(lastNative);
   state.observe(await page.events(), navigation);
   return state.ready(tree);
  }
  async function requireCurrent() {
   if (!await current()) throw new Error('Specimen state lost during capture: ' + specimen.key);
  }
  validateLast = requireCurrent;
  const end = Date.now() + 20000;
  while (true) {
   if (await current(true)) break;
   if (Date.now() >= end) {
    throw new Error('Specimen/fonts/focus/overlay not ready: ' + specimen.key +
     ' (' + state.loaded + '/' + settings.fonts.length + ' Flutter fonts loaded); native=' +
     JSON.stringify(lastNative));
   }
   await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  }
  let previous = '', identical = 0, imageHash = '';
  const frame = settings.output + '/' + specimen.key + '.frame.png';
  while (identical < 2) {
   if (Date.now() >= end) throw new Error('Specimen did not settle: ' + specimen.key);
   await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
   await requireCurrent();
   await page.screenshot({path: frame});
   imageHash = createHash('sha256').update(await readFile(frame)).digest('hex');
   await requireCurrent();
   identical = imageHash === previous ? identical + 1 : 0;
   previous = imageHash;
  }
  const name = specimen.key + '.png';
  await rename(frame, settings.output + '/' + name);
  await requireCurrent();
  images.push({name, sha256: imageHash, font_families: state.families});
  console.log('Captured ' + images.length + '/' + settings.cases.length + ': ' + specimen.key);
 }
 const manifest = {
  status: 'complete', renderer_tree_sha256: settings.digest, browser,
  cases: settings.cases.map(c => c.key), images,
  note: 'Local captures require visual review; no device certification.'
 };
 await writeFile(settings.output + '/capture.pending.json', JSON.stringify(manifest, null, 2) + '\n');
 await validateLast();
 await rename(settings.output + '/capture.pending.json', settings.output + '/capture.json');
 await task.finish({keep: []});
}
