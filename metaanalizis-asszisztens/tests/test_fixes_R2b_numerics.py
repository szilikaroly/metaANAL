# -*- coding: utf-8 -*-
"""Regressziós tesztek a differenciális fuzz (tests/fuzz) numerikus hibáihoz: fuzz:D1–D4.

D1 = (RE)ML τ² a határon a belső maximum helyett (oszcilláló Fisher-scoring, rács-tartalék);
D2 = tr(P) / DL τ² / WLS kiejtéses hibája rosszul kondicionált meta-regressziós tervnél;
D3 = Knapp–Hartung meta-regresszió tökéletes illeszkedésnél; D4 = Begg-teszt azonos hatásokkal.
Az adatsorok a fuzz-futások pontos adatsorai (azonosító a megjegyzésben, újragenerálható:
tests/fuzz/gen.py). Referencia: R metafor 4.4 (rma, leave1out, influence; control = list(threshold =
1e-14·medián(v_i), stepadj = 0.5)), illetve a tests/fuzz/exact.py pontos racionális aritmetikája —
D2-nél a metafor maga is pontatlan (pl. DL τ² 3,756727 a pontos 3,757080 helyett).
"""
import math
import random
import unittest

from _helpers import assert_close
from metaelemzes import bias as B
from metaelemzes import linalg as la
from metaelemzes import models as M
from metaelemzes import moderators as MO
from metaelemzes import sensitivity as S

# fuzz 20261004-00421 (k = 13)
D1_Y = [0.16412953686397994, 0.9339800789609318, 0.31848241878171457, -0.18763501460043086,
        0.265351273703522, -0.10488540470525537, 0.5191413266888798, 0.04864714754599725,
        -0.11770903400801502, -0.05896784380412723, 0.2559750989993806, 0.3268107945495797,
        -0.07126589130230249]
D1_V = [0.05629623570610577, 0.408596147862564, 0.12495164918684593, 0.5090885068850163,
        0.07113410962541696, 0.1465595448328902, 0.5667174517537003, 0.8415734940905141,
        0.18636638145565038, 0.11365076171859316, 0.1377441313045374, 0.000698342004742594,
        0.2597865875528922]
# fuzz 20261004-01745 (k = 38, v_i-arány 5,4e7)
D1B_Y = [4.001145602073867, 2.8430665207882675, 4.699773999695067, 4.399954709785005, 3.783212413971418,
         3.8274108331930634, 3.783487788004375, 3.4477050361811514, 2.6837043980416526, 3.408586076433571,
         3.7082033112210215, 4.52506068220865, 3.4826499961781336, 4.17276222884173, 3.4966538628883086,
         4.157329549189212, 3.710172236746727, 3.0577797609490887, 4.423170143074157, 4.386354284413546,
         4.31601331726327, 4.596000710827509, 4.599379087242613, 3.961500879554543, 2.689475707598125,
         3.97155515757317, 4.321471768594102, 3.7554589499363216, 2.7829655537669957, 3.847405381549497,
         3.2866478741081706, 4.611623038563066, 3.428725617082346, 4.06899142915224, 3.4600540779155233,
         4.364426825325523, 3.5762361999268424, 4.502090495478706]
D1B_V = [3.381004988587359, 8.039040955052421, 2.45512912566018, 3.2652519849726422, 2.0532132592580394,
         2.6977916674599083, 3.2482505432074134, 8.196377318846652, 5.788934341067283, 2.974484999343849,
         1.1156977814521116, 3.780450994010618, 8.437321024934388, 1.066320541557409, 2.8972364150714167,
         6.1626003604279855, 1.4590592119988945, 3.6221369561297876, 1.6332105455347533e-07,
         2.3734655708722134, 5.88100104087567, 7.595582463311694, 7.0090027140730164, 1.2574803385425435,
         1.7017666578161246, 4.763696819029968, 2.119671639498483, 1.1837609259645703, 1.6419854443701571,
         8.827254570468046, 1.2666053076369488, 2.65685072118482, 7.0686975086892865, 1.3407227717202077,
         5.635278644564418, 4.359773419859691, 1.1725192803593463, 3.2309264984570256]

