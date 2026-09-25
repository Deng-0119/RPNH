/** Browser-only locale. No network, runtime state, Registry or translation service. */
import { EN } from './messages.mjs';
export const LANGUAGES = Object.freeze(['en', 'zh-CN']);
export const LANGUAGE_KEY = 'rpnh.viewer.language';
function browserStorage() { try { return globalThis.localStorage; } catch { return null; } }
export function readLanguage(storage = browserStorage()) {
  try { const value = storage?.getItem(LANGUAGE_KEY); return LANGUAGES.includes(value) ? value : 'en'; }
  catch { return 'en'; }
}
let language = readLanguage();
export function getLanguage() { return language; }
export function setLanguage(value, storage = browserStorage()) {
  if (!LANGUAGES.includes(value)) throw new RangeError('Unsupported interface language');
  language = value;
  try { storage?.setItem(LANGUAGE_KEY, value); } catch { /* Private/blocked storage: keep session preference. */ }
  return language;
}
export function t(key, ...values) {
  if (!Object.hasOwn(EN, key)) throw new Error(`Missing UI translation: ${key}`);
  const template = language === 'zh-CN' ? key : EN[key];
  // Interpolated run data is never translated or evaluated, even if it matches a UI key.
  return template.replace(/\{(\d+)\}/g, (whole, index) => index < values.length ? String(values[index]) : whole);
}
export function messageError(key, ...values) {
  const error = new Error();
  Object.defineProperty(error, 'message', { configurable: true, get: () => t(key, ...values) });
  return error;
}
export function applyLanguage(root = document) {
  root.documentElement.lang = language;
  for (const node of root.querySelectorAll('[data-i18n]')) node.textContent = t(node.dataset.i18n);
  for (const attr of ['aria-label', 'placeholder']) {
    for (const node of root.querySelectorAll(`[data-i18n-${attr}]`)) node.setAttribute(attr, t(node.getAttribute(`data-i18n-${attr}`)));
  }
}
