# -*- coding: utf-8 -*-
"""E3: elemzési spec (szk.ma.analysis-spec/v1) ↔ pipeline.DEFAULTS ↔ argv, és a futás-leíró (szk.ma.run/v1).

- a leképezés introspekcióval épül, és minden analyze-kapcsolót lefed (nincs leképezetlen kapcsoló);
- options_from_args ≡ a cmd_analyze korábbi kézi opció-dictje (a pipeline-ba jutó opciók szintjén);
- oda-vissza tulajdonság MINDEN kapcsolóra: argv → spec → argv → parse ugyanazt az opció-dictet adja;
- a specből futtatott motor bájtra ugyanazt a kimenetet adja, mint a kapcsolókkal futtatott CLI;
- validálás (magyar hibák), atomi mentés, run_id, run.json; a --spec/--json-summary bekötése után a CLI is.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _helpers import ROOT
from metaelemzes import cli, pipeline, report, tableio
from metaelemzes import spec as S

EXAMPLES = os.path.join(ROOT, "peldak")
DATE = "2026-10-05"
CASES = [
    ("bcg_oltas_RR.csv", ["--measure", "RR"]),
    ("bcg_oltas_RR.csv", ["--measure", "RR", "--subgroup", "allokáció", "--moderators", "szélesség,év", "--cumulative",
                          "év", "--robust", "--ht-centre", "untruncated", "--outliers", "--title", "BCG",
                          "--left-label", "oltás jobb", "--right-label", "kontroll jobb"]),
    ("bcg_oltas_RR.csv", ["--measure", "OR", "--model", "fixed", "--mh", "--cc", "0.25", "--cc-to", "all", "--drop00",
                          "no", "--level", "90", "--exclude", "allokáció=random"]),
    ("bcg_oltas_RR.csv", ["--measure", "RD", "--tau2", "dl", "--ci", "z", "--pi", "z", "--rd-var", "gr", "--subgroup",
                          "allokáció", "--common-tau2", "--subgroup-prespecified", "--trimfill-estimator", "R0",
                          "--trimfill-trim-model", "fixed", "--egger-ci-dist", "norm", "--begg-method", "normal",
                          "--begg-continuity"]),
    ("bcg_oltas_RR.csv", ["--measure", "RR", "--moderators", "szélesség", "--metareg-test", "z", "--metareg-tau2",
                          "FE", "--include", "allokáció=random"]),
    ("normand1999_folytonos.csv", ["--measure", "SMD", "--smd-vtype", "UB", "--j-method", "approx"]),
    ("normand1999_folytonos.csv", ["--measure", "MD", "--md-vtype", "HO", "--model", "ivhet"]),
    ("normand1999_folytonos.csv", ["--measure", "SMD_GLASS", "--glass-vtype", "LS2", "--no-plots"]),
    ("molloy2014_korrelacio.csv", ["--measure", "ZCOR", "--tau2", "PM", "--ci", "hksj_adhoc"]),
    ("pritz1997_arany.csv", ["--measure", "PFT", "--pft-backtransform", "variance", "--drop00", "yes"]),
    ("pritz1997_arany.csv", ["--measure", "PLO", "--tau2", "SJ", "--level", "0,9"]),
]


def run_cli(*args):
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = cli.main(list(args))
    return code, buf.getvalue(), err.getvalue()


def parse(argv):
    an, names = S._parser()
    return an.parse_args(argv[1:] if argv and argv[0] in names else argv)


def wired():
    """Be van-e kötve a CLI-be az 'analyze --spec / --json-summary / --run-id' (cli.py, a homlokzat-munka)."""
    an, _ = S._analyze_parser()
    dests = {a.dest for a in an._actions}
    return {"spec", "json_summary", "run_id"} <= dests


def legacy_opts(a):
    """A cmd_analyze E3 előtti, kézzel épített opció-dictje (változatlan másolat, a filter_report nélkül)."""
    opts = {"measure": a.measure, "model": a.model, "tau2": a.tau2, "ci": a.ci, "pi": a.pi,
            "level": a.level, "smd_vtype": a.smd_vtype, "j_method": a.j_method, "cc": a.cc,
            "cc_to": a.cc_to, "mh": a.mh, "peto": a.peto, "rd_var": a.rd_var, "subgroup": a.subgroup,
            "common_tau2": a.common_tau2, "subgroup_prespecified": a.subgroup_prespecified,
            "moderators": [m.strip() for m in a.moderators.split(",")] if a.moderators else [],
            "metareg_test": a.metareg_test, "metareg_tau2": a.metareg_tau2, "cumulative": a.cumulative,
            "title": a.title,
            "left_label": a.left_label, "right_label": a.right_label,
            "trimfill_estimator": a.trimfill_estimator,
            "md_vtype": a.md_vtype, "glass_vtype": a.glass_vtype, "gen_smd_vtype": a.gen_smd_vtype,
            "pft_backtransform": a.pft_backtransform, "h_centre": a.ht_centre,
            "trimfill_trim_model": a.trimfill_trim_model, "egger_ci_dist": a.egger_ci_dist,
            "begg_method": a.begg_method, "begg_continuity": a.begg_continuity,
            "metareg_robust": a.robust, "outliers": a.outliers}
    if a.drop00 is not None:
        opts["drop00"] = a.drop00 == "yes"
    return opts


def effective(opts):
    """Amit a pipeline.run valójában használ: DEFAULTS + a nem None értékek (a run() első két sora)."""
    eff = dict(pipeline.DEFAULTS)
    eff.update({k: v for k, v in opts.items() if v is not None})
    return eff


def samples(o):
    """Kapcsolónként (az introspekcióból, nem kézi listából) a kipróbálandó argv-értékek."""
    if o.kind == "flag":
        return [[o.flag]]
    if o.kind == "tristate":
        return [[o.flag, c] for c in o.act.choices]
    if o.kind == "csv":
        return [[o.flag, "szélesség"], [o.flag, "szélesség,év"], [o.flag, " a , b "], [o.flag + "=-x,y"]]
    if o.kind == "multi":
        return [[o.flag, "a"], [o.flag, "a", o.flag, "b"]]
    res = []
    if o.choices is not None:
        for c in o.choices:
            res.append([o.flag, c])
            if o.act.type in (str.upper, str.lower):
                res.append([o.flag, c.lower() if o.act.type is str.upper else c.upper()])
        if o.key == "md_vtype":
            res += [[o.flag, "HO"], [o.flag, "ls"]]
        return res
    if o.json_type == "number":
        for tok in ("0", "0.25", "0,75", "1", "2.5", "90", "1e-3", "0.1"):
            try:
                o.act.type(tok)
            except (argparse.ArgumentTypeError, ValueError):
                continue
            res.append([o.flag, tok])
        return res
    return [[o.flag, "allokáció"], [o.flag, "a b"], [o.flag + "=-kötőjeles"], [o.flag, ""], [o.flag, "x=y"],
            [o.flag, "Ő \"idéző\" 'jel'"]]


class TestMapping(unittest.TestCase):
    def test_no_mapping_problems(self):
        # minden analyze-kapcsoló adat / szűrő / futás-vezérlő vagy DEFAULTS-kulcs; az alapértékek egyeznek
        self.assertEqual(S.mapping_problems(), [])

    def test_option_keys_are_exactly_defaults(self):
        opts = S.options_from_args(parse(["analyze", "--data", "x.csv", "--measure", "RR"]))
        self.assertEqual(list(opts), list(pipeline.DEFAULTS))
        self.assertEqual([r["key"] for r in S.option_table()], list(pipeline.DEFAULTS))
        schema = S.analysis_spec_schema()["properties"]["options"]
        self.assertEqual(list(schema["properties"]), list(pipeline.DEFAULTS))
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["required"], ["measure"])

    def test_explicit_dest_table(self):
        tab = {r["key"]: r for r in S.option_table()}
        self.assertEqual((tab["h_centre"]["flag"], tab["h_centre"]["dest"]), ("--ht-centre", "ht_centre"))
        self.assertEqual((tab["metareg_robust"]["flag"], tab["metareg_robust"]["dest"]), ("--robust", "robust"))
        self.assertNotIn("ht_centre", tab)
        self.assertNotIn("robust", tab)
        # --ht-center (álnév) is ugyanoda
        o = S.options_from_args(parse(["analyze", "--data", "x", "--measure", "RR", "--ht-center", "untruncated"]))
        self.assertEqual(o["h_centre"], "untruncated")

    def test_every_flag_is_classified(self):
        an, _ = S._analyze_parser()
        mapped = {o.dest for o in S._meta().opts.values() if o.dest}
        for act in an._actions:
            if isinstance(act, argparse._HelpAction):
                continue
            self.assertTrue(act.dest in mapped or act.dest in (S.DATA_DEST,) + S.FILTER_DESTS + S.RUN_DESTS,
                            "leképezetlen kapcsoló: %s" % act.option_strings)
        for key in S.spec_only_keys():
            self.assertIn(key, pipeline.DEFAULTS)
            self.assertIsNone(S._meta().opts[key].flag)

    def test_defaults_agree_with_pipeline(self):
        opts = S.options_from_args(parse(["analyze", "--data", "x", "--measure", "SMD"]))
        self.assertEqual(opts, pipeline.DEFAULTS)

    def test_enum_choices_come_from_argparse(self):
        an, _ = S._analyze_parser()
        props = S.analysis_spec_schema()["properties"]["options"]["properties"]
        for act in an._actions:
            key = S.DEST_TO_KEY.get(act.dest, act.dest)
            if act.choices is not None and key in props and "enum" in props[key]:
                self.assertEqual([c for c in props[key]["enum"] if c is not None], list(act.choices), key)
        self.assertIn(None, props["tau2"]["enum"])          # alapérték None → null is
        self.assertNotIn(None, props["model"]["enum"])
        self.assertEqual(props["md_vtype"]["enum"], ["unequal", "pooled"])   # metavar-ból


class TestOptionsFromArgs(unittest.TestCase):
    """A cmd_analyze-nak ezt a függvényt kell hívnia: ugyanazt adja a pipeline-nak, mint a régi kézi dict."""

    def test_matches_legacy_dict_for_every_case(self):
        argvs = [["analyze", "--data", "x.csv"] + flags for _, flags in CASES]
        for o in S._meta().opts.values():
            if o.act is not None:
                for s in samples(o):
                    argvs.append(["analyze", "--data", "x.csv", "--measure", "RR"] + s)
        argvs.append(["analyze", "--data", "x.csv", "--measure", "RR", "--moderators", ""])
        argvs.append(["analyze", "--data", "x.csv", "--measure", "RR", "--moderators", "a,,b"])
        for argv in argvs:
            if "--measure" not in argv:
                argv += ["--measure", "RR"]
            ns = parse(argv)
            new = S.options_from_args(ns)
            self.assertEqual(effective(new), effective(legacy_opts(ns)), argv)
            self.assertEqual(set(new), set(pipeline.DEFAULTS))

    def test_results_json_identical_to_legacy_dict(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        for fname, flags in CASES[:5]:
            ns = parse(["analyze", "--data", os.path.join(EXAMPLES, fname)] + flags)
            outs = []
            for opts in (legacy_opts(ns), S.options_from_args(ns)):
                rows, meta = tableio.read_table(ns.data)
                frep = []
                rows = tableio.apply_filters(rows, ns.exclude, ns.include, meta=meta, report=frep)
                meta["filters"] = {"exclude": ns.exclude, "include": ns.include}
                opts["filter_report"] = frep
                out, es = pipeline.run(rows, opts, meta)
                outs.append(json.dumps(pipeline.to_jsonable(out), ensure_ascii=False, indent=1))
            self.assertEqual(outs[0], outs[1], flags)


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.data = os.path.join(EXAMPLES, "bcg_oltas_RR.csv")

    def check(self, argv):
        ns1 = parse(argv)
        spec = S.spec_from_argv(argv, ROOT)
        self.assertEqual(S.validate_spec(spec), [], argv)
        argv2 = S.argv_from_spec(spec, ROOT)
        ns2 = parse(argv2)
        o1 = S.options_from_args(ns1)
        self.assertEqual(S.options_from_args(ns2), o1, (argv, argv2))
        self.assertEqual(S.options_from_spec(spec), o1, argv)
        for d in S.FILTER_DESTS:
            self.assertEqual(getattr(ns2, d), getattr(ns1, d), argv)
        self.assertEqual(os.path.normpath(os.path.join(ROOT, ns2.data)), os.path.normpath(ns1.data))
        # abszolút alak is ugyanaz; és spec → argv → spec azonos
        ns3 = parse(S.argv_from_spec(spec, ROOT, absolute=True))
        self.assertEqual(S.options_from_args(ns3), o1)
        self.assertEqual(os.path.normpath(ns3.data), os.path.normpath(ns1.data))
        # a relatív alak a projektgyökérből futtatható; innen az abszolúttal képezzük vissza a specet
        self.assertEqual(S.spec_from_argv(S.argv_from_spec(spec, ROOT, absolute=True), ROOT), spec)
        self.assertEqual(S.spec_from_argv(S.argv_from_spec(spec, ROOT, absolute=True, all_options=True), ROOT), spec)
        return spec

    def test_every_flag_every_value(self):
        n = 0
        for o in S._meta().opts.values():
            if o.act is None:
                continue
            got = samples(o)
            self.assertTrue(got, o.key)
            for s in got:
                base = ["analyze", "--data", self.data] + ([] if o.key == "measure" else ["--measure", "RR"])
                self.check(base + s)
                n += 1
        self.assertGreater(n, 80)

    def test_all_flags_at_once_and_filters(self):
        argv = ["analyze", "--data", self.data]
        for o in S._meta().opts.values():
            if o.act is not None:
                argv += samples(o)[-1]
        argv += ["--include", "allokáció=random", "--include=-x=1", "--exclude", "év=1948", "--exclude", "a= b "]
        spec = self.check(argv)
        self.assertEqual(spec["filters"], {"include": ["allokáció=random", "-x=1"], "exclude": ["év=1948", "a= b "]})

    def test_canonical_values_in_spec(self):
        spec = S.spec_from_argv(["analyze", "--data", self.data, "--measure", "rr", "--tau2", "reml", "--level", "95",
                                 "--cc", "1", "--md-vtype", "HO", "--moderators", " a , b ", "--drop00", "yes"], ROOT)
        o = spec["options"]
        self.assertEqual((o["measure"], o["tau2"], o["level"], o["cc"], o["md_vtype"], o["moderators"], o["drop00"]),
                         ("RR", "REML", 0.95, 1.0, "pooled", ["a", "b"], True))
        self.assertEqual(spec["data"], {"path": "peldak/bcg_oltas_RR.csv"})
        self.assertEqual((spec["name"], spec["outcome"]), ("bcg_oltas_rr", "bcg_oltas_RR"))

    def test_minimal_argv_only_non_defaults(self):
        spec = S.spec_from_argv(["analyze", "--data", self.data, "--measure", "RR", "--model", "random"], ROOT)
        self.assertEqual(S.argv_from_spec(spec, ROOT, os.path.join(ROOT, "peldak", "ki")),
                         ["analyze", "--data", "peldak/bcg_oltas_RR.csv", "--measure", "RR", "--out", "peldak/ki"])
        self.assertEqual(S.argv_from_spec(spec, ROOT, log_to_project=True)[-2:], ["--project", "."])

    def test_spec_only_option(self):
        spec = S.spec_from_argv(["analyze", "--data", self.data, "--measure", "RR"], ROOT)
        spec["options"]["label_col"] = "vizsgálat"
        spec["options"]["bias_min_k"] = 5
        self.assertEqual(S.validate_spec(spec), [])
        opts = S.options_from_spec(spec)
        self.assertEqual((opts["label_col"], opts["bias_min_k"]), ("vizsgálat", 5))
        with self.assertRaises(S.SpecError) as cm:
            S.argv_from_spec(spec, ROOT)
        self.assertIn("nincs parancssori kapcsolója", str(cm.exception))

    def test_partial_spec_uses_cli_defaults(self):
        spec = {"schema": S.SCHEMA, "name": "o1", "outcome": "o1", "data": {"path": "peldak/bcg_oltas_RR.csv"},
                "options": {"measure": "RR", "tau2": None}}
        self.assertEqual(S.validate_spec(spec), [])
        self.assertEqual(S.options_from_spec(spec),
                         S.options_from_args(parse(["analyze", "--data", self.data, "--measure", "RR"])))

    def test_data_outside_root_rejected(self):
        with self.assertRaises(S.SpecError):
            S.spec_from_argv(["analyze", "--data", self.data, "--measure", "RR"], os.path.join(ROOT, "tests"))


class TestEngineIdentity(unittest.TestCase):
    """A specből futtatott motor bájtra ugyanazt adja, mint a kapcsolókkal futtatott CLI."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def outdir(self, name):
        return os.path.join(self.tmp, name)

    def assertSameOutputs(self, a, b, msg):
        fa = sorted(f for f in os.listdir(a) if f != "run.json")
        fb = sorted(f for f in os.listdir(b) if f != "run.json")
        self.assertEqual(fa, fb, msg)
        self.assertIn("results.json", fa)
        for f in fa:
            with open(os.path.join(a, f), "rb") as x, open(os.path.join(b, f), "rb") as y:
                self.assertEqual(x.read(), y.read(), "%s: %s" % (msg, f))

    def test_spec_vs_flags(self):
        for i, (fname, flags) in enumerate(CASES):
            data = os.path.normpath(os.path.join(EXAMPLES, fname))
            argv = ["analyze", "--data", data] + flags
            a_dir = self.outdir("a%d" % i)
            code_a, out_a, _ = run_cli(*(argv + ["--out", a_dir, "--date", DATE]))
            self.assertEqual(code_a, 0, flags)
            spec = S.spec_from_argv(argv, ROOT)
            # (1) a spec egyenértékű kapcsolóival
            b_dir = self.outdir("b%d" % i)
            argv_b = S.argv_from_spec(spec, ROOT, b_dir, absolute=True)
            if "--no-plots" in flags:
                argv_b.append("--no-plots")
            code_b, out_b, _ = run_cli(*(argv_b + ["--date", DATE]))
            self.assertEqual(code_b, 0)
            self.assertSameOutputs(a_dir, b_dir, flags)
            self.assertEqual(out_a.replace(a_dir, "<OUT>"), out_b.replace(b_dir, "<OUT>"))
            # (2) a cmd_analyze a specből kitöltött namespace-szel (az 'analyze --spec' kódútja)
            c_dir = self.outdir("c%d" % i)
            ns = S.namespace_from_spec(spec, ROOT)
            ns.out, ns.date, ns.no_plots = c_dir, DATE, "--no-plots" in flags
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.cmd_analyze(ns), 0)
            self.assertSameOutputs(a_dir, c_dir, flags)
            # (3) könyvtári út (api.analyze): pipeline.run a specből → results.json bájtra azonos
            rows, meta = tableio.read_table(S.data_path(spec, ROOT))
            exclude, include = S.filters_from_spec(spec)
            frep = []
            rows = tableio.apply_filters(rows, exclude, include, meta=meta, report=frep)
            meta["filters"] = {"exclude": exclude, "include": include}
            opts = S.options_from_spec(spec)
            opts["filter_report"] = frep
            out, es = pipeline.run(rows, opts, meta)
            with open(os.path.join(a_dir, "results.json"), encoding="utf-8") as fh:
                self.assertEqual(json.dumps(pipeline.to_jsonable(out), ensure_ascii=False, indent=1), fh.read(),
                                 flags)

    def test_apply_spec_fills_namespace(self):
        proj = self.outdir("p")
        os.makedirs(os.path.join(proj, "03_adatok"))
        shutil.copy(os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), os.path.join(proj, "03_adatok", "o1.csv"))
        data = os.path.join(proj, "03_adatok", "o1.csv")
        flags = ["--measure", "RR", "--subgroup", "allokáció", "--exclude", "év=1948", "--robust",
                 "--moderators", "szélesség"]
        spec = S.spec_from_argv(["analyze", "--data", data] + flags, proj, name="o1_primary", pin_data=True)
        path = os.path.join(proj, S.spec_relpath("o1_primary"))
        digest = S.save_spec(path, spec)
        an, _ = S._analyze_parser()
        ns = argparse.Namespace(**{a.dest: a.default for a in an._actions if not isinstance(a, argparse._HelpAction)})
        ns.spec, ns.project = path, proj
        got = S.apply_spec(ns)            # argv nélkül: tartalék ütközés-ellenőrzés
        self.assertEqual(got, spec)
        self.assertEqual(ns.spec_sha256, digest)
        ref = parse(["analyze", "--data", data] + flags)
        self.assertEqual(S.options_from_args(ns), S.options_from_args(ref))
        self.assertEqual((ns.data, ns.exclude, ns.include), (os.path.normpath(data), ["év=1948"], None))
        # ütköző kapcsoló → hiba
        ns2 = argparse.Namespace(**vars(ns))
        ns2.spec, ns2.model = path, "fixed"
        with self.assertRaises(S.SpecError) as cm:
            S.apply_spec(ns2)
        self.assertIn("--model", str(cm.exception))
        # megváltozott adat → elavult spec
        with open(data, "a", encoding="utf-8") as fh:
            fh.write("Új;1;10;2;10;40;2000;random\n")
        ns3 = argparse.Namespace(**{a.dest: a.default for a in an._actions if not isinstance(a, argparse._HelpAction)})
        ns3.spec, ns3.project = path, proj
        with self.assertRaises(S.SpecError) as cm:
            S.apply_spec(ns3)
        self.assertIn("megváltozott", str(cm.exception))


