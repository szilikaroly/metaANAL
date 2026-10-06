#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parallel unittest runner (stdlib only): every test module runs in its own subprocess.

    python3 tests/run_parallel.py [--jobs N] [--gui] [-v] [--timeout S] [module ...]

Discovery mirrors `python3 -m unittest discover -s tests` (pattern 'test*.py', valid module
names, recursion into sub-packages that have an __init__.py). Each child process runs
unittest's own discover() restricted to exactly one file (pattern = that file name, same
top-level directory), so imports, load_tests() and import-error reporting are identical to the
single-process run; only the process boundary differs (module-level global state is isolated).

--gui additionally runs tests/gui/test*.py. tests/gui has no __init__.py, so plain
`unittest discover -s tests` does not collect it; here it is run with tests/gui as its own
top-level directory, the same as `python3 -m unittest discover -s tests/gui`.

Exit status 1 if any module has a failure, an error, an unexpected success, crashed or timed
out; 0 otherwise.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
VALID_MODULE_NAME = re.compile(r"[_a-z]\w*\.py$", re.IGNORECASE)

CHILD = r"""
import json, sys, time, unittest
top, start, pattern, out, verbosity = sys.argv[1:6]
t0 = time.time()
loader = unittest.TestLoader()
suite = loader.discover(start, pattern=pattern, top_level_dir=top)
res = unittest.TextTestRunner(stream=sys.stderr, verbosity=int(verbosity)).run(suite)
fmt = lambda pairs: [[str(t), tb] for t, tb in pairs]
with open(out, "w") as fh:
    json.dump({"run": res.testsRun, "failures": fmt(res.failures), "errors": fmt(res.errors),
               "skipped": len(res.skipped), "expected_failures": len(res.expectedFailures),
               "unexpected_successes": [str(t) for t in res.unexpectedSuccesses],
               "successful": res.wasSuccessful(), "seconds": time.time() - t0}, fh)
"""


def _match(name, pattern):
    import fnmatch
    return fnmatch.fnmatch(name, pattern)


def discover(start, top, pattern):
    """[(display name, top-level dir, start dir, file pattern)] like TestLoader.discover()."""
    found = []
    for name in sorted(os.listdir(start)):
        path = os.path.join(start, name)
        if os.path.isfile(path):
            if VALID_MODULE_NAME.match(name) and _match(name, pattern):
                rel = os.path.relpath(path, top)[:-3].replace(os.sep, ".")
                found.append((rel, top, start, name))
        elif os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py")):
            found.extend(discover(path, top, pattern))
    return found


def run_module(item, verbosity, timeout, tmpdir):
    name, top, start, fname = item
    out = os.path.join(tmpdir, name + ".json")
    cmd = [sys.executable, "-c", CHILD, top, start, fname, out, str(verbosity)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=timeout)
        rc, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as ex:
        rc, stdout, stderr = None, ex.stdout or "", "TIMEOUT after %ss\n%s" % (timeout, ex.stderr or "")
    wall = time.time() - t0
    res = None
    if os.path.exists(out):
        with open(out) as fh:
            res = json.load(fh)
    if res is None:   # crashed before writing results (e.g. os._exit, segfault, timeout)
        res = {"run": 0, "failures": [], "errors": [[name, "child process ended without results "
                                                     "(rc=%s)\n%s" % (rc, (stderr or "")[-3000:])]],
               "skipped": 0, "expected_failures": 0, "unexpected_successes": [], "successful": False,
               "seconds": wall}
    res.update(name=name, wall=wall, rc=rc, stdout=stdout, stderr=stderr)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the unittest modules under tests/ in parallel subprocesses.")
    ap.add_argument("modules", nargs="*", help="only these modules (e.g. test_units or gui.test_store)")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--gui", action="store_true", help="also run tests/gui/test*.py")
    ap.add_argument("-p", "--pattern", default="test*.py")
    ap.add_argument("-s", "--start", default=HERE, help="start directory (default: this tests/ directory)")
    ap.add_argument("-v", "--verbose", action="store_true", help="verbose child runners; print every module's output")
    ap.add_argument("--timeout", type=float, default=None, help="per-module timeout in seconds")
    a = ap.parse_args(argv)

    start = os.path.abspath(a.start)
    items = discover(start, start, a.pattern)
    gui = os.path.join(start, "gui")
    if a.gui and os.path.isdir(gui) and not os.path.isfile(os.path.join(gui, "__init__.py")):
        items += [("gui." + n, t, s, f) for n, t, s, f in discover(gui, gui, a.pattern)]
    if a.modules:
        want = set(a.modules)
        items = [it for it in items if it[0] in want or it[3][:-3] in want]
    if not items:
        print("no test modules found")
        return 1
    # longest first (file size as a proxy) for better packing
    items.sort(key=lambda it: -os.path.getsize(os.path.join(it[2], it[3])))
    t0 = time.time()
    results = []
    with tempfile.TemporaryDirectory(prefix="run_parallel_") as tmp:
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            futs = {ex.submit(run_module, it, 2 if a.verbose else 1, a.timeout, tmp): it for it in items}
            for fut in as_completed(futs):
                r = fut.result()
                results.append(r)
                bad = len(r["failures"]) + len(r["errors"]) + len(r["unexpected_successes"])
                print("%-4s %-34s %4d tests  %6.1f s%s" % ("ok" if r["successful"] else "FAIL", r["name"], r["run"],
                                                         r["wall"], "" if not bad else "  (%d problem%s)"
                                                         % (bad, "s" if bad > 1 else "")), flush=True)
                if a.verbose and r["stderr"]:
                    print(r["stderr"].rstrip())
    wall = time.time() - t0
    results.sort(key=lambda r: r["name"])
    tot = {k: sum(r[k] if isinstance(r[k], int) else len(r[k]) for r in results)
           for k in ("run", "failures", "errors", "skipped", "expected_failures", "unexpected_successes")}
    failed = [r for r in results if not r["successful"]]
    for r in failed:
        print("\n" + "=" * 70)
        print("MODULE %s (rc=%s)" % (r["name"], r["rc"]))
        for kind in ("failures", "errors"):
            for test, tb in r[kind]:
                print("-" * 70)
                print("%s: %s" % (kind[:-1].upper(), test))
                print(tb.rstrip())
        for test in r["unexpected_successes"]:
            print("UNEXPECTED SUCCESS: %s" % test)
    print("\n" + "-" * 70)
    print("Ran %d tests in %d modules in %.1f s wall (%d jobs; %.1f s summed module time)"
          % (tot["run"], len(results), wall, a.jobs, sum(r["wall"] for r in results)))
    extra = ", ".join("%s=%d" % (k, tot[k]) for k in ("failures", "errors", "skipped", "expected_failures",
                                                       "unexpected_successes") if tot[k])
    if failed:
        print("FAILED (%s; modules: %s)" % (extra or "crash", ", ".join(r["name"] for r in failed)))
        return 1
    print("OK" + (" (%s)" % extra if extra else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
