# -*- coding: utf-8 -*-
"""v1 E4c: kumulatív és buborékábra a szk.ma.plot/v2-ben, a motor-SVG-jük, és a meta-regresszió konfidencia- és
predikciós sávja a metafor predict()-jével összevetve.

- moderators.predict: ŷ, CI, PI és az együttható-kovariancia a metafor predict(rma(yi, vi, mods = ~x), newmods = …)
  értékeivel (hard-coded referencia több x-nél: BCG RR/OR szélesség/év szerint, REML/DL/FE, knha/z, 90% és 95%,
  valamint egy szintetikus, negatív moderátor-értékes GEN-adatsor minden τ²-becslővel, τ² = 0 határesettel);
- a v2 'bubble' blokk: a rács pontjain ugyanezek az értékek (a motor rácsa = a referencia x-ei), a buborék-súly a
  metafor weights()-e, a pontok, a tengelyek, a koefficiens-szöveg, a kezdőbarát magyarázat; csak egyetlen folytonos
  moderátornál van blokk;
- a v2 'cumulative' blokk: rendezés (oszlop, irány, hiányzó és vegyes kulcsok), row_index, magyarázat; a lépések a
  metafor cumul()-jával;
- motor-SVG (cumulative.svg, bubble.svg, LOO): a motor kész szövegei szó szerint (a munkapad szerveroldali
  számhűség-ellenőrzőjével is), hu/en, U+2212, annotált rétegek és sor-azonosítók, a v2 fájlból újrarajzolva
  bájtra ugyanaz; a kérés nélküli kimenet változatlan;
- a contracts/ma.plot.v2.schema.json additív bővítése.

A referencia generálása (R 4.4.0, metafor 4.4.0): lásd R_SCRIPT; a live teszt ezt futtatja, ha van Rscript + metafor.
"""
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import xml.dom.minidom

from _helpers import ROOT, assert_close
from metaelemzes import moderators as MO
from metaelemzes import pipeline, tableio
from metaelemzes import plots as P
from metaelemzes.models import ModelError

PELDAK = os.path.join(ROOT, "peldak")
BCG = os.path.join(PELDAK, "bcg_oltas_RR.csv")
NORMAND = os.path.join(PELDAK, "normand1999_folytonos.csv")
MOLLOY = os.path.join(PELDAK, "molloy2014_korrelacio.csv")
PRITZ = os.path.join(PELDAK, "pritz1997_arany.csv")
SCHEMA_PATH = os.path.join(ROOT, "metaelemzes", "contracts", "ma.plot.v2.schema.json")
SVGAUDIT = os.path.join(ROOT, "ma_gui", "adapters", "svgaudit.py")
SCHEMA_LITE = os.path.join(ROOT, "ma_gui", "schema_lite.py")
TOL = 1e-8          # a motor és a metafor eltérése ≤ 2e-10 (lásd a modul leírását)

# szintetikus GEN-adatsor (centrált dózis, negatív értékekkel) — heterogén és homogén (τ² = 0) változat
GEN_DOSE = [-2.5, -1.0, -3.0, 0.5, -1.5, 2.0, -0.5, -3.5, 1.5, 1.0, -2.0, 3.0]
GEN_VI = [0.04, 0.03, 0.05, 0.02, 0.06, 0.03, 0.04, 0.07, 0.025, 0.035, 0.05, 0.03]
GEN_YI = [0.35, -0.08, 0.16, -0.01, 0.58, 0.51, 0.06, 0.32, 0.27, 0.73, -0.26, 0.74]
GEN_YI_HOM = [0.12, 0.35, -0.05, 0.48, 0.22, 0.61, 0.30, -0.10, 0.55, 0.40, 0.18, 0.70]

R_SCRIPT = r"""
suppressMessages(library(metafor))
ctl <- list(threshold=1e-12, maxiter=10000, stepadj=0.5, tol=1e-13)
f <- function(v) paste0(sprintf("%.15g", v), collapse=" ")
show <- function(tag, res, xs, level=95) {
  p <- predict(res, newmods=xs, level=level)
  cat(tag, "tau2", f(res$tau2), "\n"); cat(tag, "x", f(xs), "\n"); cat(tag, "pred", f(p$pred), "\n")
  cat(tag, "ci_lb", f(p$ci.lb), "\n"); cat(tag, "ci_ub", f(p$ci.ub), "\n")
  pl <- if (is.null(p$pi.lb)) p$ci.lb else p$pi.lb     # FE: nincs PI (τ² = 0 → PI = CI)
  pu <- if (is.null(p$pi.ub)) p$ci.ub else p$pi.ub
  cat(tag, "pi_lb", f(pl), "\n"); cat(tag, "pi_ub", f(pu), "\n"); cat(tag, "vb", f(as.vector(res$vb)), "\n")
}
dat <- escalc(measure="RR", ai=tpos, bi=tneg, ci=cpos, di=cneg, data=dat.bcg)
xa <- 13 + 42 * c(0, 10, 25, 37, 50) / 50
show("bcg_rr_ablat_reml_knha", rma(yi, vi, mods=~ablat, data=dat, method="REML", test="knha", control=ctl), xa)
show("bcg_rr_ablat_reml_z", rma(yi, vi, mods=~ablat, data=dat, method="REML", test="z", control=ctl), xa)
show("bcg_rr_ablat_reml_knha_l90", rma(yi, vi, mods=~ablat, data=dat, method="REML", test="knha", control=ctl), xa,
     level=90)
xy <- 1948 + 32 * c(0, 12, 25, 50) / 50
dor <- escalc(measure="OR", ai=tpos, bi=tneg, ci=cpos, di=cneg, data=dat.bcg)
show("bcg_or_year_dl_knha", rma(yi, vi, mods=~year, data=dor, method="DL", test="knha", control=ctl), xy)
show("bcg_rr_year_fe_z", rma(yi, vi, mods=~year, data=dat, method="FE", test="z", control=ctl), xy)
xg <- -3.5 + 6.5 * c(0, 7, 20, 33, 50) / 50
h <- data.frame(yi=c(0.12, 0.35, -0.05, 0.48, 0.22, 0.61, 0.30, -0.10, 0.55, 0.40, 0.18, 0.70),
                vi=c(0.04, 0.03, 0.05, 0.02, 0.06, 0.03, 0.04, 0.07, 0.025, 0.035, 0.05, 0.03),
                dose=c(-2.5, -1.0, -3.0, 0.5, -1.5, 2.0, -0.5, -3.5, 1.5, 1.0, -2.0, 3.0))
g <- h; g$yi <- c(0.35, -0.08, 0.16, -0.01, 0.58, 0.51, 0.06, 0.32, 0.27, 0.73, -0.26, 0.74)
show("gen_hom_reml_knha", rma(yi, vi, mods=~dose, data=h, method="REML", test="knha", control=ctl), xg)
for (m in c("REML", "ML", "PM", "SJ", "HE", "DL")) for (tst in c("knha", "z"))
  show(sprintf("gen_dose_%s_%s", tolower(m), tst), rma(yi, vi, mods=~dose, data=g, method=m, test=tst, control=ctl), xg)
"""

