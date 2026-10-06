# Lánc-visszajátszás — lefedettségi jelentés (terv 8.2)

Generálja: `python3 tests/gui/test_chain_replay.py --write-report` (a `test_chain_replay` teszt ellenőrzi, hogy naprakész). A forrás: `tests/source_cases.py` aktív esetei (`tests/reference/source_examples/`).

Út: az eset sorai → a munkapad táblaírója (`ProjectStore.save_table`, `;` + tizedesvessző) → `api.analyze(explore)` → nézetmodell (`plot/v2` + `results`). Minden leképezett ellenőrzés kétszer: a várt érték ugyanazzal a tűréssel, mint a közvetlen motorteszt, és a nézetmodell száma = a közvetlen motorhívásé (relatív 1e-12). A PRISMA-esetek a `PUT /api/prisma/manual` motor-hívásán (`api.prisma_check`, a dobozok nyers szövegként), az átváltás az `api.convert`-en fut.

## Összesítés

| | Eset | Ellenőrzés |
|---|---:|---:|
| aktív (összes) | 196 | 3283 |
| leképezett | 168 | 3024 (92.1%) |
| — ebből zöld | | 3024 (100.0% a leképezettekből) |
| nem leképezhető | 28 | 259 (7.9%) |

Utak: elemzés-futás (`api.analyze` explore) 165 eset; PRISMA (`api.prisma_check`) 2; átváltás (`api.convert`) 1.

## Hívástípusonként

| Hívás | Eset | Ellenőrzés | Leképezett eset | Leképezett ellenőrzés | Zöld | Nem leképezhető ellenőrzés |
|---|---:|---:|---:|---:|---:|---:|
| `begg` | 3 | 7 | 3 | 7 | 7 | 0 |
| `conversion` | 1 | 1 | 1 | 1 | 1 | 0 |
| `effect_sizes` | 35 | 612 | 34 | 600 | 600 | 12 |
| `egger` | 4 | 25 | 4 | 25 | 25 | 0 |
| `harbord` | 1 | 4 | 1 | 4 | 4 | 0 |
| `lfk` | 9 | 39 | 9 | 39 | 39 | 0 |
| `meta_analysis` | 106 | 1843 | 84 | 1674 | 1674 | 169 |
| `meta_regression` | 10 | 171 | 9 | 163 | 163 | 8 |
| `peters` | 1 | 4 | 1 | 4 | 4 | 0 |
| `prisma_flow` | 2 | 15 | 2 | 15 | 15 | 0 |
| `subgroup` | 22 | 520 | 20 | 492 | 492 | 28 |
| `trimfill` | 2 | 42 | 0 | 0 | 0 | 42 |

## Nem leképezhető ellenőrzések okonként

Ezek a közvetlen motortesztben (`tests/source_cases.py`) maradnak; a pipeline-opció hiánya motor-PR-jelölt. A terv 8.2 előzetes becslése 106 nem leképezhető ellenőrzés (96,8%-os felső korlát) volt; a mérés további okot talált: a **validálási kapu** (a pipeline az `error` szintű V-találattal jelölt sort kizárja, a közvetlen motorhívás nem validál). Ide tartoznak a Khan 2020 6–7. fejezetének MetaXL-esetei, ahol a forrás tört eseményszámmal (x = prevalencia × n) számol, a motor V006-szabálya pedig a nem egész eseményszámot hibának veszi. Ez a motor szándékos döntése, nem a lánc hibája; ha a tört eseményszámot engedni kell (pl. figyelmeztetéssel), az motor-PR.

| Ok | Eset | Ellenőrzés |
|---|---:|---:|
| validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál | 12 | 121 |
| tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) | 8 | 73 |
| trimfill side — az oldalt a pipeline mindig maga választja; nincs pipeline-opció (motor-PR-jelölt) | 2 | 42 |
| validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám; V026 Nincs elemezhető vizsgálat) — a közvetlen motorhívás nem validál | 5 | 15 |
| meta-regresszió moderátor nélkül (csak tengelymetszet) — a pipeline moderátor nélkül nem illeszt meta-regressziót | 1 | 8 |

## Esetek