# fuzz:D2 a batch-ből (DL meta-regresszió, közel-kollineáris oszlopok + v_i-arány 2,8e4)
D2_Y = [-6.961840339577067, 1.6947013346874393, 3.1814709280399334, 0.7301014495661451, 1.4155202200446975,
        1.4867026531438612]
D2_V = [2.732102083952417, 0.0004136786160774165, 8.96927815680661, 0.0003153613327840782, 0.5505826204961216,
        5.6481295901929025]
D2_X = [[1.0, 1.0, 1.0004322962767473, 7.820237031166391], [1.0, 1.0, 0.9999534569035755, 4.873466536882888],
        [1.0, 1.0, 0.999723124331152, 1.3226067371686512], [1.0, 0.0, 2.3169455826201296e-05, 9.583699154824775],
        [1.0, 0.0, 0.00029177673513281307, 9.67810641881192], [1.0, 1.0, 1.00053173929353, 1.7238621320031111]]
# fuzz 20261005-00771: collinear moderátor (x2 = x1 + N(0, 1e-3)), v_i-arány 2,9e7
C_Y = [2.570468165196308, -1.4916931905752198, 5.115135412040074, -1.862191957489464, -2.9977219894191833,
       1.7589886343414707, -0.4801296991838542, 0.4574863522567627, 2.2720782486727953, 1.7224197456621302,
       -1.8725118248665493, 0.3131830437998073, 2.432304001018602]
C_V = [1.5484272892583044, 2.8817669140191966, 8.714341083416416, 3.1062648939557317e-07, 2.854392144173882,
       1.2143516739821827, 1.3193951434212887, 2.9057160372014232, 6.190473325974865, 8.886943317687697,
       7.747652176134463, 7.784822219031933, 1.3640469410253662]
C_M = [[6.168068375896743, 6.166756564421325, 7.087237929071513], [5.538325953786671, 5.540312747115481, 7.858140433930373],
       [9.227125698880785, 9.227898993592804, 0.050751863883271575], [3.6067732620617656, 3.6065943656438515, 0.5574831598318974],
       [5.024734504290091, 5.025550178934958, 8.33751852291443], [1.1676973962275294, 1.165074778937995, 4.038085074367432],
       [7.890702963995172, 7.890821407462284, 5.594267170881143], [9.114990079552763, 9.116181264465201, 7.043663339280371],
       [1.1330390863459083, 1.1338608123651226, 0.46086893784556326], [7.883952616180357, 7.8844893119349, 0.38812649299823976],
       [6.811423907807174, 6.810988622152057, 2.1675749994118467], [8.437963625840023, 8.43827110597907, 7.6994277118092675],
       [1.2383522121275048, 1.237949866920016, 3.9439374567062346]]
# fuzz 11-00054: közös τ²-es DL alcsoport-elemzés, egy v_i = 2,6e-7
SG_Y = [0.3776486892720574, -3.7174138352765445, -1.4743118364040853, 1.2005883408445315, 1.1278212176008822,
        1.6044604396271898, 1.2992568079949507, 1.646291557065609]
SG_V = [1.152262370375282, 1.6334729820337168, 2.563012088391787e-07, 2.9131338017426884, 2.688671593225768,
        7.587172174184832, 4.361882764873106, 6.841037098302923]
SG_G = ["B", "B", "A", "A", "A", "A", "A", "A"]

# fuzz 20261004-00102: y_i = a + b·x1 pontosan (tökéletes illeszkedés), k = 23, két folytonos moderátor
PF_Y = [1.3271753248899099, 1.3345100544107207, 1.3280592172402386, 1.3389307418584628,
        1.336202397472115, 1.3339859027320249, 1.33790736091357, 1.3336280062218842,
        1.3383358019240839, 1.3264089748840864, 1.3309317426552307, 1.3306148470760621,
        1.3293695691479166, 1.3283945068766039, 1.3383454752180601, 1.3322539366768227,
        1.3304571711006739, 1.334789330297186, 1.3379657857839913, 1.328188617028421,
        1.3266576883942744, 1.337600018268719, 1.3360952042385137]