METAFOR_PREDICT = {
    'bcg_rr_ablat_reml_knha': {
        'tau2': 0.0763479639573262, 'x': [13.0, 21.4, 34.0, 44.08, 55.0],
        'pred': [-0.126854215145048, -0.371308705242736, -0.737990440389267, -1.03133582850649, -1.34912666563349],
        'ci_lb': [-0.551663403452762, -0.692724592176064, -1.00964269346921, -1.38443269418638, -1.85362938910614],
        'ci_ub': [0.297954973162665, -0.0498928183094077, -0.466338187309329, -0.678238962826604, -0.844623942160836],
        'pi_lb': [-0.868688436503462, -1.05917739756775, -1.40406109137998, -1.73456592034041, -2.13930269886855],
        'pi_ub': [0.614980006213365, 0.316559987082277, -0.0719197893985543, -0.32810573667257, -0.558950632398418],
        'vb': [0.0806135667496124, -0.00210495054519453, -0.00210495054519453, 6.7263247550698e-05],
    },
    'bcg_rr_ablat_reml_z': {
        'tau2': 0.0763479639573262, 'x': [13.0, 21.4, 34.0, 44.08, 55.0],
        'pred': [-0.126854215145048, -0.371308705242736, -0.737990440389267, -1.03133582850649, -1.34912666563349],
        'ci_lb': [-0.458738364730805, -0.622416358359104, -0.950220023642029, -1.30719439327624, -1.74327175936469],
        'ci_ub': [0.205029934440708, -0.120201052126368, -0.525760857136505, -0.755477263736748, -0.954981571902286],
        'pi_lb': [-0.762019271120398, -0.968253102183957, -1.31965095614872, -1.6391067617887, -2.01893105088211],
        'pi_ub': [0.508310840830301, 0.225635691698485, -0.156329924629818, -0.42356489522428, -0.679322280384864],
        'vb': [0.0620485166169633, -0.00162018707455363, -0.00162018707455363, 5.17727338169226e-05],
    },
    'bcg_rr_ablat_reml_knha_l90': {
        'tau2': 0.0763479639573262, 'x': [13.0, 21.4, 34.0, 44.08, 55.0],
        'pred': [-0.126854215145048, -0.371308705242736, -0.737990440389267, -1.03133582850649, -1.34912666563349],
        'ci_lb': [-0.473475530890788, -0.633566680580392, -0.959643982684254, -1.31944376858006, -1.76077359470304],
        'ci_ub': [0.219767100600691, -0.10905072990508, -0.516336898094281, -0.743227888432925, -0.937479736563935],
        'pi_lb': [-0.732150807002688, -0.932572344910738, -1.28146805758898, -1.60513353913159, -1.99386756072046],
        'pi_ub': [0.478442376712591, 0.189954934425266, -0.194512823189551, -0.457538117881397, -0.704385770546508],
        'vb': [0.0806135667496124, -0.00210495054519453, -0.00210495054519453, 6.7263247550698e-05],
    },
    'bcg_or_year_dl_knha': {
        'tau2': 0.296825911664378, 'x': [1948.0, 1955.68, 1964.0, 1980.0],
        'pred': [-1.34108851133985, -1.10626043684002, -0.851863356131879, -0.362638200923904],
        'ci_lb': [-2.14036386242408, -1.67532587165979, -1.24640847308317, -0.94419455162974],
        'ci_ub': [-0.541813160255613, -0.537195002020249, -0.457318239180593, 0.218918149781933],
        'pi_lb': [-2.78218720330173, -2.43357344522576, -2.1142381825735, -1.69535427802724],
        'pi_ub': [0.10001018062203, 0.22105257154573, 0.410511470309742, 0.97007787617943],
        'vb': [1039.14653323944, -0.528110983971928, -0.528110983971928, 0.000268401892253685],
    },
    'bcg_rr_year_fe_z': {
        'tau2': 0.0, 'x': [1948.0, 1955.68, 1964.0, 1980.0],
        'pred': [-1.02778342224538, -0.827137724773117, -0.609771552511496, -0.191759682777608],
        'ci_lb': [-1.20846607600015, -0.961014231163419, -0.702927042985384, -0.294225151051147],
        'ci_ub': [-0.847100768490617, -0.693261218382814, -0.516616062037609, -0.0892942145040688],
        # közös hatású modell: a metafor nem ad PI-t; τ² = 0, így a predikciós sáv = a konfidenciasáv
        'pi_lb': [-1.20846607600015, -0.961014231163419, -0.702927042985384, -0.294225151051147],
        'pi_ub': [-0.847100768490617, -0.693261218382814, -0.516616062037609, -0.0892942145040688],
        'vb': [50.9338859148449, -0.0258425178988075, -0.0258425178988075, 1.31122380587544e-05],
    },
    'gen_hom_reml_knha': {
        'tau2': 0.0, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [-0.021000226738757, 0.0828264816240023, 0.275647511440555, 0.468468541257108, 0.720619118709523],
        'ci_lb': [-0.096321585556902, 0.0220690706424448, 0.235810889772608, 0.429336339158111, 0.653164760903027],
        'ci_ub': [0.054321132079388, 0.14358389260556, 0.315484133108503, 0.507600743356105, 0.78807347651602],
        'pi_lb': [-0.096321585556902, 0.0220690706424448, 0.235810889772608, 0.429336339158111, 0.653164760903027],
        'pi_ub': [0.054321132079388, 0.14358389260556, 0.315484133108503, 0.507600743356105, 0.78807347651602],
        'vb': [0.00026231858777378, 6.58646381662815e-07, 6.58646381662815e-07, 7.22484415577809e-05],
    },
    'gen_dose_reml_knha': {
        'tau2': 0.0516661819971169, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0452907786724064, 0.111432414916644, 0.234266882227371, 0.357101349538099, 0.517731037559819],
        'ci_lb': [-0.322031645268802, -0.18424228669236, 0.0326948349135582, 0.138713837073887, 0.145996100879414],
        'ci_ub': [0.412613202613615, 0.407107116525649, 0.435838929541185, 0.57548886200231, 0.889465974240224],
        'pi_lb': [-0.580351157286991, -0.475019286481306, -0.310832520780245, -0.194437565227925, -0.110511695824151],
        'pi_ub': [0.670932714631804, 0.697884116314595, 0.779366285234987, 0.908640264304122, 1.14597377094379],
        'vb': [0.00759150024386403, 0.000525284529662615, 0.000525284529662615, 0.00189902418483199],
    },
    'gen_dose_reml_z': {
        'tau2': 0.0516661819971169, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0452907786724064, 0.111432414916644, 0.234266882227371, 0.357101349538099, 0.517731037559819],
        'ci_lb': [-0.278256597072634, -0.149005738445598, 0.05671685347558, 0.164739811151343, 0.190297003053546],
        'ci_ub': [0.368838154417447, 0.371870568278887, 0.411816910979163, 0.549462887924854, 0.845165072066092],
        'pi_lb': [-0.505305638784078, -0.404611713323517, -0.245313687931778, -0.128157830709872, -0.035158243867727],
        'pi_ub': [0.595887196128891, 0.627476543156805, 0.713847452386521, 0.842360529786069, 1.07062031898736],
        'vb': [0.00761196550395821, 0.00052670059818368, 0.00052670059818368, 0.00190414359767784],
    },
    'gen_dose_ml_knha': {
        'tau2': 0.0371595401622593, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0392641584559087, 0.10615708533367, 0.230386806678085, 0.354616528022499, 0.517070779011349],
        'ci_lb': [-0.331735418425718, -0.192595782276388, 0.0279202439351796, 0.13825008360841, 0.148247063171785],
        'ci_ub': [0.410263735337536, 0.404909952943729, 0.43285336942099, 0.570982972436589, 0.885894494850913],
        'pi_lb': [-0.528294685270158, -0.41704042130274, -0.24445533980246, -0.126316724429147, -0.0490681512973052],
        'pi_ub': [0.606823002181976, 0.629354591970081, 0.705228953158629, 0.835549780474146, 1.08320970932],
        'vb': [0.00752555509332975, 0.00045182613465347, 0.00045182613465347, 0.00190707254477436],
    },
    'gen_dose_ml_z': {
        'tau2': 0.0371595401622593, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0392641584559087, 0.10615708533367, 0.230386806678085, 0.354616528022499, 0.517070779011349],
        'ci_lb': [-0.259718678483834, -0.134603242892899, 0.0672221264832429, 0.180250147572071, 0.219841434926263],
        'ci_ub': [0.338246995395652, 0.34691741356024, 0.393551486872927, 0.528982908472928, 0.814300123096435],
        'pi_lb': [-0.442542421110815, -0.341852266105576, -0.181158520342322, -0.061497011724325, 0.0363503547370115],
        'pi_ub': [0.521070738022632, 0.554166436772916, 0.641932133698492, 0.770730067769324, 0.997791203285686],
        'vb': [0.0063164425240435, 0.000379232332366945, 0.000379232332366945, 0.00160066785358139],
    },
    'gen_dose_pm_knha': {
        'tau2': 0.0514258678860261, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0452067669314937, 0.111359101653292, 0.234213437565203, 0.357067773477114, 0.517723443515767],
        'ci_lb': [-0.322166425931633, -0.184358147660677, 0.0326293534024556, 0.13870901100491, 0.14603013273767],
        'ci_ub': [0.41257995979462, 0.407076350967261, 0.435797521727951, 0.575426535949318, 0.889416754293864],
        'pi_lb': [-0.579510823376436, -0.474096017333226, -0.309794970268198, -0.1933770956552, -0.109544379732708],
        'pi_ub': [0.669924357239423, 0.69681422063981, 0.778221845398604, 0.907512642609428, 1.14499126676424],
        'vb': [0.00759053753770073, 0.00052425268108914, 0.00052425268108914, 0.00189912646204547],
    },
    'gen_dose_pm_z': {
        'tau2': 0.0514258678860261, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0452067669314937, 0.111359101653292, 0.234213437565203, 0.357067773477114, 0.517723443515767],
        'ci_lb': [-0.27795003555203, -0.14876614042753, 0.0568916586898627, 0.164990287025709, 0.190766484899801],
        'ci_ub': [0.368363569415018, 0.371484343734115, 0.411535216440544, 0.54914525992852, 0.844680402131733],
        'pi_lb': [-0.504320914622403, -0.403631670468143, -0.244319076550756, -0.127126520793394, -0.0340475294340864],
        'pi_ub': [0.59473444848539, 0.626349873774727, 0.712745951681162, 0.841262067747622, 1.06949441646562],
        'vb': [0.0075905375377003, 0.00052425268108911, 0.00052425268108911, 0.00189912646204536],
    },
    'gen_dose_sj_knha': {
        'tau2': 0.0638116727060356, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.049027400600546, 0.114686866267083, 0.236625873933508, 0.358564881599934, 0.518023583932952],
        'ci_lb': [-0.316050783577853, -0.179105788997607, 0.0355766743087672, 0.138882938295163, 0.144409285920138],
        'ci_ub': [0.414105584778945, 0.408479521531772, 0.437675073558249, 0.578246824904705, 0.891637881945765],
        'pi_lb': [-0.621853875007462, -0.520225513267217, -0.361053292566529, -0.245636874117965, -0.157540809058596],
        'pi_ub': [0.719908676208554, 0.749599245801383, 0.834305040433546, 0.962766637317832, 1.1935879769245],
        'vb': [0.00763554774757475, 0.000571386732049765, 0.000571386732049765, 0.00189474561602975],
    },
    'gen_dose_sj_z': {
        'tau2': 0.0638116727060356, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.049027400600546, 0.114686866267083, 0.236625873933508, 0.358564881599934, 0.518023583932952],
        'ci_lb': [-0.293644519311488, -0.16107459415042, 0.0479158435403417, 0.15236567128101, 0.16733944460329],
        'ci_ub': [0.39169932051258, 0.390448326684586, 0.425335904326675, 0.564764091918857, 0.868707723262613],
        'pi_lb': [-0.553097136082727, -0.452035549057599, -0.29322447149512, -0.1777634015588, -0.0886965150890211],
        'pi_ub': [0.651151937283819, 0.681409281591764, 0.766476219362137, 0.894893164758667, 1.12474368295492],
        'vb': [0.00869388298089022, 0.000650584548679139, 0.000650584548679139, 0.00215736934780476],
    },
    'gen_dose_he_knha': {
        'tau2': 0.051185352233677, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0451222369152162, 0.111285329748572, 0.234159645010518, 0.357033960272464, 0.51771575715347],
        'ci_lb': [-0.322302051966814, -0.184474740102752, 0.0325634372946504, 0.138704109157925, 0.146064300406599],
        'ci_ub': [0.412546525797246, 0.407045399599895, 0.435755852726385, 0.575363811387003, 0.889367213900342],
        'pi_lb': [-0.578669032820583, -0.473170792690568, -0.308754686264274, -0.192313711084967, -0.108574706960592],
        'pi_ub': [0.668913506651015, 0.695741452187712, 0.77707397628531, 0.906381631629895, 1.14400622126753],
        'vb': [0.00758957012506273, 0.000523214681240917, 0.000523214681240917, 0.00189922964554341],
    },
    'gen_dose_he_z': {
        'tau2': 0.051185352233677, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0451222369152162, 0.111285329748572, 0.234159645010518, 0.357033960272464, 0.51771575715347],
        'ci_lb': [-0.277643158466171, -0.14852633423818, 0.0570666175223652, 0.165241194113857, 0.191236988572422],
        'ci_ub': [0.367887632296603, 0.371096993735323, 0.41125267249867, 0.548826726431071, 0.844194525734519],
        'pi_lb': [-0.503333707791464, -0.402649027511146, -0.243321625002573, -0.126092201282843, -0.0329336859249659],
        'pi_ub': [0.593578181621897, 0.625219687008289, 0.711640915023608, 0.84016012182777, 1.06836520023191],
        'vb': [0.00756909066688871, 0.000521802855142214, 0.000521802855142214, 0.00189410482378831],
    },
    'gen_dose_dl_knha': {
        'tau2': 0.0520168534320177, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0454125754299265, 0.111538690268333, 0.234344332111087, 0.357149973953841, 0.517741967132827],
        'ci_lb': [-0.321836270265487, -0.18407434564642, 0.0327897134159407, 0.138720752834262, 0.145946632600566],
        'ci_ub': [0.41266142112534, 0.407151726183085, 0.435898950806233, 0.57557919507342, 0.889537301665088],
        'pi_lb': [-0.58157604323348, -0.476364431233476, -0.312343218127785, -0.195981419033159, -0.111920469454261],
        'pi_ub': [0.672401194093333, 0.699441811770141, 0.781031882349959, 0.910281366940841, 1.14740440371992],
        'vb': [0.00759289810344264, 0.000526780837372428, 0.000526780837372428, 0.00189887639127924],
    },
    'gen_dose_dl_z': {
        'tau2': 0.0520168534320177, 'x': [-3.5, -2.59, -0.9, 0.79, 3.0],
        'pred': [0.0454125754299265, 0.111538690268333, 0.234344332111087, 0.357149973953841, 0.517741967132827],
        'ci_lb': [-0.278703830101181, -0.149355348615298, 0.0564617892648001, 0.164374707694858, 0.189613046281771],
        'ci_ub': [0.369528980961034, 0.372432729151964, 0.412226874957374, 0.549925240212824, 0.845870887983884],
        'pi_lb': [-0.50673961805986, -0.406038646915388, -0.246761476927464, -0.129658924611499, -0.0367751102778294],
        'pi_ub': [0.597564768919713, 0.629116027452054, 0.715450141149638, 0.843958872519181, 1.07225904454348],
        'vb': [0.00764323198896535, 0.000530272906672271, 0.000530272906672271, 0.00191146418392419],
    },
}

