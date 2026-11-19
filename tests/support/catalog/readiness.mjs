import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {readiness} = await import(pathToFileURL(process.argv[2]));
const specimen = {key: 'filter-field-320-light-focus', focus: true, overlay: false};
const fonts = ['fonts/first.ttf?v=1', 'fonts/second.ttf?v=2'];
const node = (name, role = 'generic', focused = false) => ({
 ignored: false, name: {value: name}, role: {value: role},
 properties: [{name: 'focused', value: {value: focused}}]
});
const tree = {nodes: [node('Catalog specimen ' + specimen.key), node('Search', 'textbox', true)]};
const state = readiness(specimen, fonts);
assert.equal(state.ready(tree), false);
state.observe([{method: 'Runtime.consoleAPICalled', params: {args: [{value:
 'Load font from URL: http://127.0.0.1/assets/' + fonts[0]}]}}]);
assert.equal(state.ready(tree), false);
state.fonts({installed: true, families: [fonts[0]]});
assert.equal(state.ready(tree), false);
state.fonts({installed: true, families: fonts});
assert.equal(state.ready({nodes: tree.nodes.slice(0, 1)}), false);
assert.equal(state.ready({nodes: tree.nodes.slice(1)}), false);
assert.equal(state.ready(tree), true);
assert.throws(() => readiness(specimen, fonts).observe([{params: {args: [{value:
 'Error loading font first: missing asset'}]}}]), /font loading failed/);
const overlay = readiness({...specimen, focus: false, overlay: true}, []);
assert.equal(overlay.ready({nodes: tree.nodes.slice(0, 1)}), false);
assert.equal(overlay.ready({nodes: [...tree.nodes, node('Retry', 'button')]}), true);
for (const event of [
 {method: 'Network.loadingFailed', params: {errorText: 'Failed'}},
 {method: 'Runtime.exceptionThrown', params: {}},
 {method: 'Network.responseReceived', params: {response: {status: 404}}}
]) assert.throws(() => readiness(specimen, fonts).observe([event]), /resource\/runtime failure/);
console.log('Delayed Flutter font completion, specimen, focus and overlay checks passed');

const isolated = {method: 'Runtime.exceptionThrown', params: {exceptionDetails: {
 url: 'chrome-extension://fixture/script.js', stackTrace: {callFrames: [
  {url: 'chrome-extension://fixture/script.js'}
 ]}
}}};
assert.doesNotThrow(() => readiness(specimen, fonts).observe([isolated]));
const mixed = structuredClone(isolated);
mixed.params.exceptionDetails.stackTrace.callFrames.push({url: 'http://127.0.0.1/main.dart.js'});
assert.throws(() => readiness(specimen, fonts).observe([mixed]), /resource\/runtime failure/);
const pageFailure = structuredClone(isolated);
pageFailure.params.exceptionDetails.url = 'http://127.0.0.1/main.dart.js';
assert.throws(() => readiness(specimen, fonts).observe([pageFailure]), /resource\/runtime failure/);

for (const ancestry of [
 {parent: {callFrames: [{url: 'http://127.0.0.1/main.dart.js'}]}},
 {parentId: {id: 'unresolved'}},
 {parent: {callFrames: [{}]}},
 {parent: null},
 {parentId: null},
]) {
 const unknown = structuredClone(isolated);
 Object.assign(unknown.params.exceptionDetails.stackTrace, ancestry);
 assert.throws(() => readiness(specimen, fonts).observe([unknown]), /resource\/runtime failure/);
}

const abortedDocument = {method: 'Network.loadingFailed', params: {
 type: 'Document', canceled: true, errorText: 'net::ERR_ABORTED'
}};
assert.doesNotThrow(() => readiness(specimen, fonts).observe([abortedDocument], true));
assert.throws(() => readiness(specimen, fonts).observe([abortedDocument]), /resource\/runtime failure/);
for (const params of [
 {...abortedDocument.params, type: 'Font'},
 {...abortedDocument.params, canceled: false},
 {...abortedDocument.params, errorText: 'net::ERR_FAILED'},
]) assert.throws(() => readiness(specimen, fonts).observe([
 {method: 'Network.loadingFailed', params}
], true), /resource\/runtime failure/);

const {installFontObserver} = await import(new URL('./catalog_font_observer.mjs', pathToFileURL(process.argv[2])));
const scope = {};
installFontObserver(scope);
assert.equal(scope.__catalogFontProof.installed, false);
class Provider {
 registerFont(bytes, family) {
  assert.equal(this instanceof Provider, true);
  if (bytes === 'bad') return null;
  if (bytes === 'throw') throw new Error('decode failed');
  this.last = [bytes, family];
  return undefined;
 }
 static Make() { return new Provider(); }
}
scope.flutterCanvasKit = {TypefaceFontProvider: Provider};
assert.equal(scope.__catalogFontProof.installed, true);
const first = Provider.Make();
first.registerFont('valid', fonts[0]);
state.fonts(scope.__catalogFontProof);
assert.equal(state.ready(tree), false);
first.registerFont('bad', fonts[1]);
state.fonts(scope.__catalogFontProof);
assert.equal(state.ready(tree), false);
assert.throws(() => first.registerFont('throw', fonts[1]), /decode failed/);
first.registerFont('valid', fonts[1]);
assert.deepEqual(first.last, ['valid', fonts[1]]);
state.fonts(scope.__catalogFontProof);
assert.equal(state.ready(tree), true);
Provider.Make();
state.fonts(scope.__catalogFontProof);
assert.equal(state.ready(tree), false);
first.registerFont('valid', fonts[0]);
assert.equal(scope.__catalogFontProof.families.length, 0);
const absent = {};
installFontObserver(absent);
absent.flutterCanvasKit = {};
assert.throws(() => state.fonts(absent.__catalogFontProof), /API is unavailable/);
console.log('Native registration completion, failed decode, reset and original-call invariants passed');

class Replacement extends Provider {
 static Make() { return new Replacement(); }
}
scope.flutterCanvasKit = {TypefaceFontProvider: Replacement};
assert.equal(scope.__catalogFontProof.installed, true);
assert.equal(scope.__catalogFontProof.families.length, 0);
state.fonts(scope.__catalogFontProof);
assert.equal(state.ready(tree), false);
first.registerFont('valid', fonts[1]);
assert.equal(scope.__catalogFontProof.families.length, 0);
const replacement = Replacement.Make();
for (const font of fonts) replacement.registerFont('valid', font);
state.fonts(scope.__catalogFontProof);
assert.equal(state.ready(tree), true);