PF_V = [0.0002627150745139089, 0.00018816922368246827, 0.0009704745137191763, 0.00011395041150819321,
        0.0002503179443346947, 0.00013750387312333224, 0.00042031831270419406, 0.0001618207170136403,
        0.0009145725471986406, 0.0003731445024199088, 0.000576496820984357, 0.000622204822952638,
        0.00010155791873129699, 0.0006384014695224886, 0.000749201609784645, 0.00011669529851733344,
        0.00011767003552351993, 0.0002832406157212005, 0.00012166533328157251, 0.00011978300783023265,
        0.0001475625049200579, 0.0003350280144977589, 0.0005145503129670799]
PF_M = [[9.276817837719765, 4.801125792154459],
        [3.667952027487135, 1.4663027053056843],
        [8.60090558088477, 5.862822950448713],
        [0.28745345381572696, 6.273654994113615],
        [2.3738177162888907, 0.9183410757576349],
        [4.068770683672148, 4.300785660868813],
        [1.0700326036489927, 5.041704580883286],
        [4.342454054495092, 3.2086155040277067],
        [0.7424038712255765, 5.287699592348209],
        [9.862845493427978, 9.61685742961291],
        [6.404286122258518, 9.204387202301739],
        [6.646616091532641, 9.682906811574957],
        [7.59887980836039, 2.0423804886750396],
        [8.344509680722522, 0.09022377233593892],
        [0.7350067057674226, 5.616574008066516],
        [5.393204688217876, 9.655553156772354],
        [6.767190870049248, 4.478035166402291],
        [3.454389827712263, 4.1268980560588915],
        [1.0253551200085642, 9.053221162657834],
        [8.501953595576035, 9.48971421316436],
        [9.672654335065703, 1.0186460299085565],
        [1.3050574464655307, 7.086929612646086],
        [2.4557883549237225, 4.879517073957209]]


def _x(mods):
    return [[1.0] + [float(v) for v in row] for row in mods]