class TestValidate(unittest.TestCase):
    def base(self):
        return {"schema": S.SCHEMA, "name": "o1_primary", "outcome": "o1", "purpose": "primary",
                "prespecified": True, "protocol_ref": None, "parent": None,
                "data": {"path": "03_adatok/o1.csv", "sha256": "a" * 64},
                "options": {"measure": "RR", "tau2": "REML", "ci": "hksj", "moderators": ["szélesség"],
                            "drop00": None, "h_centre": "untruncated", "metareg_robust": True},
                "filters": {"include": [], "exclude": ["estimated=igen"]}, "kb_refs": ["D-S08-001"],
                "x_ismeretlen_felso_mezo": 1}

    def errs(self, **changes):
        spec = self.base()
        for path, v in changes.items():
            node = spec
            parts = path.split("__")
            for p in parts[:-1]:
                node = node[p]
            if v is KeyError:
                del node[parts[-1]]
            else:
                node[parts[-1]] = v
        return S.validate_spec(spec)

    def test_valid(self):
        self.assertEqual(S.validate_spec(self.base()), [])

    def test_unknown_options_rejected_with_hint(self):
        e = self.errs(options__robust=True, options__ht_centre="truncated", options__foo=1)
        self.assertEqual(len(e), 3)
        self.assertTrue(any("options.robust" in x and "metareg_robust" in x for x in e), e)
        self.assertTrue(any("options.ht_centre" in x and "h_centre" in x for x in e), e)
        self.assertTrue(all("ismeretlen opció" in x for x in e))

    def test_enum_and_type_errors(self):
        cases = {"options__model": "mixed", "options__tau2": "reml", "options__level": 95, "options__cc": -1,
                 "options__mh": 1, "options__drop00": "yes", "options__moderators": "a,b",
                 "options__subgroup": 3, "options__pi": None, "options__label_col": 1, "options__bias_min_k": 2.5,
                 "options__level__": None}
        del cases["options__level__"]
        for k, v in cases.items():
            e = self.errs(**{k: v})
            self.assertEqual(len(e), 1, (k, v, e))
            self.assertTrue(e[0].startswith("options."), e)
        self.assertIn("lehetséges: random, fixed, ivhet", self.errs(options__model="mixed")[0])
        self.assertIn("'REML'", self.errs(options__tau2="reml")[0])
        self.assertEqual(len(self.errs(options__moderators=["a,b", "", " c"])), 3)
        self.assertTrue(self.errs(options__cc=float("nan")))
        self.assertTrue(self.errs(options__level=float("inf")))
        self.assertTrue(self.errs(options__mh=0))

    def test_required_and_structure(self):
        self.assertIn("options.measure: kötelező (a parancssorban is: --measure)",
                      self.errs(options__measure=KeyError))
        for k in ("schema", "name", "outcome", "data", "options"):
            self.assertTrue(any(k in x for x in self.errs(**{k: KeyError})), k)
        self.assertTrue(self.errs(schema="szk.ma.analysis-spec/v2"))
        self.assertTrue(self.errs(name="O1 Primary"))
        self.assertTrue(self.errs(name="a" * 65))
        self.assertTrue(self.errs(name="abc\n"))
        self.assertTrue(self.errs(purpose="main"))
        self.assertTrue(self.errs(prespecified="igen"))
        self.assertTrue(self.errs(parent="Nem Név"))
        self.assertEqual(S.validate_spec([]), ["a spec nem JSON-objektum (kapott: list)"])

    def test_paths_and_filters(self):
        for bad in ("/abs/o1.csv", "C:/x.csv", "../o1.csv", "a/../o1.csv", "a\\b.csv", "", "dir/"):
            self.assertTrue(self.errs(data__path=bad), bad)
        self.assertEqual(self.errs(data__path="03_adatok/al mappa/o1 é.csv"), [])
        self.assertTrue(self.errs(data__sha256="ABC"))
        self.assertTrue(self.errs(filters__exclude=["estimated"]))
        self.assertTrue(self.errs(filters__exclude=[" =x"]))
        self.assertTrue(self.errs(filters__excludes=[]))
        self.assertTrue(self.errs(filters="rob=high"))
        self.assertTrue(self.errs(kb_refs="D-S08-001"))

    def test_messages_are_hungarian(self):
        e = self.errs(options__model="mixed", options__foo=1, data__path="../x", name="X")
        joined = " ".join(e)
        for word in ("érvénytelen", "ismeretlen", "relatív"):
            self.assertIn(word, joined)