# metafor weights(rma(yi, vi, mods = ~ablat, data = dat.bcg-RR, method = "REML")) — a buborékok súlya (%)
METAFOR_WEIGHTS_ABLAT = [2.8228688019066, 4.18782412955535, 2.30743665881812, 11.7748750472509, 8.89479413465826,
                         13.6282827496637, 3.79003076293538, 14.1278771496104, 8.5448469721263, 7.59578506486094,
                         12.7827972805444, 1.86350704222421, 7.67907420584548]
# metafor cumul(rma(yi, vi, data = dat.bcg-RR, method = "REML", test = "knha"), order = year): a lépések sorrendje
# (0-alapú sorindex) és néhány lépés (k: becslés, CI alsó, CI felső, τ²) a log RR skálán
METAFOR_CUMUL_ORDER = [0, 1, 5, 2, 9, 8, 11, 4, 6, 10, 12, 3, 7]
METAFOR_CUMUL = {
    1: (-0.889311333920205, -2.00766747907616, 0.229044811235751, 0.0),
    2: (-1.32500344224488, -5.60470865844108, 2.95470177395133, 0.0),
    3: (-0.972079931812824, -1.95119919485508, 0.00703933122943246, 0.087206792838218),
    7: (-0.901251100272772, -1.40314344022821, -0.399358760317336, 0.120459721860583),
    13: (-0.714532342158211, -1.10844371368566, -0.320620970630764, 0.313243258137274),
}
QT_975_11, QNORM_975, QT_95_11 = 2.20098516009164, 1.95996398454005, 1.79588481870404
BCG_GRID_IDX = (0, 10, 25, 37, 50)       # a motor 51 pontos rácsán ezek az x-ek = a referencia x-ei