class TestD1RemlBoundary(unittest.TestCase):
    def test_D1_reml_interior_maximum_k13(self):
        # korábban: τ² = 0, becslés 0,317476, se 0,025724 (a Fisher-scoring oszcillált, a rács-tartalék
        # a [0, első rácspont] szakaszt nem vizsgálta)
        t2, info = M.tau2_reml(D1_Y, D1_V)
        assert_close(self, t2, 0.00387821218842357, 1e-7, rel=True)
        self.assertTrue(info["converged"])
        self.assertGreater(M.reml_loglik(D1_Y, D1_V, t2), M.reml_loglik(D1_Y, D1_V, 0.0) + 0.05)
        r = M.meta_analysis(D1_Y, D1_V, "random", "REML", "z")
        assert_close(self, r.estimate, 0.280646639135146, 1e-8, rel=True)
        assert_close(self, r.se, 0.0582428082778645, 1e-7, rel=True)
        self.assertFalse(any("nem konvergált" in w for w in r.warnings))

    def test_D1_ml_boundary_stays_zero(self):
        # ML-nél a maximum valóban a határon van (metafor: τ² = 0)
        r = M.meta_analysis(D1_Y, D1_V, "random", "ML", "z")
        self.assertEqual(r.tau2, 0.0)
        assert_close(self, r.estimate, 0.317475602982259, 1e-10, rel=True)
        assert_close(self, r.se, 0.0257236156452213, 1e-10, rel=True)

    def test_D1_reml_k38_extreme_variances(self):
        r = M.meta_analysis(D1B_Y, D1B_V, "random", "REML", "z")
        assert_close(self, r.tau2, 0.0744965518102723, 1e-6, rel=True)
        assert_close(self, r.estimate, 4.10520491433882, 1e-8, rel=True)
        assert_close(self, r.se, 0.190442441051176, 1e-6, rel=True)
        r = M.meta_analysis(D1B_Y, D1B_V, "random", "ML", "z")          # metafor ML: τ² = 0
        self.assertEqual(r.tau2, 0.0)
        assert_close(self, r.estimate, 4.42316864855764, 1e-10, rel=True)

    def test_D1_grid_fallback_refines_below_first_grid_point(self):
        # a Fisher-scoring nélküli tartalék (rács + arany-metszés) maga is a belső maximumot adja
        never = lambda s, step=1.0: (s, False, 1000)
        ll = lambda t: M.reml_loglik(D1_Y, D1_V, t)
        t2, info = M.optimize_tau2(ll, never, 0.0, M.variance_scale(D1_V), M._tau2_upper(D1_Y, D1_V))
        assert_close(self, t2, 0.00387821218842357, 1e-5, rel=True)
        self.assertIn("fallback", info)
        # valódi határ-maximumnál a tartalék is 0-t ad
        y, v = [0.1, 0.2, 0.15, 0.12], [0.1, 0.2, 0.3, 0.1]
        t0, _ = M.optimize_tau2(lambda t: M.reml_loglik(y, v, t), never, 0.0, M.variance_scale(v),
                                M._tau2_upper(y, v))
        self.assertEqual(t0, 0.0)

    def test_D1_leave1out_and_influence_share_the_optimizer(self):
        # metafor leave1out() / influence() (stepadj = 0.5)
        loo = S.leave_one_out(D1_Y, D1_V, ["s%d" % i for i in range(13)], "random", "REML", "z")
        for i, (t2, est, se) in ((0, (0.00292417404538661, 0.294146736319135, 0.0545412409368852)),
                                 (1, (0.00643597602126419, 0.257232903422628, 0.0686451831383025)),
                                 (11, (0.0, 0.148874579256876, 0.112305899088268))):
            assert_close(self, loo[i]["tau2"], t2, 1e-6 if t2 else 1e-12, rel=bool(t2))
            assert_close(self, loo[i]["estimate"], est, 1e-7, rel=True)
            assert_close(self, loo[i]["se"], se, 1e-6, rel=True)
        inf = S.influence(D1_Y, D1_V, ["s%d" % i for i in range(13)], "random", "REML")
        assert_close(self, inf[0]["hat"], 0.0563731755717609, 1e-6, rel=True)
        assert_close(self, inf[11]["hat"], 0.741218080877846, 1e-6, rel=True)


