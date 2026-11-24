import {mkdtemp, writeFile, readFile, rm, access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const {capture} = await import(pathToFileURL(process.argv[2]));
for (const mode of ['stable', 'late-error', 'final-error', 'lost-focus', 'lost-overlay', 'lost-specimen', 'initial-abort', 'late-abort', 'between-cases', 'delayed-native-fonts', 'lost-native-fonts', 'final-ax-reset', 'stale-start']) {
 const output = await mkdtemp(join(tmpdir(), 'catalog-capture-loop-'));
 const log = console.log;
 try {
  const key = 'filter-field-320-light-focus';
  let shots = 0, axCalls = 0, finished = 0, navigated = false, initialAbortSent = false;
 let activeKey = key, accepted = false, boundarySent = false, nativeReset = false, staleSent = false;
 console.log = (...args) => {
  log(...args);
  if (String(args[0]).startsWith('Captured 1/2:')) accepted = true;
 };
  const page = {
   cdp: async method => {
    if (method === 'Runtime.evaluate') {
     let families = ['First', 'Second'];
     if (mode === 'delayed-native-fonts' && axCalls < 3) families = ['First'];
     if (mode === 'lost-native-fonts' && shots) families = ['First'];
     if (nativeReset) families = [];
     return {result: {value: {installed: true, families}}};
    }
    if (method === 'Accessibility.getFullAXTree') {
     axCalls++;
     if (mode === 'final-ax-reset') {
      try { await access(join(output, 'capture.pending.json')); nativeReset = true; }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
     }
     return {nodes: [
      {ignored: false, name: {value: shots && mode === 'lost-specimen' ? 'other' : 'Catalog specimen ' + activeKey}},
      {ignored: false, name: {value: shots && mode === 'lost-overlay' ? '' : 'Retry'}, role: {value: 'button'}},
      {ignored: false, role: {value: 'textbox'}, properties: [
       {name: 'focused', value: {value: !(shots && mode === 'lost-focus')}}
      ]}
     ]};
    }
   },
   events: async () => {
    if (mode === 'stale-start' && !staleSent) {
     staleSent = true;
     assert.equal(activeKey, null);
     return [{method: 'Runtime.exceptionThrown', params: {exceptionDetails: {text: 'old stopped page'}}}];
    }
    if (mode === 'between-cases' && accepted && !boundarySent) {
     boundarySent = true;
     return [{method: 'Network.loadingFailed', params: {
      type: 'Document', canceled: true, errorText: 'net::ERR_ABORTED'
     }}];
    }
    if ((navigated && !initialAbortSent && mode === 'initial-abort') || (shots >= 1 && mode === 'late-abort')) {
     initialAbortSent = true;
     return [{method: 'Network.loadingFailed', params: {
      type: 'Document', canceled: true, errorText: 'net::ERR_ABORTED'
     }}];
    }
    if ((shots >= 1 && mode === 'late-error') || (shots >= 3 && mode === 'final-error')) {
     return [{method: 'Runtime.exceptionThrown', params: {exceptionDetails: {text: 'late failure'}}}];
    }
    return [];
   },
   goto: async url => { navigated = url.startsWith('http:'); activeKey = new URL(url).searchParams.get('case'); }, waitForFunction: async () => {}, evaluate: async () => 'synthetic',
   screenshot: async ({path}) => {
    if (mode === 'delayed-native-fonts') assert.ok(axCalls >= 4);
    shots++; await writeFile(path, 'identical frame'); }
  };
  await writeFile(join(output, 'capture.json'), JSON.stringify({status: 'incomplete'}));
  const run = () => capture({origin: 'http://127.0.0.1:1', output, digest: 'synthetic', fonts: (mode.includes('native-fonts') || mode === 'final-ax-reset') ? ['First', 'Second'] : [],
   cases: mode === 'between-cases' ? [
    {key, width: 320, focus: true, overlay: true},
    {key: 'filter-field-768-light-focus', width: 768, focus: true, overlay: true}
   ] : [{key, width: 320, focus: true, overlay: true}]},
   async () => ({spaceId: 'synthetic', page: () => page, finish: async () => {finished++;}}));
  if (mode === 'stable' || mode === 'initial-abort' || mode === 'delayed-native-fonts' || mode === 'stale-start') {
   await run();
   const proof = JSON.parse(await readFile(join(output, 'capture.json'), 'utf8'));
   assert.equal(proof.status, 'complete');
   assert.equal(proof.images.length, 1);
   assert.deepEqual(proof.images[0].font_families, mode === "delayed-native-fonts" ? ["First", "Second"] : []);
   assert.match(proof.images[0].sha256, /^[a-f0-9]{64}$/);
   assert.equal(shots, 3);
   assert.ok(axCalls >= 8);
   assert.equal(finished, 1);
  } else {
   await assert.rejects(run, (mode.includes('error') || mode === 'late-abort' || mode === 'between-cases') ? /resource\/runtime failure/ : /state lost/);
   assert.equal(JSON.parse(await readFile(join(output, 'capture.json'), 'utf8')).status, 'incomplete');
   assert.equal(finished, 0);
  }
  console.log(mode + ' full-loop check passed');
 } finally { console.log = log; await rm(output, {recursive: true, force: true}); }
}