def analyze(path, filters=None, **opts):
    rows, meta = tableio.read_table(path)
    if filters is not None:
        rows = [r for i, r in enumerate(rows) if i in filters]
    return pipeline.run(rows, opts, meta)


def doc_of(path, **opts):
    out, es = analyze(path, **opts)
    return out, es, pipeline.plot_document(out, es)


def gen_mr(tag):
    _, meth, test = tag.rsplit("_", 2)
    yi = GEN_YI_HOM if tag.startswith("gen_hom") else GEN_YI
    return MO.meta_regression(yi, GEN_VI, [[1.0, d] for d in GEN_DOSE], ["intercept", "dose"], meth.upper(), test)


def bcg_mr(tag):
    parts = tag.split("_")
    level = 0.90 if tag.endswith("_l90") else 0.95
    out, es = analyze(BCG, measure=parts[1].upper(), moderators=["szélesség" if parts[2] == "ablat" else "év"],
                      metareg_tau2=parts[3].upper(), metareg_test=parts[4], level=level)
    return out["metaregression"]


def svg_text(svg):
    doc = xml.dom.minidom.parseString(svg.encode("utf-8"))
    return ["".join(n.data for n in t.childNodes if n.nodeType == n.TEXT_NODE) for t in doc.getElementsByTagName("text")]


def unwrap(svg):
    """Az annotált SVG sorai a <g>-burkolók nélkül, U+2212 → '-' (az annotálatlan SVG sorainak multihalmaza)."""
    lines = []
    for ln in svg.split("\n"):
        if (ln.startswith("<g id=") and ln.endswith(">") and "</g>" not in ln) or ln == "</g>":
            continue
        m = re.match(r'^<g id="[^"]+"[^>]*>(.*)</g>$', ln)
        lines.append((m.group(1) if m else ln).replace(P.MINUS, "-"))
    return sorted(lines)


def load_file_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return p

    def gen_csv(self, yi=GEN_YI, extra=None, name="gen.csv"):
        head = "study,yi,vi,dose" + ("".join("," + k for k in (extra or {})))
        lines = [head]
        for i, (y, v, d) in enumerate(zip(yi, GEN_VI, GEN_DOSE)):
            lines.append("S%02d,%r,%r,%r" % (i + 1, y, v, d) + "".join(",%s" % vals[i] for vals in (extra or {}).values()))
        return self.write(name, "\n".join(lines) + "\n")


