/* ma_gui/web/eslint.config.js — minimális ESLint (flat config) a felület forrásaira.
 *
 * Futtatás (a repó metaanalizis-asszisztens/ mappájából):
 *   node /opt/node22/lib/node_modules/eslint/bin/eslint.js -c ma_gui/web/eslint.config.js ma_gui/web/src
 *
 * ES2019, klasszikus szkript (egyetlen inline <script>, nincs import/export), böngésző-globálisok.
 * A szabályok szándékosan kevesek: no-undef, no-unused-vars, eqeqeq (+ a nyelvi alapellenőrzések). A
 * biztonsági és számhűségi tiltásokat (innerHTML, toFixed, Math.* a geom.js-en kívül, böngészőtároló,
 * külső URL …) a build_gui.py lintje kényszeríti ki, nem ez a fájl.
 */
'use strict';

// Kézzel felsorolt böngésző-globálisok (a 'globals' csomag nélkül — nincs külső függőség).
const BROWSER = [
  'window', 'document', 'navigator', 'location', 'history', 'console', 'self',
  'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame',
  'queueMicrotask', 'getComputedStyle', 'matchMedia', 'getSelection', 'performance', 'crypto',
  'fetch', 'Headers', 'Request', 'Response', 'AbortController', 'AbortSignal', 'URL', 'URLSearchParams',
  'Blob', 'File', 'FileReader', 'FormData', 'BroadcastChannel', 'TextEncoder', 'TextDecoder',
  'Node', 'Element', 'HTMLElement', 'SVGElement', 'Text', 'NodeFilter', 'DocumentFragment',
  'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'FocusEvent', 'ClipboardEvent', 'DOMException',
  'MutationObserver', 'ResizeObserver', 'IntersectionObserver', 'CSS', 'Intl',
  'localStorage', 'sessionStorage', 'alert', 'confirm', 'prompt'
];
const globals = {};
BROWSER.forEach((g) => { globals[g] = 'readonly'; });

module.exports = [
  {
    files: ['src/**/*.js'],
    languageOptions: {
      ecmaVersion: 2019,
      sourceType: 'script',
      globals: globals
    },
    linterOptions: { reportUnusedDisableDirectives: 'error' },
    rules: {
      'no-undef': 'error',
      'no-unused-vars': ['error', { vars: 'all', args: 'none', caughtErrors: 'none' }],
      eqeqeq: ['error', 'always'],
      'no-dupe-keys': 'error',
      'no-dupe-args': 'error',
      'no-redeclare': 'error',
      'no-unreachable': 'error',
      'no-shadow-restricted-names': 'error',
      'no-with': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
      'no-script-url': 'error'
    }
  }
];
