export function installFontObserver(scope) {
 const proof = {installed: false, families: [], error: null};
 Object.defineProperty(scope, '__catalogFontProof', {value: proof, configurable: true});
 let current = scope.flutterCanvasKit, active = null;
 function arm(kit) {
  const provider = kit?.TypefaceFontProvider;
  const make = provider?.Make;
  const register = provider?.prototype?.registerFont;
  if (typeof make !== 'function' || typeof register !== 'function') {
   proof.error = 'Pinned CanvasKit font-provider API is unavailable';
   return;
  }
  provider.Make = function (...args) {
   const result = make.apply(this, args);
   active = result;
   proof.families = [];
   return result;
  };
  provider.prototype.registerFont = function (bytes, family) {
   const result = register.call(this, bytes, family);
   if (this === active && result !== null && !proof.families.includes(family)) {
    proof.families.push(family);
   }
   return result;
  };
  proof.installed = true;
 }
 if (current) arm(current);
 Object.defineProperty(scope, 'flutterCanvasKit', {
  configurable: true,
  get() { return current; },
  set(value) {
   if (value === current) return;
   current = value;
   active = null;
   proof.installed = false;
   proof.families = [];
   proof.error = null;
   arm(value);
  }
 });
}