class TestLoadSave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.spec = S.spec_from_argv(["analyze", "--data", os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), "--measure",
                                      "RR", "--title", "Ő"], ROOT, purpose="primary", prespecified=True)

    def test_save_atomic_canonical_and_hash(self):
        path = os.path.join(self.tmp, "05_elemzes", "specs", "o1.json")
        digest = S.save_spec(path, self.spec)
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertEqual(digest, S.spec_sha256(self.spec))
        self.assertEqual(os.listdir(os.path.dirname(path)), ["o1.json"])     # nincs ott maradt ideiglenes fájl
        self.assertTrue(raw.endswith(b"\n") and "Ő".encode("utf-8") in raw)
        self.assertEqual(list(json.loads(raw.decode("utf-8")))[:4], ["schema", "name", "outcome", "purpose"])
        self.assertEqual(S.load_spec(path), self.spec)
        # kulcssorrendtől független: ugyanaz a hash
        shuffled = dict(reversed(list(self.spec.items())))
        shuffled["options"] = dict(reversed(list(self.spec["options"].items())))
        self.assertEqual(S.spec_sha256(shuffled), digest)
        self.assertEqual(S.save_spec(path, shuffled), digest)

    def test_save_rejects_invalid_and_keeps_old_file(self):
        path = os.path.join(self.tmp, "s.json")
        S.save_spec(path, self.spec)
        bad = dict(self.spec, options=dict(self.spec["options"], model="x"))
        with self.assertRaises(S.SpecError):
            S.save_spec(path, bad)
        self.assertEqual(S.load_spec(path), self.spec)
        self.assertEqual(os.listdir(self.tmp), ["s.json"])

    def test_load_rejects_nan_duplicates_and_garbage(self):
        p = os.path.join(self.tmp, "x.json")
        txt = S.spec_bytes(self.spec).decode("utf-8")
        for bad in (txt.replace('"cc": 0.5', '"cc": NaN'), txt.replace('"cc": 0.5', '"cc": Infinity'),
                    txt.replace('"cc": 0.5', '"cc": 0.5, "cc": 1'), "{", "[]"):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(bad)
            with self.assertRaises(S.SpecError):
                S.load_spec(p)
        with open(p, "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + txt.encode("utf-8"))      # BOM-mal is olvasható
        self.assertEqual(S.load_spec(p), self.spec)

    def test_spec_relpath(self):
        self.assertEqual(S.spec_relpath("o1_primary"), "05_elemzes/specs/o1_primary.json")
        with self.assertRaises(S.SpecError):
            S.spec_relpath("../x")


class TestRunDescriptor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_run_id(self):
        h = "a1f3c2" + "0" * 58
        self.assertEqual(S.run_id(datetime.datetime(2026, 10, 4, 21, 12, 0), h), "20261004T211200Z-a1f3c2")
        cest = datetime.timezone(datetime.timedelta(hours=2))
        self.assertEqual(S.run_id(datetime.datetime(2026, 10, 4, 23, 12, 0, tzinfo=cest), h.upper()),
                         "20261004T211200Z-a1f3c2")
        self.assertRegex(S.run_id(datetime.datetime.now(datetime.timezone.utc), h), S.RUN_ID_PATTERN)
        for bad in (None, "", "xyz123", "a1f3"):
            with self.assertRaises(ValueError):
                S.run_id(datetime.datetime(2026, 1, 1), bad)

    def run_pipeline(self, argv):
        ns = parse(argv)
        rows, meta = tableio.read_table(ns.data)
        out, es = pipeline.run(rows, S.options_from_args(ns), meta)
        return ns, out, es

    def test_descriptor_commit_in_project(self):
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(proj, "03_adatok"))
        data = os.path.join(proj, "03_adatok", "o1.csv")
        shutil.copy(os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), data)
        ns, out, es = self.run_pipeline(["analyze", "--data", data, "--measure", "RR", "--out",
                                         os.path.join(proj, "05_elemzes", "o1", "r"), "--project", proj])
        outdir = ns.out
        paths = pipeline.write_outputs(out, es, outdir, report.build_report(out, None, DATE))
        t0 = datetime.datetime(2026, 10, 4, 21, 12, 0, tzinfo=datetime.timezone.utc)
        t1 = t0 + datetime.timedelta(milliseconds=84)
        d = S.describe_cli_run(ns, out, paths, outdir, t0, t1)
        h = S.sha256_file(data)
        self.assertEqual((d["schema"], d["mode"], d["run_id"]), ("szk.ma.run/v1", "commit", "20261004T211200Z-" + h[:6]))
        self.assertEqual(d["data"], {"path": "03_adatok/o1.csv", "sha256": h, "rows": 13})
        self.assertEqual(d["spec"]["path"], None)
        self.assertEqual(d["spec"]["sha256"], S.spec_sha256(S.spec_from_namespace(ns, proj)))
        self.assertEqual(d["equivalent_argv"], ["ma.py", "analyze", "--data", "03_adatok/o1.csv", "--measure", "RR",
                                                "--out", "05_elemzes/o1/r", "--project", "."])
        self.assertEqual(d["files"]["results"]["path"], "05_elemzes/o1/r/results.json")
        self.assertEqual(d["files"]["results"]["sha256"], S.sha256_file(paths["results.json"]))
        self.assertEqual(list(d["files"]), ["results", "plot", "report", "effect_sizes", "forest", "funnel", "doi"])
        self.assertEqual(d["primary"], {"model": "random",
                                        "display_text": {"hu": "0,49 [0,33; 0,73]", "en": "0.49 [0.33; 0.73]"}})
        self.assertEqual((d["k"], d["elapsed_ms"], d["started"]), (13, 84, "2026-10-04T21:12:00Z"))
        for key in S.run_schema()["required"]:
            self.assertIn(key, d)
        p = S.write_run_json(outdir, d)
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), d)

    def test_descriptor_explore_without_project(self):
        data = os.path.join(EXAMPLES, "pritz1997_arany.csv")
        ns, out, es = self.run_pipeline(["analyze", "--data", data, "--measure", "PFT", "--out", self.tmp])
        paths = pipeline.write_outputs(out, es, self.tmp, None, plots=False)
        t = datetime.datetime(2026, 10, 4, 21, 12, 0)
        d = S.describe_cli_run(ns, out, paths, self.tmp, t, t)
        self.assertEqual((d["mode"], d["run_id"]), ("explore", None))
        self.assertTrue(os.path.isabs(d["data"]["path"]))
        self.assertEqual(d["spec"], {"path": None, "sha256": None, "name": "pritz1997_arany", "parent": None})
        self.assertEqual(d["equivalent_argv"][:3], ["ma.py", "analyze", "--data"])
        self.assertEqual(parse(d["equivalent_argv"][1:]).data, os.path.abspath(data))
        self.assertEqual(list(d["files"]), ["results", "effect_sizes"])
        ns.run_id = "20261004T211200Z-abcdef"
        self.assertEqual(S.describe_cli_run(ns, out, paths, self.tmp, t, t)["mode"], "commit")
        ns.run_id = "rossz"
        with self.assertRaises(S.SpecError):
            S.describe_cli_run(ns, out, paths, self.tmp, t, t)

    def test_mode_rules_and_no_nan(self):
        out = {"effect_sizes": {"k": 0}, "validation": {"summary": {"error": 1, "warning": 0, "info": 0}},
               "input": {"n_rows": 2}, "primary": None, "extra": float("nan")}
        d = S.run_descriptor(out)
        self.assertEqual((d["run_id"], d["mode"], d["primary"], d["k"], d["data"]["rows"]),
                         (None, "explore", None, 0, 2))
        self.assertNotIn("files", d)
        json.dumps(d, allow_nan=False)
        with self.assertRaises(ValueError):
            S.run_descriptor(out, mode="commit")
        with self.assertRaises(ValueError):
            S.run_descriptor(out, mode="explore", run_id="20261004T211200Z-abcdef")
        with self.assertRaises(ValueError):
            S.run_descriptor(out, mode="dry")
        self.assertEqual(S.display_text(float("nan"), None, 1.5), {"hu": "– [–; 1,50]", "en": "– [–; 1.50]"})
        self.assertNotIn("NaN", S.run_json_bytes({"x": float("nan"), "y": [float("inf")]}).decode())