| Eset | Hívás | Mérték | Ellenőrzés | Út | Eredmény / ok |
|---|---|---|---:|---|---|
| `borenstein_book__ch13_smd_fixed_effect` | meta_analysis | SMD | 25 | analyze | 25/25 zöld |
| `borenstein_book__ch13_smd_hedges_g` | effect_sizes | SMD | 18 | analyze | 18/18 zöld |
| `borenstein_book__ch13_smd_random_dl` | meta_analysis | SMD | 23 | analyze | 23/23 zöld |
| `borenstein_book__ch43_identical_rr_fixed` | meta_analysis | GEN | 8 | analyze | 8/8 zöld |
| `borenstein_book__ch43_identical_rr_random_dl_tau2_zero` | meta_analysis | GEN | 7 | analyze | 7/7 zöld |
| `borenstein_book__ch43_se_from_p` | conversion | — | 1 | convert | 1/1 zöld |
| `borenstein_fe_re__extreme_effect_in_large_study__cohen_d_variance` | effect_sizes | COHEN_D | 24 | analyze | 24/24 zöld |
| `borenstein_fe_re__extreme_effect_in_large_study__fixed` | meta_analysis | GEN | 22 | analyze | 22/22 zöld |
| `borenstein_fe_re__extreme_effect_in_large_study__random_dl` | meta_analysis | GEN | 21 | analyze | 21/21 zöld |
| `borenstein_fe_re__extreme_effect_in_small_study__cohen_d_variance` | effect_sizes | COHEN_D | 24 | analyze | 24/24 zöld |
| `borenstein_fe_re__extreme_effect_in_small_study__fixed` | meta_analysis | GEN | 22 | analyze | 22/22 zöld |
| `borenstein_fe_re__extreme_effect_in_small_study__random_dl` | meta_analysis | GEN | 22 | analyze | 22/22 zöld |
| `borenstein_fe_re__huge_n_fixed` | meta_analysis | GEN | 5 | analyze | 5/5 zöld |
| `borenstein_fe_re__huge_n_fixed__re_tau2_truncation` | meta_analysis | GEN | 4 | analyze | 4/4 zöld |
| `borenstein_fe_re__huge_n_random` | meta_analysis | GEN | 8 | analyze | 8/8 zöld |
| `borenstein_fe_re__reading_scores_fixed` | meta_analysis | GEN | 25 | analyze | 25/25 zöld |
| `borenstein_fe_re__reading_scores_random_dl` | meta_analysis | GEN | 34 | analyze | 34/34 zöld |
| `borenstein_fe_re__reading_scores_random_dl__fe_block` | meta_analysis | GEN | 8 | analyze | 8/8 zöld |
| `cheung_guide__fixed_effect` | meta_analysis | GEN | 23 | analyze | 23/23 zöld |
| `cheung_guide__fixed_effect__wilson_weighted_sd` | meta_analysis | GEN | 3 | analyze | 3/3 zöld |
| `cheung_guide__metareg_ml_mean_age` | meta_regression | GEN | 16 | analyze | 16/16 zöld |
| `cheung_guide__metareg_ml_wilson_spss` | meta_regression | GEN | 32 | analyze | 32/32 zöld |
| `cheung_guide__metareg_ml_wilson_spss__re_weighted_mean` | meta_analysis | GEN | 2 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `cheung_guide__metareg_reml_kh_stata` | meta_regression | GEN | 31 | analyze | 31/31 zöld |
| `cheung_guide__metareg_reml_kh_stata__i2_res` | meta_regression | GEN | 1 | analyze | 1/1 zöld |
| `cheung_guide__random_dl` | meta_analysis | GEN | 21 | analyze | 21/21 zöld |
| `cheung_guide__re_ml` | meta_analysis | GEN | 22 | analyze | 22/22 zöld |
| `jslhr_tutorial__egger_k22` | egger | GEN | 11 | analyze | 11/11 zöld |
| `jslhr_tutorial__egger_k22_normal_ci` | egger | GEN | 4 | analyze | 4/4 zöld |
| `jslhr_tutorial__k19_h_ci_truncated_centre` | meta_analysis | GEN | 5 | analyze | 5/5 zöld |
| `jslhr_tutorial__k19_outliers_removed_re_reml` | meta_analysis | GEN | 34 | analyze | 34/34 zöld |
| `jslhr_tutorial__k22_re_reml` | meta_analysis | GEN | 90 | analyze | 90/90 zöld |
| `jslhr_tutorial__trimfill_k22_fe_trimming` | trimfill | GEN | 37 | — | nem leképezhető: trimfill side — az oldalt a pipeline mindig maga választja; nincs pipeline-opció (motor-PR-jelölt) |
| `jslhr_tutorial__trimfill_k22_filled_re_reml` | meta_analysis | GEN | 20 | analyze | 20/20 zöld |
| `jslhr_tutorial__trimfill_k22_meta_default` | trimfill | GEN | 5 | — | nem leképezhető: trimfill side — az oldalt a pipeline mindig maga választja; nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch01_02__prisma2009_flow_d1_vs_d2_gastrectomy` | prisma_flow | other | 8 | prisma | 8/8 zöld |
| `khan_ch01_02__z_crit_90_unit_se_ci` | meta_analysis | GEN | 3 | analyze | 3/3 zöld |
| `khan_ch01_02__z_crit_95_unit_se_ci` | meta_analysis | GEN | 3 | analyze | 3/3 zöld |
| `khan_ch03_04__heartburn_fixed` | meta_analysis | RR | 28 | analyze | 28/28 zöld |
| `khan_ch03_04__heartburn_ivhet` | meta_analysis | RR | 37 | analyze | 37/37 zöld |
| `khan_ch03_04__heartburn_ivhet_table49_per_study` | effect_sizes | RR | 14 | analyze | 14/14 zöld |
| `khan_ch03_04__heartburn_lfk` | lfk | RR | 17 | analyze | 17/17 zöld |
| `khan_ch03_04__heartburn_lfk_table49_inputs` | lfk | GEN | 2 | analyze | 2/2 zöld |
| `khan_ch03_04__heartburn_per_study_wald_ci` | subgroup | RR | 21 | analyze | 21/21 zöld |
| `khan_ch03_04__heartburn_random_dl` | meta_analysis | RR | 43 | analyze | 43/43 zöld |
| `khan_ch03_04__heartburn_rr_per_study` | effect_sizes | RR | 44 | analyze | 44/44 zöld |
| `khan_ch03_04__heartburn_study1_arm_risks` | effect_sizes | PR | 4 | analyze | 4/4 zöld |
| `khan_ch03_04__heartburn_subgroup_period_random` | subgroup | RR | 36 | analyze | 36/36 zöld |
| `khan_ch03_04__heartburn_subgroup_weight_share` | subgroup | RR | 2 | analyze | 2/2 zöld |
| `khan_ch03_04__pesticide_or` | effect_sizes | OR | 4 | analyze | 4/4 zöld |
| `khan_ch03_04__rare_events_or` | effect_sizes | OR | 4 | analyze | 4/4 zöld |
| `khan_ch03_04__rare_events_rr` | effect_sizes | RR | 4 | analyze | 4/4 zöld |
| `khan_ch03_04__thyroid_fixed` | meta_analysis | RR | 42 | analyze | 42/42 zöld |
| `khan_ch03_04__thyroid_per_study_wald_ci` | subgroup | RR | 35 | analyze | 35/35 zöld |
| `khan_ch03_04__thyroid_rr_per_study` | effect_sizes | RR | 42 | analyze | 42/42 zöld |
| `khan_ch03_04__vaccine_arm_odds_cc05_plo` | effect_sizes | PLO | 4 | analyze | 4/4 zöld |
| `khan_ch03_04__vaccine_arm_odds_plo` | effect_sizes | PLO | 6 | analyze | 6/6 zöld |
| `khan_ch03_04__vaccine_arm_risks_pr` | effect_sizes | PR | 6 | analyze | 6/6 zöld |
| `khan_ch03_04__vaccine_or_cc05_all_cells` | effect_sizes | OR | 3 | analyze | 3/3 zöld |
| `khan_ch03_04__vaccine_or_effect_size` | effect_sizes | OR | 6 | analyze | 6/6 zöld |
| `khan_ch03_04__vaccine_rr_ci_ztest` | meta_analysis | RR | 13 | analyze | 13/13 zöld |
| `khan_ch03_04__vaccine_rr_effect_size` | effect_sizes | RR | 7 | analyze | 7/7 zöld |
| `khan_ch05__heartburn_doi_plot_lfk` | lfk | OR | 9 | analyze | 9/9 zöld |
| `khan_ch05__heartburn_doi_plot_lfk_appendix_inputs` | lfk | GEN | 2 | analyze | 2/2 zöld |
| `khan_ch05__heartburn_fe_iv` | meta_analysis | OR | 26 | analyze | 26/26 zöld |
| `khan_ch05__heartburn_ivhet` | meta_analysis | OR | 25 | analyze | 25/25 zöld |
| `khan_ch05__heartburn_per_study_logor` | effect_sizes | OR | 42 | analyze | 42/42 zöld |
| `khan_ch05__heartburn_re_dl` | meta_analysis | OR | 38 | analyze | 38/38 zöld |
| `khan_ch05__heartburn_re_table5_7_tau2_rounded` | meta_analysis | OR | 7 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch05__heartburn_subgroup_period_re` | subgroup | OR | 42 | analyze | 42/42 zöld |
| `khan_ch05__heartburn_subgroup_period_re_appendix_inputs` | subgroup | GEN | 25 | analyze | 25/25 zöld |
| `khan_ch05__heartburn_subgroup_weight_share` | subgroup | OR | 2 | analyze | 2/2 zöld |
| `khan_ch05__thyroid_fe_iv` | meta_analysis | OR | 34 | analyze | 34/34 zöld |
| `khan_ch05__thyroid_per_study_logor` | effect_sizes | OR | 35 | analyze | 35/35 zöld |
| `khan_ch05__vaccination_logor_woolf_se` | effect_sizes | OR | 5 | analyze | 5/5 zöld |
| `khan_ch05__vaccination_wald_ci_ztest` | meta_analysis | OR | 12 | analyze | 12/12 zöld |
| `khan_ch06_07__aspirin_arm_risks` | effect_sizes | PR | 14 | analyze | 14/14 zöld |
| `khan_ch06_07__aspirin_metaxl_study_wald_ci` | subgroup | RD | 21 | analyze | 21/21 zöld |
| `khan_ch06_07__aspirin_mi_rd_fixed` | meta_analysis | RD | 31 | analyze | 31/31 zöld |
| `khan_ch06_07__aspirin_mi_rd_ivhet` | meta_analysis | RD | 20 | analyze | 20/20 zöld |
| `khan_ch06_07__aspirin_mi_rd_ivhet_tau2_rounded` | meta_analysis | RD | 8 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch06_07__aspirin_mi_rd_random_dl` | meta_analysis | RD | 22 | analyze | 22/22 zöld |
| `khan_ch06_07__aspirin_mi_rd_random_tau2_rounded` | meta_analysis | RD | 11 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch06_07__aspirin_rd_per_study` | effect_sizes | RD | 14 | analyze | 14/14 zöld |
| `khan_ch06_07__rd_single_study_wald` | meta_analysis | RD | 11 | analyze | 11/11 zöld |
| `khan_ch06_07__schizophrenia_metaxl_fixed` | meta_analysis | PFT | 12 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_fixed_backtransformed` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_ivhet` | meta_analysis | PFT | 12 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_ivhet_backtransformed` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_random` | meta_analysis | PFT | 12 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_random_backtransformed` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_study_row_babigian` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám; V026 Nincs elemezhető vizsgálat) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_study_row_bondestam` | meta_analysis | PFT | 3 | analyze | 3/3 zöld |
| `khan_ch06_07__schizophrenia_metaxl_study_row_fichter` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám; V026 Nincs elemezhető vizsgálat) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_study_row_keith` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám; V026 Nincs elemezhető vizsgálat) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_study_row_shen` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám; V026 Nincs elemezhető vizsgálat) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_study_row_zharikov` | meta_analysis | PFT | 3 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám; V026 Nincs elemezhető vizsgálat) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_subgroup_backtransformed_and_share` | subgroup | PFT | 11 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_metaxl_subgroup_income_fixed` | subgroup | PFT | 17 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_raw_fixed` | meta_analysis | PR | 16 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_raw_ivhet` | meta_analysis | PR | 9 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_raw_ivhet_tau2_rounded` | meta_analysis | PR | 7 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch06_07__schizophrenia_raw_per_study_var` | effect_sizes | PR | 12 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_raw_random_dl` | meta_analysis | PR | 11 | — | nem leképezhető: validálási kapu: a munkapad validálása hibásnak ítéli és kizárja a sort (V006 Érvénytelen eseményszám) — a közvetlen motorhívás nem validál |
| `khan_ch06_07__schizophrenia_raw_random_tau2_rounded` | meta_analysis | PR | 10 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch06_07__single_proportion_es` | effect_sizes | PR | 5 | analyze | 5/5 zöld |
| `khan_ch06_07__single_proportion_wald_ci_ztest` | meta_analysis | PR | 9 | analyze | 9/9 zöld |
| `khan_ch08__cholesterol_paired_mean_difference` | effect_sizes | MC | 5 | analyze | 5/5 zöld |
| `khan_ch08__ginkgo_md_pooled_equal_var` | effect_sizes | MD | 4 | analyze | 4/4 zöld |
| `khan_ch08__ginkgo_md_unequal_var` | effect_sizes | MD | 6 | analyze | 6/6 zöld |
| `khan_ch08__open_education_fe` | meta_analysis | GEN | 23 | analyze | 23/23 zöld |
| `khan_ch08__open_education_fe_fullprec` | meta_analysis | GEN | 16 | analyze | 16/16 zöld |
| `khan_ch08__open_education_ivhet` | meta_analysis | GEN | 17 | analyze | 17/17 zöld |
| `khan_ch08__open_education_ivhet_fullprec` | meta_analysis | GEN | 12 | analyze | 12/12 zöld |
| `khan_ch08__open_education_per_study_variance` | effect_sizes | GEN | 12 | analyze | 12/12 zöld |
| `khan_ch08__open_education_per_study_wald_ci` | subgroup | GEN | 33 | analyze | 33/33 zöld |
| `khan_ch08__open_education_re_dl` | meta_analysis | GEN | 20 | analyze | 20/20 zöld |
| `khan_ch08__open_education_re_dl_fullprec` | meta_analysis | GEN | 14 | analyze | 14/14 zöld |
| `khan_ch08__optime_cohen_fe_metaxl_var` | meta_analysis | GEN | 31 | analyze | 31/31 zöld |
| `khan_ch08__optime_cohen_fe_native_variance` | meta_analysis | COHEN_D | 14 | analyze | 14/14 zöld |
| `khan_ch08__optime_cohen_ivhet_metaxl_var` | meta_analysis | GEN | 20 | analyze | 20/20 zöld |
| `khan_ch08__optime_cohen_lfk_metaxl_var` | lfk | GEN | 2 | analyze | 2/2 zöld |
| `khan_ch08__optime_cohen_per_study_ci_metaxl_var` | subgroup | GEN | 18 | analyze | 18/18 zöld |
| `khan_ch08__optime_cohen_per_study_es` | effect_sizes | COHEN_D | 18 | analyze | 18/18 zöld |
| `khan_ch08__optime_cohen_re_dl_metaxl_var` | meta_analysis | GEN | 29 | analyze | 29/29 zöld |
| `khan_ch08__optime_glass_fe_metaxl_var` | meta_analysis | GEN | 31 | analyze | 31/31 zöld |
| `khan_ch08__optime_glass_ivhet_metaxl_var` | meta_analysis | GEN | 20 | analyze | 20/20 zöld |
| `khan_ch08__optime_glass_per_study_ci_metaxl_var` | subgroup | GEN | 27 | analyze | 27/27 zöld |
| `khan_ch08__optime_glass_per_study_es` | effect_sizes | SMD_GLASS | 18 | analyze | 18/18 zöld |
| `khan_ch08__optime_glass_re_dl_metaxl_var` | meta_analysis | GEN | 29 | analyze | 29/29 zöld |
| `khan_ch08__optime_hedges_fe_metaxl_var` | meta_analysis | GEN | 31 | analyze | 31/31 zöld |
| `khan_ch08__optime_hedges_fe_native_variance` | meta_analysis | SMD | 14 | analyze | 14/14 zöld |
| `khan_ch08__optime_hedges_ivhet_metaxl_var` | meta_analysis | GEN | 20 | analyze | 20/20 zöld |
| `khan_ch08__optime_hedges_lfk_metaxl_var` | lfk | GEN | 2 | analyze | 2/2 zöld |
| `khan_ch08__optime_hedges_per_study_ci_metaxl_var` | subgroup | GEN | 18 | analyze | 18/18 zöld |
| `khan_ch08__optime_hedges_per_study_es` | effect_sizes | SMD | 18 | analyze | 18/18 zöld |
| `khan_ch08__optime_hedges_re_dl_metaxl_var` | meta_analysis | GEN | 29 | analyze | 29/29 zöld |
| `khan_ch08__quant_ability_study1_variance_from_g` | effect_sizes | GEN | 3 | analyze | 3/3 zöld |
| `khan_ch08__quant_ability_study1_wald_ci_ztest` | meta_analysis | GEN | 5 | analyze | 5/5 zöld |
| `khan_ch09__blood_loss_md_effect_sizes` | effect_sizes | MD | 33 | analyze | 33/33 zöld |
| `khan_ch09__blood_loss_md_fixed` | meta_analysis | MD | 25 | analyze | 25/25 zöld |
| `khan_ch09__blood_loss_md_fixed_fullprec` | meta_analysis | MD | 20 | analyze | 20/20 zöld |
| `khan_ch09__blood_loss_md_fixed_reversed` | meta_analysis | MD | 21 | analyze | 21/21 zöld |
| `khan_ch09__blood_loss_md_ivhet` | meta_analysis | MD | 21 | analyze | 21/21 zöld |
| `khan_ch09__blood_loss_md_ivhet_fullprec` | meta_analysis | MD | 17 | analyze | 17/17 zöld |
| `khan_ch09__blood_loss_md_random_dl` | meta_analysis | MD | 22 | analyze | 22/22 zöld |
| `khan_ch09__blood_loss_md_random_dl_fullprec` | meta_analysis | MD | 21 | analyze | 21/21 zöld |
| `khan_ch09__blood_loss_per_study_wald_ci` | subgroup | MD | 44 | analyze | 44/44 zöld |
| `khan_ch09__blood_loss_reversed_per_study_ci` | subgroup | MD | 33 | analyze | 33/33 zöld |
| `khan_ch09__blood_loss_subgroup_period_fixed` | subgroup | MD | 29 | analyze | 29/29 zöld |
| `khan_ch09__blood_loss_subgroup_period_fixed_fullprec` | subgroup | MD | 16 | analyze | 16/16 zöld |
| `khan_ch09__blood_loss_subgroup_period_ivhet` | subgroup | MD | 29 | analyze | 29/29 zöld |
| `khan_ch09__blood_loss_subgroup_period_ivhet_fullprec` | subgroup | MD | 10 | analyze | 10/10 zöld |
| `khan_ch09__blood_loss_subgroup_period_random` | subgroup | MD | 29 | analyze | 29/29 zöld |
| `khan_ch09__blood_loss_subgroup_period_random_fullprec` | subgroup | MD | 22 | analyze | 22/22 zöld |
| `khan_ch09__bonjer2015_md_effect_size` | effect_sizes | MD | 6 | analyze | 6/6 zöld |
| `khan_ch09__bonjer2015_md_wald_ci` | meta_analysis | MD | 6 | analyze | 6/6 zöld |
| `khan_ch10__fisher_z_ci_adams` | meta_analysis | ZCOR | 13 | analyze | 13/13 zöld |
| `khan_ch10__fisher_z_ci_baker` | meta_analysis | ZCOR | 13 | analyze | 13/13 zöld |
| `khan_ch10__fisher_z_ci_davis` | meta_analysis | ZCOR | 13 | analyze | 13/13 zöld |
| `khan_ch10__fisher_z_ci_miller` | meta_analysis | ZCOR | 13 | analyze | 13/13 zöld |
| `khan_ch10__fisher_z_ci_moore` | meta_analysis | ZCOR | 17 | analyze | 17/17 zöld |
| `khan_ch10__fisher_z_ci_thomas` | meta_analysis | ZCOR | 13 | analyze | 13/13 zöld |
| `khan_ch10__fisher_z_ci_williams` | meta_analysis | ZCOR | 13 | analyze | 13/13 zöld |
| `khan_ch10__fisher_z_ci_young` | meta_analysis | ZCOR | 13 | analyze | 13/13 zöld |
| `khan_ch10__fisher_z_per_study_es` | effect_sizes | ZCOR | 56 | analyze | 56/56 zöld |
| `khan_ch10__fixed_effect_fisher_z` | meta_analysis | ZCOR | 38 | analyze | 38/38 zöld |
| `khan_ch10__heterogeneity_q_i2` | meta_analysis | ZCOR | 9 | analyze | 9/9 zöld |
| `khan_ch10__ivhet_fisher_z` | meta_analysis | ZCOR | 25 | analyze | 25/25 zöld |
| `khan_ch10__ivhet_tau2_rounded` | meta_analysis | ZCOR | 9 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch10__moore_z_test` | meta_analysis | ZCOR | 5 | analyze | 5/5 zöld |
| `khan_ch10__random_effects_dl_fisher_z` | meta_analysis | ZCOR | 39 | analyze | 39/39 zöld |
| `khan_ch10__random_effects_tau2_rounded` | meta_analysis | ZCOR | 19 | — | nem leképezhető: tau2_fixed — rögzített τ²-re nincs pipeline-opció (motor-PR-jelölt) |
| `khan_ch10__tau2_dersimonian_laird` | meta_analysis | ZCOR | 9 | analyze | 9/9 zöld |
| `khan_ch11_13__fibrinolysis_begg` | begg | GEN | 3 | analyze | 3/3 zöld |
| `khan_ch11_13__fibrinolysis_egger` | egger | GEN | 5 | analyze | 5/5 zöld |
| `khan_ch11_13__fibrinolysis_lfk` | lfk | GEN | 2 | analyze | 2/2 zöld |
| `khan_ch11_13__ihdchol_ivhet_metaxl_weights` | meta_analysis | OR | 23 | analyze | 23/23 zöld |
| `khan_ch11_13__ihdchol_ivhet_pooled_or` | meta_analysis | OR | 28 | analyze | 28/28 zöld |
| `khan_ch11_13__ihdchol_metareg_dl_knha` | meta_regression | OR | 30 | analyze | 30/30 zöld |
| `khan_ch11_13__ihdchol_per_study_log_or` | effect_sizes | OR | 92 | analyze | 92/92 zöld |
| `khan_ch11_13__ihdchol_wls_intercept_point_estimate` | meta_analysis | OR | 4 | analyze | 4/4 zöld |
| `khan_ch11_13__ihdchol_wls_intercept_robust_inference` | meta_regression | OR | 8 | — | nem leképezhető: meta-regresszió moderátor nélkül (csak tengelymetszet) — a pipeline moderátor nélkül nem illeszt meta-regressziót |
| `khan_ch11_13__ihdchol_wls_metareg_categorical_point_estimates` | meta_regression | OR | 10 | analyze | 10/10 zöld |
| `khan_ch11_13__ihdchol_wls_metareg_categorical_robust_inference` | meta_regression | OR | 19 | analyze | 19/19 zöld |
| `khan_ch11_13__ihdchol_wls_metareg_continuous_point_estimates` | meta_regression | OR | 9 | analyze | 9/9 zöld |
| `khan_ch11_13__ihdchol_wls_metareg_continuous_robust_inference` | meta_regression | OR | 15 | analyze | 15/15 zöld |
| `khan_ch11_13__magnesium_begg_kendall_exact` | begg | RR | 3 | analyze | 3/3 zöld |
| `khan_ch11_13__magnesium_begg_normal_approx` | begg | RR | 1 | analyze | 1/1 zöld |
| `khan_ch11_13__magnesium_egger` | egger | RR | 5 | analyze | 5/5 zöld |
| `khan_ch11_13__magnesium_harbord` | harbord | OR | 4 | analyze | 4/4 zöld |
| `khan_ch11_13__magnesium_lfk` | lfk | RR | 2 | analyze | 2/2 zöld |
| `khan_ch11_13__magnesium_lfk_cc05` | lfk | RR | 1 | analyze | 1/1 zöld |
| `khan_ch11_13__magnesium_peters` | peters | OR | 4 | analyze | 4/4 zöld |
| `simplypsych_guide__fe_inverse_variance_weight` | meta_analysis | GEN | 6 | analyze | 6/6 zöld |
| `simplypsych_guide__prisma2009_flow_bialek2023` | prisma_flow | other | 7 | prisma | 7/7 zöld |