# ------------------------------------------------------------------ predict() a metafor ellen
class TestPredictAgainstMetafor(unittest.TestCase):
    def check(self, tag, mr, ref):
        assert_close(self, mr.tau2, ref["tau2"], TOL, tag + " tau2")
        vb = [c for row in mr._vcov for c in row]          # 2×2 szimmetrikus: sor- és oszlopfolytonos ugyanaz
        for j, (a, b) in enumerate(zip(vb, ref["vb"])):
            assert_close(self, a, b, TOL, "%s vb[%d]" % (tag, j))
        level = 0.90 if tag.endswith("_l90") else None
        for rk in ("pred", "ci_lb", "ci_ub", "pi_lb", "pi_ub"):
            self.assertEqual(len(ref[rk]), len(ref["x"]), "%s: %s hossza" % (tag, rk))       # nincs csendes kihagyás
        pr = MO.predict(mr, [[1.0, x] for x in ref["x"]], level=level)
        for key, rk in (("pred", "pred"), ("ci_lower", "ci_lb"), ("ci_upper", "ci_ub"), ("pi_lower", "pi_lb"),
                        ("pi_upper", "pi_ub")):
            for j, (p, want) in enumerate(zip(pr, ref[rk])):
                assert_close(self, p[key], want, TOL, "%s %s x=%g" % (tag, key, ref["x"][j]))

    def test_hardcoded_reference_all_cases(self):
        self.assertEqual(len(METAFOR_PREDICT), 18)
        for tag, ref in METAFOR_PREDICT.items():
            with self.subTest(case=tag):
                mr = bcg_mr(tag) if tag.startswith("bcg") else gen_mr(tag)
                self.check(tag, mr, ref)

    def test_band_formula_and_crit(self):
        mr = bcg_mr("bcg_rr_ablat_reml_knha")
        assert_close(self, MO.prediction_crit(mr), QT_975_11, 1e-12, "t(11)")
        assert_close(self, MO.prediction_crit(mr, 0.90), QT_95_11, 1e-12, "t(11) 90%")
        assert_close(self, MO.prediction_crit(bcg_mr("bcg_rr_ablat_reml_z")), QNORM_975, 1e-12, "z")
        p = MO.predict(mr, [[1.0, 30.0]])[0]
        se2 = sum(a * sum(mr._vcov[i][j] * b for j, b in enumerate((1.0, 30.0))) for i, a in enumerate((1.0, 30.0)))
        assert_close(self, p["se"], math.sqrt(se2), 1e-14, "se")
        assert_close(self, p["pi_upper"] - p["pred"], QT_975_11 * math.sqrt(se2 + mr.tau2), 1e-12, "PI fél-szélesség")
        # τ² = 0 (homogén): a predikciós sáv = a konfidenciasáv
        h = gen_mr("gen_hom_reml_knha")
        for q in MO.predict(h, [[1.0, -1.0], [1.0, 2.0]]):
            self.assertEqual((q["pi_lower"], q["pi_upper"]), (q["ci_lower"], q["ci_upper"]))

    def test_predict_errors_and_results_json_unchanged(self):
        mr = bcg_mr("bcg_rr_ablat_reml_knha")
        with self.assertRaises(ModelError):
            MO.predict(mr, [[1.0, 2.0, 3.0]])
        old = MO.MetaResult(**{k: v for k, v in mr.to_dict().items()})      # régi (kovariancia nélküli) eredmény
        with self.assertRaises(ModelError):
            MO.predict(old, [[1.0, 2.0]])
        # a kovariancia aláhúzásos mező: a results.json nem változik
        out, es = analyze(BCG, measure="RR", moderators=["szélesség"])
        self.assertNotIn("_vcov", out["metaregression"].to_dict())
        self.assertNotIn("vcov", json.dumps(pipeline.to_jsonable(out), ensure_ascii=False))

    @unittest.skipUnless(shutil.which("Rscript"), "nincs Rscript (a hard-coded referencia ugyanezt ellenőrzi)")
    def test_live_metafor(self):
        try:
            ok = subprocess.run(["Rscript", "-e", "quit(status = !requireNamespace('metafor', quietly = TRUE))"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            self.skipTest("az R-ben nincs metafor csomag")
        proc = subprocess.run(["Rscript", "-e", R_SCRIPT], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        live = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                live.setdefault(parts[0], {})[parts[1]] = [float(v) for v in parts[2:]]
        self.assertEqual(sorted(live), sorted(METAFOR_PREDICT))
        for tag, ref in live.items():
            with self.subTest(case=tag):
                ref = dict(ref, tau2=ref["tau2"][0])
                # a hard-coded érték maga is a mostani metafor eredménye (15 értékes jegy)
                for key in ("pred", "ci_lb", "pi_ub"):
                    for a, b in zip(ref[key], METAFOR_PREDICT[tag][key]):
                        assert_close(self, a, b, 1e-12, "%s %s hard-coded = live" % (tag, key))
                self.check(tag, bcg_mr(tag) if tag.startswith("bcg") else gen_mr(tag), ref)


# ------------------------------------------------------------------ v2 'bubble'
class TestBubbleBlock(_Tmp):
    def test_band_on_engine_grid_equals_metafor(self):
        for tag, opts in (("bcg_rr_ablat_reml_knha", {}), ("bcg_rr_ablat_reml_z", {"metareg_test": "z"}),
                          ("bcg_rr_ablat_reml_knha_l90", {"level": 0.90})):
            with self.subTest(case=tag):
                out, es, doc = doc_of(BCG, measure="RR", moderators=["szélesség"], **opts)
                b = doc["bubble"]
                ref = METAFOR_PREDICT[tag]
                self.assertEqual(b["grid_n"], pipeline.BUBBLE_GRID_N)
                self.assertEqual(len(b["line"]), 51)
                self.assertEqual(len(b["band"]), 51)
                self.assertEqual(len(b["pi_band"]), 51)
                self.assertEqual(b["x_range"], [13.0, 55.0])
                self.assertEqual((b["line"][0][0], b["line"][-1][0]), (13.0, 55.0))
                for j, gi in enumerate(BCG_GRID_IDX):
                    x, lo, hi = b["band"][gi]
                    assert_close(self, x, ref["x"][j], 1e-12, "x")
                    self.assertEqual(b["line"][gi][0], x)
                    self.assertEqual(b["pi_band"][gi][0], x)
                    assert_close(self, b["line"][gi][1], ref["pred"][j], TOL, "%s ŷ(%g)" % (tag, x))
                    assert_close(self, lo, ref["ci_lb"][j], TOL, "%s CI alsó(%g)" % (tag, x))
                    assert_close(self, hi, ref["ci_ub"][j], TOL, "%s CI felső(%g)" % (tag, x))
                    assert_close(self, b["pi_band"][gi][1], ref["pi_lb"][j], TOL, "%s PI alsó(%g)" % (tag, x))
                    assert_close(self, b["pi_band"][gi][2], ref["pi_ub"][j], TOL, "%s PI felső(%g)" % (tag, x))
                for a, w in zip([c for row in b["vcov"] for c in row], ref["vb"]):
                    assert_close(self, a, w, TOL, tag + " vcov")
                # a sáv a vonalat közrefogja, a predikciós sáv a konfidenciasávot
                for (x, y), (_, lo, hi), (_, plo, phi) in zip(b["line"], b["band"], b["pi_band"]):
                    self.assertTrue(plo <= lo <= y <= hi <= phi)
                lv = 90 if "l90" in tag else 95
                self.assertEqual(b["band_label"]["hu"], "%d%%-os konfidenciasáv" % lv)
                self.assertEqual(b["pi_band_label"]["en"], "%d%% prediction band" % lv)

    def test_points_weights_model_and_texts(self):
        out, es, doc = doc_of(BCG, measure="RR", moderators=["szélesség"])
        b = doc["bubble"]
        self.assertEqual(b["moderator"], {"name": "szélesség", "label": {"hu": "szélesség", "en": "szélesség"},
                                          "type": "continuous", "coefficient": "szélesség"})
        pts = b["points"]
        self.assertEqual([p["row_uid"] for p in pts], [s["row_uid"] for s in doc["studies"]])
        self.assertEqual([p["row_index"] for p in pts], [s["row_index"] for s in doc["studies"]])
        self.assertEqual([p["x"] for p in pts], [float(r["szélesség"]) for r in es.rows])
        self.assertEqual([p["y"] for p in pts], list(es.yi))
        self.assertEqual([p["display_text"] for p in pts], [s["display_text"] for s in doc["studies"]])
        for p, w in zip(pts, METAFOR_WEIGHTS_ABLAT):
            assert_close(self, p["weight_pct"], w, TOL, "súly " + p["label"])
            self.assertEqual(p["weight_text"], {"hu": "%.1f%%" % w, "en": "%.1f%%" % w})
        assert_close(self, sum(p["weight_pct"] for p in pts), 100.0, 1e-12, "Σ súly")
        self.assertEqual(pts[0]["x_text"], {"hu": "44", "en": "44"})
        m = b["model"]
        self.assertEqual((m["k"], m["p"], m["test"], m["df"], m["tau2_method"], m["level"]), (13, 2, "knha", 11, "REML", 0.95))
        assert_close(self, m["crit"], QT_975_11, 1e-12, "crit")
        mr = out["metaregression"]
        self.assertEqual([c["estimate"] for c in b["coefficients"]], [c["estimate"] for c in mr.coefficients])
        self.assertEqual(b["coef_text"]["hu"], "Meredekség (szélesség): -0.029 [-0.047; -0.011] / egység; p = 0.005 "
                                               "(t(11)); maradék τ² = 0.0763; R² = 76%; RR-szorzó / egység: 0.97 "
                                               "[0.95; 0.99]")
        self.assertEqual(b["coef_text"]["en"], "Slope (szélesség): −0.029 [−0.047; −0.011] per unit; p = 0.005 "
                                               "(t(11)); residual τ² = 0.0763; R² = 76%; RR ratio per unit: 0.97 "
                                               "[0.95; 0.99]")
        # tengelyek: a tickek a tartományban, a tartomány minden pontot és a konfidenciasávot tartalmazza
        for ax in (b["x_axis"], b["y_axis"]):
            lo, hi = ax["domain"]
            self.assertTrue(ax["ticks"])
            for t in ax["ticks"]:
                self.assertTrue(lo <= t["at"] <= hi)
                self.assertEqual(t["text"], t["text_i18n"]["hu"])
        ylo, yhi = b["y_axis"]["domain"]
        self.assertTrue(all(ylo <= v <= yhi for p in pts for v in (p["y"],)))
        self.assertTrue(all(ylo <= v <= yhi for row in b["band"] for v in row[1:]))
        self.assertEqual([t["text"] for t in b["y_axis"]["ticks"]], ["0.2", "0.5", "1"])      # RR-skála
        self.assertEqual(b["y_axis"]["refs"], [{"at": 0.0, "text": None}])
        self.assertEqual(b["y_axis"]["title"], doc["axis"]["title"])
        self.assertEqual(b["x_axis"]["title"], {"hu": "szélesség", "en": "szélesség"})
        # kezdőbarát magyarázat: olvasás, sávok, extrapoláció, ökológiai torzítás; k = 13 ≥ 10 → nincs kis-k mondat
        note = b["note"]
        for word in ("minden kör egy vizsgálat", "konfidenciasáv", "predikciós sáv", "(13–55)", "ökológiai torzítás"):
            self.assertIn(word, note["hu"])
        for word in ("each circle is a study", "prediction band", "(13–55)", "ecological bias"):
            self.assertIn(word, note["en"])
        self.assertNotIn("k = 13 < 10", note["hu"])

    def test_only_single_continuous_moderator(self):
        self.assertIsNone(doc_of(BCG, measure="RR")[2]["bubble"])
        self.assertIsNone(doc_of(BCG, measure="RR", moderators=["allokáció"])[2]["bubble"])          # kategóriás
        self.assertIsNone(doc_of(BCG, measure="RR", moderators=["szélesség", "év"])[2]["bubble"])    # kettő
        out, es, doc = doc_of(BCG, measure="RR", moderators=["év"])
        self.assertEqual(doc["bubble"]["moderator"]["name"], "year")       # kanonikus kulcs, eredeti címke
        self.assertEqual(doc["bubble"]["moderator"]["label"], {"hu": "év", "en": "év"})
        # állandó moderátor kimarad → egy folytonos marad → van buborék
        p = self.gen_csv(extra={"const": ["7"] * 12})
        out, es, doc = doc_of(p, measure="GEN", moderators=["dose", "const"])
        self.assertEqual(out["metaregression_dropped"], ["const"])
        self.assertEqual(doc["bubble"]["moderator"]["name"], "dose")
        # hiányzó moderátor-érték: nincs meta-regresszió → nincs buborék
        p = self.gen_csv(extra={"m": ["1", "2", "", "4", "5", "6", "7", "8", "9", "10", "11", "12"]}, name="miss.csv")
        out, es, doc = doc_of(p, measure="GEN", moderators=["m"])
        self.assertNotIn("metaregression", out)
        self.assertIsNone(doc["bubble"])

    def test_negative_moderator_minus_and_small_k_note(self):
        p = self.gen_csv()
        out, es, doc = doc_of(p, measure="GEN", moderators=["dose"], metareg_tau2="PM")
        b = doc["bubble"]
        ref = METAFOR_PREDICT["gen_dose_pm_knha"]
        for j, gi in enumerate((0, 7, 20, 33, 50)):
            assert_close(self, b["band"][gi][0], ref["x"][j], 1e-12, "x")
            assert_close(self, b["band"][gi][1], ref["ci_lb"][j], TOL, "CI alsó")
            assert_close(self, b["pi_band"][gi][2], ref["pi_ub"][j], TOL, "PI felső")
        self.assertEqual(b["points"][0]["x_text"], {"hu": "-2.5", "en": "−2.5"})
        self.assertIn("(-3.5–3)", b["note"]["hu"])
        self.assertIn("(−3.5–3)", b["note"]["en"])
        self.assertTrue(any(t["text_i18n"]["en"].startswith(P.MINUS) for t in b["x_axis"]["ticks"]))
        self.assertNotIn("szorzó", b["coef_text"]["hu"])          # GEN: nincs arány-skála
        self.assertNotIn("Itt k =", b["note"]["hu"])            # k = 12 ≥ 10
        out, es, doc = doc_of(p, measure="GEN", moderators=["dose"], filters=set(range(8)))
        self.assertIn("Itt k = 8 < 10, ezért az összefüggés különösen bizonytalan.", doc["bubble"]["note"]["hu"])
        self.assertIn("Here k = 8 < 10", doc["bubble"]["note"]["en"])
        out, es, doc = doc_of(p, measure="GEN", moderators=["dose"], metareg_robust=True)
        self.assertIn("modell-alapú kovarianciából", doc["bubble"]["note"]["hu"])

    def test_perfect_fit_band_collapses(self):
        p = self.write("perfect.csv", "study,yi,vi,x\n" + "".join(
            "P%d,%r,%r,%d\n" % (i, 0.1 + 0.2 * i, 0.01 * (1 + i % 3), i) for i in range(1, 8)))
        out, es, doc = doc_of(p, measure="GEN", moderators=["x"])
        b = doc["bubble"]
        for (_, y), (_, lo, hi), (_, plo, phi) in zip(b["line"], b["band"], b["pi_band"]):
            self.assertTrue(all(math.isfinite(v) for v in (y, lo, hi, plo, phi)))
            assert_close(self, hi - lo, 0.0, 1e-9, "sávszélesség")
            assert_close(self, phi - plo, 0.0, 1e-9, "PI-szélesség")
        P.bubble_svg(b, 0.0)        # rajzolható

    def test_numtext_moderator_and_scales(self):
        # magyar tizedesvessző: a moderátor számként (x), a szövege tizedesponttal (a motor konvenciója)
        p = self.write("hu.csv", "vizsgálat;yi;vi;dózis\nA;0,1;0,04;1,5\n".replace(";", ";") +
                       "B;0,3;0,05;2,5\nC;0,2;0,03;3,25\nD;0,6;0,02;4\nE;0,5;0,06;5,5\n")
        out, es, doc = doc_of(p, measure="GEN", moderators=["dózis"])
        self.assertEqual([pt["x"] for pt in doc["bubble"]["points"]], [1.5, 2.5, 3.25, 4.0, 5.5])
        self.assertEqual([pt["x_text"]["hu"] for pt in doc["bubble"]["points"]], ["1.5", "2.5", "3.25", "4", "5.5"])
        # más skálák: ZCOR (r-tickek), PFT, PLO, MD, OR
        for path, opts in ((MOLLOY, {"measure": "ZCOR", "moderators": ["n"]}),
                           (PRITZ, {"measure": "PFT", "moderators": ["n"]}),
                           (PRITZ, {"measure": "PLO", "moderators": ["n"]}),
                           (NORMAND, {"measure": "MD", "moderators": ["n1"]}),
                           (BCG, {"measure": "OR", "moderators": ["év"], "metareg_tau2": "DL"})):
            with self.subTest(opts=opts):
                out, es, doc = doc_of(path, **opts)
                b = doc["bubble"]
                self.assertIsNotNone(b)
                lo, hi = b["y_axis"]["domain"]
                self.assertTrue(all(lo <= t["at"] <= hi for t in b["y_axis"]["ticks"]))
                self.assertGreaterEqual(len(b["y_axis"]["ticks"]), 2)
                json.dumps(pipeline.to_jsonable(b), allow_nan=False)
                svg = pipeline.render_figure(doc, "bubble", "en", True)["svg"]
                xml.dom.minidom.parseString(svg.encode("utf-8"))
        out, es, doc = doc_of(BCG, measure="OR", moderators=["év"], metareg_tau2="DL")
        ref = METAFOR_PREDICT["bcg_or_year_dl_knha"]
        for j, gi in enumerate((0, 12, 25, 50)):
            assert_close(self, doc["bubble"]["band"][gi][1], ref["ci_lb"][j], TOL, "OR év CI alsó")
            assert_close(self, doc["bubble"]["pi_band"][gi][2], ref["pi_ub"][j], TOL, "OR év PI felső")


# ------------------------------------------------------------------ v2 'cumulative'
class TestCumulativeBlock(_Tmp):
    def test_order_steps_and_metafor_cumul(self):
        out, es, doc = doc_of(BCG, measure="RR", cumulative="év")
        cum = doc["cumulative"]
        ents = cum["entries"]
        self.assertEqual([e["row_index"] for e in ents], METAFOR_CUMUL_ORDER)
        by_uid = {s["row_uid"]: s["row_index"] for s in doc["studies"]}
        self.assertEqual([by_uid[e["added_row_uid"]] for e in ents], METAFOR_CUMUL_ORDER)
        for k, (est, lo, hi, tau2) in METAFOR_CUMUL.items():
            e = ents[k - 1]
            self.assertEqual(e["k"], k)
            assert_close(self, e["estimate"], est, TOL, "cumul est k=%d" % k)
            assert_close(self, e["ci_lower"], lo, TOL, "cumul lo k=%d" % k)
            assert_close(self, e["ci_upper"], hi, TOL, "cumul hi k=%d" % k)
            assert_close(self, out["sensitivity"]["cumulative"][k - 1]["tau2"], tau2, 1e-7, "cumul τ² k=%d" % k)
        # az utolsó lépés a teljes (elsődleges) elemzés
        prim = next(s for s in doc["summaries"] if s["primary"])
        self.assertEqual(ents[-1]["display_text"], prim["display_text"])
        o = cum["order"]
        self.assertEqual((o["column"], o["direction"], o["sort"], o["n_missing"], o["missing_last"], o["mixed_types"]),
                         ("year", "ascending", "numeric", 0, True, False))
        self.assertEqual(o["label"], {"hu": "év", "en": "év"})
        self.assertEqual(o["row_uids"], [e["added_row_uid"] for e in ents])
        self.assertEqual(o["text"], {"hu": "Sorrend: a(z) „év” oszlop szerint növekvő (számként).",
                                     "en": "Order: ascending by 'év' (numeric)."})
        self.assertIn("Az utolsó sor (gyémánt) a teljes elemzés", cum["note"]["hu"])
        self.assertIn("The last row (diamond) is the full analysis", cum["note"]["en"])

    def test_missing_and_mixed_keys(self):
        # (a kanonikus 'year' oszlopot a tableio évszámként olvassa: '1999a' → 1999; itt egy saját oszlop)
        p = self.write("mix.csv", "study,yi,vi,fázis\nA,0.1,0.04,3\nB,0.3,0.05,\nC,0.2,0.03,IIb\nD,0.5,0.02,1\n")
        out, es, doc = doc_of(p, measure="GEN", cumulative="fázis")
        o = doc["cumulative"]["order"]
        self.assertEqual((o["sort"], o["n_missing"], o["mixed_types"]), ("natural", 1, True))
        self.assertEqual([e["label"] for e in doc["cumulative"]["entries"]], ["D", "A", "C", "B"])
        self.assertEqual(o["text"]["hu"], "Sorrend: a(z) „fázis” oszlop szerint növekvő (szövegként, természetes "
                                          "rendezéssel); 1 vizsgálatnál hiányzik a rendezőkulcs, ezek a sor végére "
                                          "kerültek; az oszlop vegyesen tartalmaz számot és szöveget — ellenőrizd a "
                                          "sorrendet.")
        self.assertIn("1 study without a sorting key placed last", o["text"]["en"])
        self.assertEqual(doc["cumulative"]["entries"][-1]["key_text"], "–")

    def test_not_requested_unchanged(self):
        self.assertIsNone(doc_of(BCG, measure="RR")[2]["cumulative"])


# ------------------------------------------------------------------ motor-SVG
class TestEngineSvgs(_Tmp):
    @classmethod
    def setUpClass(cls):
        cls.out, cls.es = analyze(BCG, measure="RR", cumulative="év", moderators=["szélesség"])
        cls.doc = pipeline.plot_document(cls.out, cls.es)
        cls.audit = load_file_module("_svgaudit_for_v1_plots", SVGAUDIT) if os.path.isfile(SVGAUDIT) else None

    def test_outputs_unchanged_when_not_requested(self):
        self.assertEqual(pipeline.PLOT_FILES, ("forest.svg", "funnel.svg", "doi.svg", "plot_data.json"))
        for opts in ({"measure": "RR"}, {"measure": "RR", "moderators": ["allokáció"]},
                     {"measure": "RR", "moderators": ["szélesség", "év"]}):
            out, es = analyze(BCG, **opts)
            self.assertEqual(pipeline.make_extra_plots(out, es), {})
            paths = pipeline.write_outputs(out, es, os.path.join(self.tmp, "o"))
            self.assertEqual(sorted(paths), ["doi.svg", "effect_sizes.csv", "forest.svg", "funnel.svg",
                                             "plot_data.json", "results.json"])
            with open(paths["plot_data.json"], encoding="utf-8") as fh:
                d = json.load(fh)
            self.assertIsNone(d["cumulative"])
            self.assertIsNone(d["bubble"])

    def test_written_svgs_equal_render_from_plot_data_file(self):
        for opts in ({}, {"plot_locale": "en", "svg_annotate": True}, {"plot_schema": "v1"}):
            out, es = analyze(BCG, measure="RR", cumulative="év", moderators=["szélesség"], **opts)
            od = os.path.join(self.tmp, "o%d" % len(os.listdir(self.tmp)))
            paths = pipeline.write_outputs(out, es, od)
            self.assertIn("cumulative.svg", paths)
            self.assertIn("bubble.svg", paths)
            doc = pipeline.plot_document(out, es)
            if opts.get("plot_schema") != "v1":
                with open(paths["plot_data.json"], encoding="utf-8") as fh:
                    doc = json.load(fh)             # a felület is a fájlból rajzoltat
            lang = opts.get("plot_locale", "hu")
            for kind in ("cumulative", "bubble"):
                with open(paths[kind + ".svg"], encoding="utf-8") as fh:
                    got = fh.read()
                want = pipeline.render_figure(doc, kind, lang, bool(opts.get("svg_annotate")))
                self.assertEqual(want["lang"], lang)
                self.assertEqual(want["kind"], kind)
                self.assertEqual(got, want["svg"], (kind, opts))
        # elavult fájlok: a kumulatív/meta-regresszió nélküli újrafutás törli őket
        pipeline.write_outputs(*analyze(BCG, measure="RR"), outdir=od)
        self.assertFalse(os.path.exists(os.path.join(od, "cumulative.svg")))
        self.assertFalse(os.path.exists(os.path.join(od, "bubble.svg")))
        self.assertTrue(os.path.exists(os.path.join(od, "forest.svg")))

    def test_engine_texts_verbatim(self):
        doc = self.doc
        for lang in ("hu", "en"):
            for ann in (False, True):
                cum = svg_text(pipeline.render_figure(doc, "cumulative", lang, ann)["svg"])
                minus = P.MINUS if (lang == "en" or ann) else "-"
                for e in doc["cumulative"]["entries"]:
                    self.assertIn(P.num_text(e["display_text"][lang], minus), cum)
                    self.assertIn(P.num_text(e["i2_text"][lang], minus), cum)
                    self.assertIn(e["key_text"], cum)
                for t in doc["cumulative"]["axis"]["ticks"]:
                    self.assertIn(P.num_text(t["text_i18n"][lang], minus), cum)
                self.assertIn(doc["cumulative"]["order"]["text"][lang], cum)
                bub = svg_text(pipeline.render_figure(doc, "bubble", lang, ann)["svg"])
                for ax in ("x_axis", "y_axis"):
                    for t in doc["bubble"][ax]["ticks"]:
                        self.assertIn(P.num_text(t["text_i18n"][lang], minus), bub)
                coef = doc["bubble"]["coef_text"][lang]
                if minus != "-" and lang == "hu":
                    coef = coef.replace("-0", P.MINUS + "0")
                self.assertIn(coef, " ".join(bub))
                loo = svg_text(pipeline.render_figure(doc, "loo", lang, ann)["svg"])
                for e in doc["loo"]:
                    self.assertIn(P.num_text(e["display_text"][lang], minus), loo)

    @unittest.skipUnless(os.path.isfile(SVGAUDIT), "nincs a munkapad számhűség-ellenőrzője")
    def test_server_numbers_check_passes(self):
        for kind in ("cumulative", "bubble", "loo"):
            for lang in ("hu", "en"):
                for ann in (False, True):
                    svg = pipeline.render_figure(self.doc, kind, lang, ann)["svg"]
                    res = self.audit.numbers_check_svg(svg, self.doc, kind)
                    self.assertTrue(res["checked"] > 0, (kind, res))
                    self.assertTrue(res["ok"], (kind, lang, ann, res["mismatches"]))
                    audit = self.audit.audit_svg(svg.encode("utf-8"))
                    self.assertTrue(audit, kind)

    def test_languages_and_minus(self):
        hu = pipeline.render_figure(self.doc, "cumulative", "hu")["svg"]
        en = pipeline.render_figure(self.doc, "cumulative", "en")["svg"]
        self.assertIn(">Hozzáadott vizsgálat<", hu)
        self.assertIn(">Kumulatív metaanalízis<", hu)
        self.assertIn(">Added study<", en)
        self.assertIn(">Cumulative meta-analysis<", en)
        self.assertIn(">RR [95% CI]<", en)
        loo = pipeline.render_figure(self.doc, "loo", "en")["svg"]
        self.assertIn(">Omitted study<", loo)
        b_hu = pipeline.render_figure(self.doc, "bubble", "hu")["svg"]
        b_en = pipeline.render_figure(self.doc, "bubble", "en")["svg"]
        self.assertIn("Meredekség (szélesség): -0.029", b_hu)
        self.assertIn("Slope (szélesség): " + P.MINUS + "0.029", b_en)
        self.assertIn("Solid line: fitted line", b_en)
        self.assertIn("kitöltött sáv: 95%-os konfidenciasáv", b_hu)
        # annotálatlan magyar: a korábbi konvenció ('-', nincs csoport, nincs data-*)
        for svg in (hu, b_hu):
            self.assertNotIn(P.MINUS, svg)
            self.assertNotIn("<g ", svg)
            self.assertNotIn("data-row", svg)
        # angolul a számok előtt nincs kötőjel-mínusz
        for t in svg_text(b_en) + svg_text(en):
            self.assertIsNone(re.search(r"(?<![\w.\-])-(?=\d)", t), t)

    def test_annotation_layers_and_row_ids(self):
        doc = self.doc
        cum = pipeline.render_figure(doc, "cumulative", "hu", True)["svg"]
        plain = pipeline.render_figure(doc, "cumulative", "hu", False)["svg"]
        self.assertEqual(unwrap(cum), sorted(plain.split("\n")))
        for layer in ("title", "header", "null", "reference", "rows", "axis", "labels", "footer"):
            self.assertIn('<g id="layer-cumulative-%s">' % layer, cum)
        for e in doc["cumulative"]["entries"]:
            self.assertIn('<g id="cumulative-step-%s" data-row="%d" data-k="%d" data-y="%s" data-lo="%s" data-hi="%s">'
                          % (e["added_row_uid"], e["row_index"], e["k"], P.attr_num(e["estimate"]),
                             P.attr_num(e["ci_lower"]), P.attr_num(e["ci_upper"])), cum)
        self.assertEqual(cum.count('fill="#1a1a1a" stroke="#1a1a1a"/>'), 1)       # egyetlen gyémánt: a végső sor
        bub = pipeline.render_figure(doc, "bubble", "en", True)["svg"]
        bplain = pipeline.render_figure(doc, "bubble", "en", False)["svg"]
        self.assertEqual(unwrap(bub), sorted(ln.replace(P.MINUS, "-") for ln in bplain.split("\n")))
        for layer in ("title", "frame", "null", "pi-band", "band", "line", "studies", "axis", "legend"):
            self.assertIn('<g id="layer-bubble-%s">' % layer, bub)
        ids = re.findall(r'<g id="bubble-study-(r[0-9a-z]+)" data-row="(\d+)" data-x="([^"]*)" data-y="([^"]*)" '
                         r'data-w="([^"]*)">', bub)
        self.assertEqual(len(ids), len(doc["bubble"]["points"]))
        want = {p["row_uid"]: (str(p["row_index"]), P.attr_num(p["x"]), P.attr_num(p["y"]), P.attr_num(p["weight_pct"]))
                for p in doc["bubble"]["points"]}
        self.assertEqual({u: (r, x, y, w) for u, r, x, y, w in ids}, want)
        self.assertIn('clip-path="url(#bubble-clip)"', bub)
        loo = pipeline.render_figure(doc, "loo", "hu", True)["svg"]
        rix = {s["row_uid"]: s["row_index"] for s in doc["studies"]}
        for e in doc["loo"]:
            self.assertIn('<g id="loo-study-%s" data-row="%d" data-y=' % (e["omitted_row_uid"], rix[e["omitted_row_uid"]]),
                          loo)

    def test_bubble_area_proportional_to_weight(self):
        svg = pipeline.render_figure(self.doc, "bubble", "hu", True)["svg"]
        r = {u: float(rad) for u, rad in re.findall(r'id="bubble-study-(r[0-9a-z]+)"[^>]*><circle cx="[^"]+" '
                                                    r'cy="[^"]+" r="([^"]+)"', svg)}
        pts = {p["row_uid"]: p["weight_pct"] for p in self.doc["bubble"]["points"]}
        big = max(pts, key=pts.get)
        self.assertEqual(r[big], P.BUBBLE_R_MAX)
        for uid, w in pts.items():
            want = max(P.BUBBLE_R_MIN, P.BUBBLE_R_MAX * math.sqrt(w / pts[big]))
            self.assertAlmostEqual(r[uid], want, delta=0.051)

    def test_render_figure_contract(self):
        for kind in ("forest", "funnel", "doi"):
            self.assertIsNone(pipeline.render_figure(self.doc, kind, "en", True))
        with self.assertRaises(ValueError):
            pipeline.render_figure(self.doc, "violin")
        with self.assertRaises(ValueError):
            pipeline.render_figure(self.doc, "cumulative", "de")
        with self.assertRaises(ValueError):
            pipeline.render_figure(pipeline.make_plots(self.out, self.es)[2], "cumulative")        # v1
        plain = pipeline.plot_document(*analyze(BCG, measure="RR"))
        for kind in ("cumulative", "bubble"):
            with self.assertRaises(ValueError) as cm:
                pipeline.render_figure(plain, kind)
            self.assertIn("ebben a futásban nincs", str(cm.exception))
        k2 = pipeline.plot_document(*analyze(self.write("k2.csv", "study,yi,vi\nA,0.2,0.04\nB,0.5,0.05\n"),
                                             measure="GEN"))
        with self.assertRaises(ValueError):
            pipeline.render_figure(k2, "loo")
        self.assertIsNone(pipeline.render_figure(self.doc, "forest", None))

    def test_escaping_and_single_step(self):
        p = self.write("x.csv", 'study,yi,vi,year,x\n"<b>A&B</b>",0.2,0.04,2001,1\n"C\x0bD",0.5,0.05,2002,2\n'
                                'E,0.1,0.03,2003,4\n')
        out, es, doc = doc_of(p, measure="GEN", cumulative="year", moderators=["x"])
        for kind in ("cumulative", "bubble", "loo"):
            svg = pipeline.render_figure(doc, kind, "hu", True)["svg"]
            xml.dom.minidom.parseString(svg.encode("utf-8"))
            self.assertNotIn("<b>", svg)
        one = self.write("one.csv", "study,yi,vi,year\nA,0.2,0.04,2001\n")
        out, es, doc = doc_of(one, measure="GEN", cumulative="year")
        svg = pipeline.render_figure(doc, "cumulative", "en")["svg"]
        self.assertEqual(len(doc["cumulative"]["entries"]), 1)
        self.assertIn("<polygon points=", svg)              # az egyetlen (végső) sor gyémánt


# ------------------------------------------------------------------ szerződés
class TestContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SCHEMA_LITE):
            raise unittest.SkipTest("nincs stdlib JSON-Schema-validátor (ma_gui/schema_lite.py)")
        cls.sl = load_file_module("_schema_lite_for_v1_plots", SCHEMA_LITE)
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            cls.schema = json.load(fh)
        cls.registry = cls.sl.load_schema_dir(os.path.dirname(SCHEMA_PATH))
        cls.sl.check_schema(cls.schema, cls.registry)

    def errors(self, doc):
        doc = json.loads(json.dumps(pipeline.to_jsonable(doc), ensure_ascii=False, allow_nan=False))
        return [str(e) for e in self.sl.validate(doc, self.schema, self.registry)]

    def test_documents_conform(self):
        for path, opts in ((BCG, {"measure": "RR", "cumulative": "év", "moderators": ["szélesség"]}),
                           (BCG, {"measure": "OR", "moderators": ["év"], "metareg_tau2": "FE", "plot_locale": "en"}),
                           (BCG, {"measure": "RR", "moderators": ["szélesség"], "metareg_test": "z", "level": 0.9}),
                           (MOLLOY, {"measure": "ZCOR", "moderators": ["n"], "cumulative": "n"}),
                           (PRITZ, {"measure": "PFT", "moderators": ["n"], "cumulative": "study"}),
                           (NORMAND, {"measure": "MD", "moderators": ["n1"]})):
            with self.subTest(opts=opts):
                out, es = analyze(path, **opts)
                doc = pipeline.plot_document(out, es)
                self.assertIsNotNone(doc["bubble"])
                self.assertEqual(self.errors(doc), [])

    def test_schema_rejects_malformed_blocks(self):
        out, es = analyze(BCG, measure="RR", cumulative="év", moderators=["szélesség"])
        doc = pipeline.to_jsonable(pipeline.plot_document(out, es))
        bad = json.loads(json.dumps(doc))
        del bad["bubble"]["band"]
        self.assertTrue(self.errors(bad))
        bad = json.loads(json.dumps(doc))
        bad["bubble"]["band"][3] = [1.0, 2.0]                      # [x, alsó, felső] kell
        self.assertTrue(self.errors(bad))
        bad = json.loads(json.dumps(doc))
        bad["bubble"]["moderator"]["type"] = "ordinal"
        self.assertTrue(self.errors(bad))
        bad = json.loads(json.dumps(doc))
        bad["cumulative"]["order"]["sort"] = "random"
        self.assertTrue(self.errors(bad))
        bad = json.loads(json.dumps(doc))
        bad["cumulative"]["entries"][0]["added_row_uid"] = "nem-uid"
        self.assertTrue(self.errors(bad))


if __name__ == "__main__":
    unittest.main()
