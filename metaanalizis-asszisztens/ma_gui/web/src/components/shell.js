/* components/shell.js — MA.shell: argv → bemásolható parancssor, a kiírt szöveg soha nem változtathatja meg a
 * parancs szerkezetét (terv 7.1 T8; bírálat WS-2).
 *
 *   MA.shell.posix(argv)    → 'python3 …' egyszeres idézőjelekkel (sh/bash/zsh: benne semmi nem különleges; a
 *                             "'" → '\'' ) — mindig biztonságos.
 *   MA.shell.windows(argv)  → {line, unsafe}: a line kettős idézőjelekkel, ha MINDEN argumentum biztonságos a cmd.exe-ben
 *                             ÉS a PowerShellben is (a Windows Terminal alapja a PowerShell, a beillesztő nem tudja,
 *                             melyikbe illeszt). Idézőjelen belül a cmd.exe-nek a " és a %, a PowerShellnek a ", a $,
 *                             a ` és a „ ” ‟ (ezeket is idézőjelnek veszi)
 *                             különleges; ha egy argumentumban ilyen van, nincs egysoros parancs: line = null,
 *                             unsafe = [a hozzá tartozó kapcsoló neve, pl. '--decision'] — a felület ezt mondja ki,
 *                             és nem másol semmit a vágólapra.
 *   MA.shell.isWindows()    → a böngésző Windowson fut-e (a felkínált/másolt sor ehhez igazodik).
 *   MA.shell.preferred(argv, prog) → {line, unsafe, windows} a mostani platformra.
 * A prog: a program-előtag argv-je (alap: posix ['python3'], windows ['py', '-3']).
 */
(function () {
  'use strict';
  var MA = window.MA;

  // ékezetes betű (Latin-1 / Latin Extended-A/B) idézőjel nélkül is biztonságos mindkét héjban
  var POSIX_BARE = /^[A-Za-z0-9_@%+=:,./\u00C0-\u024F-]+$/;
  // Windows: idézőjel nélkül csak a mindkét héjban semleges karakterek (nincs , ; = @ ( ) & | < > ^ szóköz)
  var WIN_BARE = /^[A-Za-z0-9_./:\\\u00C0-\u024F-]+$/;
  // idézőjelen belül is különleges (cmd: " és %; PowerShell: " $ ` és a tipográfiai kettős idézőjelek); sortörés,
  // NUL, és a záró \ (a CRT a \"-t idézőjelnek venné)
  var WIN_UNSAFE = /["%$`“”„‟\r\n\0]|\\$/;

  function posixQuote(a) {
    var s = String(a);
    if (POSIX_BARE.test(s)) { return s; }
    return '\'' + s.replace(/'/g, '\'\\\'\'') + '\'';
  }

  function winQuote(a) {
    var s = String(a);
    if (WIN_BARE.test(s)) { return s; }
    if (WIN_UNSAFE.test(s)) { return null; }
    return '"' + s + '"';
  }

  function optionOf(args, i) {
    for (var j = i - 1; j >= 0; j--) {
      if (/^--?[A-Za-z]/.test(String(args[j]))) { return String(args[j]); }
    }
    return String(args[i]).slice(0, 24);
  }

  function rest(argv) { return Array.isArray(argv) ? argv.map(String) : []; }

  function posix(argv, prog) {
    return (prog || ['python3']).concat(rest(argv).map(posixQuote)).join(' ');
  }

  function windows(argv, prog) {
    var args = rest(argv);
    var unsafe = [];
    var parts = args.map(function (a, i) {
      var q = winQuote(a);
      if (q === null) { unsafe.push(optionOf(args, i)); }
      return q;
    });
    if (unsafe.length) { return { line: null, unsafe: unsafe }; }
    return { line: (prog || ['py', '-3']).concat(parts).join(' '), unsafe: [] };
  }

  function isWindows() {
    var nav = window.navigator || {};
    return /win/i.test(String(nav.platform || '')) || /windows/i.test(String(nav.userAgent || ''));
  }

  function preferred(argv, prog) {
    if (isWindows()) {
      var w = windows(argv, prog);
      return { line: w.line, unsafe: w.unsafe, windows: true };
    }
    return { line: posix(argv, prog), unsafe: [], windows: false };
  }

  MA.shell = { posix: posix, windows: windows, posixQuote: posixQuote, winQuote: winQuote, isWindows: isWindows,
    preferred: preferred };
})();