class TestD2MetaRegressionNumerics(unittest.TestCase):
    def test_D2_trace_and_dl_tau2_match_exact_arithmetic(self):
        # pontos (tests/fuzz/exact.py): tr(P) = 2,5780250912786973, tr(PP) = 5,149407366405894,
        # DL τ² = 3,7570802353942603; korábban tr(P) = 2,564204, tr(PP) = -25235 (!), τ² = 3,777331
        tr_p, tr_pp = MO._traces(D2_X, [1.0 / v for v in D2_V])
        assert_close(self, tr_p, 2.5780250912786973, 1e-12, rel=True)
        assert_close(self, tr_pp, 5.149407366405894, 1e-10, rel=True)
        t2, _ = MO._tau2_mr(D2_X, D2_Y, D2_V, "DL")
        assert_close(self, t2, 3.7570802353942603, 1e-11, rel=True)

    def test_D2_dl_coefficients_and_p_values(self):
        # pontos WLS a pontos τ²-nél; metafor: τ² 3,756727, p = 0,01520088 (a pontatlan τ² miatt)
        r = MO.meta_regression(D2_Y, D2_V, D2_X, ["i", "a", "b", "c"], "DL", "z")
        want_b = [13.398909778423365, 4350.297410542908, -4357.805462041116, -1.215575698761835]
        want_se = [5.519637734054431, 4194.772223483186, 4194.006917803421, 0.5680324356396442]
        want_p = [0.015203369014907459, 0.2997004601057272, 0.29877897988536495, 0.03235671784273242]
        for c, b, se, p in zip(r.coefficients, want_b, want_se, want_p):
            assert_close(self, c["estimate"], b, 1e-10, rel=True)
            assert_close(self, c["se"], se, 1e-10, rel=True)
            assert_close(self, c["p"], p, 1e-9, rel=True)
        assert_close(self, r.QM, 7.935075673053088, 1e-10, rel=True)

    def test_D2_collinear_fe_fit_matches_exact(self):
        # 20261005-00771: korábban QE = 64,80 (pontos: 24,1338), metafor QE = 0
        x = _x(C_M)
        r = MO.meta_regression(C_Y, C_V, x, ["i", "x1", "x2", "x3"], "FE", "z")
        assert_close(self, r.QE, 24.133814312912577, 1e-10, rel=True)
        for c, b, se in zip(r.coefficients,
                            [-2.28928934170967, 944.225868507283, -944.2147453968649, 0.39115668671009524],
                            [0.6956769334116432, 394.9635453819786, 394.86374944368663, 0.10729302015585486]):
            assert_close(self, c["estimate"], b, 1e-10, rel=True)
            assert_close(self, c["se"], se, 1e-10, rel=True)
        assert_close(self, r.QM, 27.07461292989402, 1e-10, rel=True)
        t2, _ = MO._tau2_mr(x, C_Y, C_V, "DL")
        assert_close(self, t2, 4.014063066846454, 1e-10, rel=True)
        tr_p, tr_pp = MO._traces(x, [1.0 / v for v in C_V])
        assert_close(self, tr_p, 3.770198440056417, 1e-11, rel=True)
        self.assertGreater(tr_pp, 0.0)

    def test_D2_common_tau2_subgroups(self):
        # fuzz 11-00054: pontos DL τ² 2,9817485483787958 (metafor rma(mods = ~g, method = "DL"): 2,98173929)
        sg = MO.subgroup_analysis(SG_Y, SG_V, SG_G, None, "random", "DL", "z", common_tau2=True)
        assert_close(self, sg.common_tau2, 2.9817485483787958, 1e-11, rel=True)

    def test_D2_traces_reduce_to_the_univariate_formula(self):
        # csak tengelymetszet: = models._reml_traces (domináns súllyal is, kiejtés nélkül)
        for v in ([0.1, 0.2, 0.3, 0.4], [1e-10, 1.0, 2.0, 0.5, 3.0], [2.5e-7, 1.0, 1.0]):
            w = [1.0 / a for a in v]
            got = MO._traces([[1.0]] * len(v), w)
            want = M._reml_traces(w)
            assert_close(self, got[0], want[0], 1e-12, rel=True)
            assert_close(self, got[1], want[1], 1e-12, rel=True)

    def test_D2_rank_deficient_design_still_refused(self):
        x = [[1.0, a, 2.0 * a] for a in (0.1, 0.5, 0.9, 1.3, 2.0, 2.2)]
        with self.assertRaises(la.SingularMatrixError):
            MO.meta_regression([0.1, 0.3, 0.2, 0.5, 0.4, 0.6], [0.1] * 6, x, ["i", "a", "b"], "REML")