class TestContractFiles(unittest.TestCase):
    def test_generated_schemas_are_valid_json(self):
        for sch in (S.analysis_spec_schema(), S.run_schema()):
            txt = json.dumps(sch, allow_nan=False)
            self.assertNotIn("http://", txt.replace("https://json-schema.org", ""))
            self.assertTrue(sch["$id"].startswith("urn:szk:contract:"))
            for pat in re.findall(r'"pattern": "((?:[^"\\]|\\.)*)"', txt):
                re.compile(json.loads('"%s"' % pat))

    def test_contract_copy_matches_generated(self):
        # ha a metaelemzes/contracts/ már tartalmazza, bájt-tartalomban a generálttal egyezzen (sodródás-őr)
        d = os.path.join(ROOT, "metaelemzes", "contracts")
        for fname, gen in (("ma.analysis-spec.schema.json", S.analysis_spec_schema),
                           ("ma.run.schema.json", S.run_schema)):
            p = os.path.join(d, fname)
            if not os.path.isfile(p):
                continue
            with open(p, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), gen(), fname)


@unittest.skipUnless(wired(), "a cli.py még nincs bekötve (analyze --spec / --json-summary / --run-id)")
class TestCLIWired(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        code, _, err = run_cli("project", "init", self.proj, "--title", "Teszt")
        self.assertEqual(code, 0, err)
        os.makedirs(os.path.join(self.proj, "03_adatok"), exist_ok=True)
        self.data = os.path.join(self.proj, "03_adatok", "o1.csv")
        shutil.copy(os.path.join(EXAMPLES, "bcg_oltas_RR.csv"), self.data)
        self.flags = ["--measure", "RR", "--subgroup", "allokáció", "--moderators", "szélesség", "--robust",
                      "--exclude", "év=1948", "--ht-centre", "untruncated"]
        spec = S.spec_from_argv(["analyze", "--data", self.data] + self.flags, self.proj, name="o1_primary",
                                outcome="o1", purpose="primary", prespecified=True, pin_data=True)
        self.spec_path = os.path.join(self.proj, S.spec_relpath("o1_primary"))
        self.spec_sha = S.save_spec(self.spec_path, spec)

    def test_spec_run_equals_flag_run_and_run_json(self):
        a_dir, b_dir = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        code, out, err = run_cli("analyze", "--data", S.data_path(S.load_spec(self.spec_path), self.proj),
                                 *self.flags, "--out", a_dir, "--project", self.proj, "--date", DATE)
        self.assertEqual(code, 0, err)
        code, out, err = run_cli("analyze", "--spec", self.spec_path, "--out", b_dir, "--project", self.proj,
                                 "--date", DATE, "--json-summary")
        self.assertEqual(code, 0, err)
        summary = json.loads(out)
        TestEngineIdentity.assertSameOutputs(self, a_dir, b_dir, "spec vs kapcsolók")
        with open(os.path.join(b_dir, "run.json"), encoding="utf-8") as fh:
            run = json.load(fh)
        self.assertEqual(summary, run)
        self.assertEqual((run["schema"], run["mode"]), (S.RUN_SCHEMA, "commit"))
        self.assertRegex(run["run_id"], S.RUN_ID_PATTERN)
        self.assertEqual(run["spec"]["sha256"], self.spec_sha)
        self.assertEqual(run["spec"]["name"], "o1_primary")
        self.assertEqual(run["data"]["sha256"], S.sha256_file(self.data))
        self.assertEqual(run["data"]["path"], "03_adatok/o1.csv")
        self.assertEqual(run["equivalent_argv"][:4], ["ma.py", "analyze", "--spec", "05_elemzes/specs/o1_primary.json"])
        for role, f in run["files"].items():
            self.assertEqual(S.sha256_file(os.path.join(self.proj, f["path"])), f["sha256"], role)
        with open(os.path.join(a_dir, "run.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["mode"], "commit")

    def test_run_json_for_plain_run_and_explicit_run_id(self):
        o = os.path.join(self.tmp, "c")
        code, _, err = run_cli("analyze", "--data", self.data, "--measure", "RR", "--out", o)
        self.assertEqual(code, 0, err)
        with open(os.path.join(o, "run.json"), encoding="utf-8") as fh:
            run = json.load(fh)
        self.assertEqual((run["mode"], run["run_id"]), ("explore", None))
        # --project nélkül a data.path a munkakönyvtárhoz relatív: innen (tests/) nem található → érthető hiba
        code, _, err = run_cli("analyze", "--spec", self.spec_path, "--out", o)
        self.assertEqual(code, 1)
        self.assertIn("adatfájlja nem található", err)
        code, _, err = run_cli("analyze", "--spec", self.spec_path, "--out", o, "--project", self.proj,
                               "--run-id", "20261004T211200Z-abcdef")
        self.assertEqual(code, 0, err)
        with open(os.path.join(o, "run.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["run_id"], "20261004T211200Z-abcdef")

    def test_conflicting_flags_rejected(self):
        for extra in (["--model", "fixed"], ["--data", self.data], ["--exclude", "rob=high"], ["--robust"]):
            code, out, err = run_cli("analyze", "--spec", self.spec_path, "--out", os.path.join(self.tmp, "x"),
                                     *extra)
            self.assertNotEqual(code, 0, extra)
            self.assertIn(extra[0], err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x", "results.json")))

    def test_stale_and_invalid_spec(self):
        with open(self.data, "a", encoding="utf-8") as fh:
            fh.write("Új;1;10;2;10;40;2000;random\n")
        code, _, err = run_cli("analyze", "--spec", self.spec_path, "--out", os.path.join(self.tmp, "y"),
                               "--project", self.proj)
        self.assertEqual(code, 1)
        self.assertIn("megváltozott", err)
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('{"schema": "szk.ma.analysis-spec/v1", "name": "x", "outcome": "x", '
                     '"data": {"path": "a.csv"}, "options": {"measure": "RR", "robust": true}}')
        code, _, err = run_cli("analyze", "--spec", bad, "--out", os.path.join(self.tmp, "z"))
        self.assertEqual(code, 1)
        self.assertIn("options.robust", err)

    def test_flags_still_required_without_spec(self):
        with redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                cli.main(["analyze", "--out", os.path.join(self.tmp, "w")])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--data", err.getvalue())


if __name__ == "__main__":
    unittest.main()