class TestD3KnhaPerfectFit(unittest.TestCase):
    X = [[1.0, 0.0], [1.0, 1.0], [1.0, 1.0], [1.0, 0.0]]

    def _check_perfect(self, r, b0):
        assert_close(self, r.coefficients[0]["estimate"], b0, 1e-14)
        self.assertEqual(r.coefficients[1]["estimate"], 0.0)
        self.assertEqual([c["se"] for c in r.coefficients], [0.0, 0.0])     # metafor: se = 0, 0
        self.assertEqual(r.coefficients[0]["stat"], math.inf)               # metafor: z = Inf
        self.assertEqual(r.coefficients[0]["p"], 0.0)
        self.assertTrue(math.isnan(r.coefficients[1]["stat"]))              # 0/0: nem definiált
        self.assertTrue(math.isnan(r.coefficients[1]["p"]))
        self.assertIsNone(r.QM)                                             # metafor: QM = NA
        self.assertIsNone(r.QM_p)
        self.assertTrue(any("tökéletesen illeszkedik" in w for w in r.warnings))
        self.assertFalse(any("kollineáris" in w for w in r.warnings))

    def test_D3_identical_effects_equal_variances(self):
        # korábban: SingularMatrixError('szinguláris mátrix (kollineáris moderátorok?)')
        for m in MO.MR_TAU2_METHODS:
            self._check_perfect(MO.meta_regression([1.0] * 4, [1.0] * 4, self.X, ["i", "x"], m, "knha"), 1.0)

    def test_D3_identical_effects_unequal_variances(self):
        # korábban: a meredekség t = 1,0954, p = 0,388, QM = 1,2 — kerekítési zajból
        for m in MO.MR_TAU2_METHODS:
            self._check_perfect(MO.meta_regression([1.0] * 4, [0.1, 0.2, 0.3, 0.4], self.X, ["i", "x"], m,
                                                   "knha"), 1.0)

    def test_D3_z_test_unchanged_on_perfect_fit(self):
        r = MO.meta_regression([1.0] * 4, [1.0] * 4, self.X, ["i", "x"], "REML", "z")
        assert_close(self, r.coefficients[0]["se"], math.sqrt(0.5), 1e-12)
        assert_close(self, r.coefficients[1]["se"], 1.0, 1e-12)
        self.assertFalse(any("tökéletesen" in w for w in r.warnings))

    def test_D3_fuzz_20261004_00102_exact_linear_effects(self):
        # korábban: QM = 2,6e26; metafor (DL és REML, knha): b = 1,3393066455446752, -0,0013077027992770629,
        # -3,1e-17 (zaj), se = 0, 0, 0, z = Inf, -Inf, -Inf, QM = NA, QE = 0, τ² = 0
        for m in ("DL", "REML"):
            r = MO.meta_regression(PF_Y, PF_V, _x(PF_M), ["i", "x1", "x2"], m, "knha")
            self.assertEqual([c["se"] for c in r.coefficients], [0.0, 0.0, 0.0])
            self.assertIsNone(r.QM)
            self.assertIsNone(r.QM_p)
            assert_close(self, r.coefficients[0]["estimate"], 1.3393066455446752, 1e-13, rel=True)
            assert_close(self, r.coefficients[1]["estimate"], -0.0013077027992770629, 1e-11, rel=True)
            self.assertEqual(r.coefficients[0]["stat"], math.inf)
            self.assertEqual(r.coefficients[1]["stat"], -math.inf)
            self.assertEqual(r.coefficients[2]["estimate"], 0.0)     # pontos aritmetikában 0 (-3,5e-18)
            self.assertTrue(math.isnan(r.coefficients[2]["stat"]))
            self.assertEqual(r.tau2, 0.0)

    def test_D3_regular_knha_unaffected(self):
        x = [[1.0, a] for a in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)]
        y = [0.1, 0.35, 0.2, 0.6, 0.55, 0.9]
        r = MO.meta_regression(y, [0.05, 0.04, 0.06, 0.05, 0.03, 0.07], x, ["i", "x"], "FE", "knha")
        self.assertIsNotNone(r.QM)
        self.assertTrue(all(c["se"] > 0 for c in r.coefficients))
        assert_close(self, r.QM, r.coefficients[1]["stat"] ** 2, 1e-10, rel=True)   # F(1, df) = t²
        self.assertFalse(any("tökéletesen" in w for w in r.warnings))


class TestD4BeggIdenticalEffects(unittest.TestCase):
    def test_D4_batch_reproduction_is_undefined(self):
        # metafor ranktest: tau = NA, p = NA ('the standard deviation is zero'); korábban τ = 1, p = 0,0167
        with self.assertRaises(M.ModelError):
            B.begg_test([0.1] * 5, [0.011, 0.023, 0.031, 0.047, 0.052])

    def test_D4_deterministic_for_any_magnitude(self):
        rnd = random.Random(4)
        vi = [0.011, 0.023, 0.031, 0.047, 0.052]
        for y in [0.1, -0.06655916410087137, 1e-8, 1234.5] + [rnd.uniform(-5, 5) for _ in range(200)]:
            for method in ("auto", "exact", "normal"):
                with self.assertRaises(M.ModelError, msg=repr(y)):
                    B.begg_test([y] * 5, vi, method=method)


if __name__ == "__main__":
    unittest.main()
