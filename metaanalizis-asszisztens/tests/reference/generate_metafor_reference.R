# Referencia-értékek generálása a metafor csomaggal a Python-motor tesztjeihez.
# Futtatás (a metaanalizis-asszisztens mappából):
#   Rscript tests/reference/generate_metafor_reference.R
# Kimenet: tests/reference/metafor_reference.json
#
# Az adatsorok a metadat csomagból származnak (a metafor hivatkozott adatai), így a
# bemenet és a várt eredmény ugyanabból a forrásból jön.

suppressPackageStartupMessages({
  library(metafor)
})

num <- function(x) if (is.null(x)) NULL else unname(as.numeric(x))

fit_summary <- function(res) {
  out <- list(
    estimate = num(res$b[1]), se = num(res$se[1]), ci_lower = num(res$ci.lb[1]),
    ci_upper = num(res$ci.ub[1]), stat = num(res$zval[1]), p = num(res$pval[1]),
    tau2 = num(res$tau2), Q = num(res$QE), p_Q = num(res$QEp), I2 = num(res$I2), H2 = num(res$H2),
    k = res$k
  )
  out
}

pred_summary <- function(res) {
  p <- predict(res)
  list(pi_lower = num(p$pi.lb), pi_upper = num(p$pi.ub))
}

ci_summary <- function(res) {
  ci <- confint(res, control = list(tol = 1e-12))
  r <- ci$random
  list(tau2_lo = num(r["tau^2", "ci.lb"]), tau2_hi = num(r["tau^2", "ci.ub"]),
       I2_lo = num(r["I^2(%)", "ci.lb"]), I2_hi = num(r["I^2(%)", "ci.ub"]),
       H2_lo = num(r["H^2", "ci.lb"]), H2_hi = num(r["H^2", "ci.ub"]))
}

all_methods <- function(dat) {
  out <- list()
  for (m in c("FE", "DL", "REML", "ML", "PM", "HE", "SJ")) {
    res <- rma(yi, vi, data = dat, method = m, control = list(threshold = 1e-12, tol = 1e-12, maxiter = 10000))
    s <- fit_summary(res)
    if (m != "FE") s <- c(s, pred_summary(res))
    out[[m]] <- s
  }
  res <- rma(yi, vi, data = dat, method = "REML", control = list(threshold = 1e-12, maxiter = 10000))
  out[["REML_confint"]] <- ci_summary(res)
  pz <- predict(res)
  res_kn <- rma(yi, vi, data = dat, method = "REML", test = "knha", control = list(threshold = 1e-12, maxiter = 10000))
  out[["REML_knha"]] <- c(fit_summary(res_kn), pred_summary(res_kn))
  res_dl <- rma(yi, vi, data = dat, method = "DL")
  out[["DL_knha"]] <- fit_summary(rma(yi, vi, data = dat, method = "DL", test = "knha"))
  # publikációs torzítás
  eg <- regtest(rma(yi, vi, data = dat, method = "FE"), model = "lm", predictor = "sei")
  # a súlyozott lm-ben (yi ~ sei, súly 1/vi) az sei együtthatója = a klasszikus Egger-tengelymetszet
  cf <- summary(eg$fit)$coefficients
  out[["egger_lm"]] <- list(intercept = num(cf["Xsei", 1]), se = num(cf["Xsei", 2]),
                            t = num(eg$zval), p = num(eg$pval), slope = num(cf["Xintrcpt", 1]))
  rk <- suppressWarnings(ranktest(res))
  out[["ranktest"]] <- list(tau = num(rk$tau), p = num(rk$pval))
  for (est in c("L0", "R0")) {
    tf <- trimfill(res, estimator = est)
    out[[paste0("trimfill_REML_", est)]] <- list(k0 = tf$k0, side = tf$side, se_k0 = num(tf$se.k0),
      estimate = num(tf$b[1]), se = num(tf$se[1]), tau2 = num(tf$tau2))
    tff <- trimfill(rma(yi, vi, data = dat, method = "FE"), estimator = est)
    out[[paste0("trimfill_FE_", est)]] <- list(k0 = tff$k0, side = tff$side, estimate = num(tff$b[1]))
  }
  # érzékenység
  l1o <- leave1out(res)
  out[["leave1out"]] <- list(estimate = num(l1o$estimate), se = num(l1o$se), tau2 = num(l1o$tau2), I2 = num(l1o$I2))
  inf <- influence(res)
  out[["influence"]] <- list(rstudent = num(inf$inf$rstudent), dffits = num(inf$inf$dffits),
    cook_d = num(inf$inf$cook.d), cov_ratio = num(inf$inf$cov.r), tau2_del = num(inf$inf$tau2.del),
    Q_del = num(inf$inf$QE.del), hat = num(inf$inf$hat), dfbetas = num(inf$dfbs[[1]]),
    influential = as.logical(inf$is.infl))
  out
}

ref <- list(metafor_version = as.character(packageVersion("metafor")))

# ---------------------------------------------------------------- BCG (RR)
bcg <- dat.bcg
d <- escalc(measure = "RR", ai = tpos, bi = tneg, ci = cpos, di = cneg, data = bcg)
bcg_rows <- lapply(seq_len(nrow(bcg)), function(i) list(study = paste(bcg$author[i], bcg$year[i]),
  e1 = bcg$tpos[i], n1 = bcg$tpos[i] + bcg$tneg[i], e2 = bcg$cpos[i], n2 = bcg$cpos[i] + bcg$cneg[i],
  ablat = bcg$ablat[i], year = bcg$year[i], alloc = as.character(bcg$alloc[i])))
ref$bcg <- list(rows = bcg_rows, measure = "RR", yi = num(d$yi), vi = num(d$vi), results = all_methods(d))
# meta-regresszió
mr <- list()
for (m in c("REML", "ML", "DL", "FE")) {
  for (tst in c("z", "knha")) {
    r <- rma(yi, vi, mods = ~ ablat, data = d, method = m, test = tst, control = list(threshold = 1e-12, maxiter = 10000))
    mr[[paste(m, tst, sep = "_")]] <- list(b = num(r$b), se = num(r$se), stat = num(r$zval), p = num(r$pval),
      tau2 = num(r$tau2), QM = num(r$QM), QMp = num(r$QMp), QE = num(r$QE), QEp = num(r$QEp),
      I2 = num(r$I2), R2 = num(r$R2))
  }
}
r <- rma(yi, vi, mods = ~ factor(alloc), data = d, method = "REML", control = list(threshold = 1e-12))
mr[["REML_alloc"]] <- list(b = num(r$b), tau2 = num(r$tau2), QM = num(r$QM), QMp = num(r$QMp))
ref$bcg$metareg <- mr
# alcsoportok külön τ²-tel
sg <- list()
for (lev in levels(factor(d$alloc))) {
  r <- rma(yi, vi, data = d, subset = alloc == lev, method = "REML", control = list(threshold = 1e-12))
  sg[[lev]] <- list(k = r$k, estimate = num(r$b), se = num(r$se), tau2 = num(r$tau2))
}
ref$bcg$subgroups_REML <- sg
# MH / Peto
yu_all <- dat.yusuf1985[dat.yusuf1985$table == "6", ]
for (ms in c("OR", "RR", "RD")) {
  r <- rma.mh(ai = tpos, bi = tneg, ci = cpos, di = cneg, data = bcg, measure = ms)
  ref$bcg[[paste0("MH_", ms)]] <- list(estimate = num(r$b), se = num(r$se), Q = num(r$QE), I2 = num(r$I2))
  r <- rma.mh(ai = ai, n1i = n1i, ci = ci, n2i = n2i, data = yu_all, measure = ms)
  ref$bcg[[paste0("YUSUF_MH_", ms)]] <- list(estimate = num(r$b), se = num(r$se), Q = num(r$QE), k = r$k)
}
r <- rma.peto(ai = tpos, bi = tneg, ci = cpos, di = cneg, data = bcg)
ref$bcg$PETO <- list(estimate = num(r$b), se = num(r$se), Q = num(r$QE))
dor <- escalc(measure = "OR", ai = tpos, bi = tneg, ci = cpos, di = cneg, data = bcg)
drd <- escalc(measure = "RD", ai = tpos, bi = tneg, ci = cpos, di = cneg, data = bcg)
ref$bcg$OR_yi <- num(dor$yi); ref$bcg$OR_vi <- num(dor$vi)
ref$bcg$RD_yi <- num(drd$yi); ref$bcg$RD_vi <- num(drd$vi)

# -------------------------------------------------- Normand 1999 (folytonos)
nd <- dat.normand1999
nd_rows <- lapply(seq_len(nrow(nd)), function(i) list(study = nd$source[i], m1 = nd$m1i[i], sd1 = nd$sd1i[i],
  n1 = nd$n1i[i], m2 = nd$m2i[i], sd2 = nd$sd2i[i], n2 = nd$n2i[i]))
ref$normand <- list(rows = nd_rows)
dmd <- escalc(measure = "MD", m1i = m1i, sd1i = sd1i, n1i = n1i, m2i = m2i, sd2i = sd2i, n2i = n2i, data = nd)
ref$normand$MD <- list(yi = num(dmd$yi), vi = num(dmd$vi), results = all_methods(dmd))
for (vt in c("LS", "LS2", "UB")) {
  ds <- escalc(measure = "SMD", m1i = m1i, sd1i = sd1i, n1i = n1i, m2i = m2i, sd2i = sd2i, n2i = n2i, data = nd, vtype = vt)
  ref$normand[[paste0("SMD_", vt)]] <- list(yi = num(ds$yi), vi = num(ds$vi))
}
ds <- escalc(measure = "SMD", m1i = m1i, sd1i = sd1i, n1i = n1i, m2i = m2i, sd2i = sd2i, n2i = n2i, data = nd)
ref$normand$SMD <- list(yi = num(ds$yi), vi = num(ds$vi), results = all_methods(ds))
dr <- escalc(measure = "ROM", m1i = m1i, sd1i = sd1i, n1i = n1i, m2i = m2i, sd2i = sd2i, n2i = n2i, data = nd)
ref$normand$ROM <- list(yi = num(dr$yi), vi = num(dr$vi))

# -------------------------------------------------- Molloy 2014 (korreláció)
mo <- dat.molloy2014
mo_rows <- lapply(seq_len(nrow(mo)), function(i) list(study = paste(mo$authors[i], mo$year[i]), r = mo$ri[i], n = mo$ni[i]))
dz <- escalc(measure = "ZCOR", ri = ri, ni = ni, data = mo)
dc <- escalc(measure = "COR", ri = ri, ni = ni, data = mo)
ref$molloy <- list(rows = mo_rows, ZCOR = list(yi = num(dz$yi), vi = num(dz$vi), results = all_methods(dz)),
                   COR = list(yi = num(dc$yi), vi = num(dc$vi)))
res <- rma(yi, vi, data = dz, method = "REML")
ref$molloy$ZCOR$back_r <- num(transf.ztor(res$b))

# -------------------------------------------------- Pritz 1997 (arány)
pr <- dat.pritz1997
pr_rows <- lapply(seq_len(nrow(pr)), function(i) list(study = paste(pr$authors[i], pr$year[i]), x = pr$xi[i], n = pr$ni[i]))
ref$pritz <- list(rows = pr_rows)
for (ms in c("PR", "PLN", "PLO", "PAS", "PFT")) {
  dp <- escalc(measure = ms, xi = xi, ni = ni, data = pr)
  res <- rma(yi, vi, data = dp, method = "REML")
  entry <- list(yi = num(dp$yi), vi = num(dp$vi), REML = fit_summary(res))
  if (ms == "PFT") entry$back_estimate <- num(transf.ipft.hm(res$b, targs = list(ni = pr$ni)))
  if (ms == "PLO") entry$back_estimate <- num(transf.ilogit(res$b))
  ref$pritz[[ms]] <- entry
}

# ---------------------------------------- Yusuf 1985 (ritka esemény, Peto/MH)
yu <- dat.yusuf1985
yu <- yu[yu$table == "6", ]
yu_rows <- lapply(seq_len(nrow(yu)), function(i) list(study = yu$trial[i], e1 = yu$ai[i], n1 = yu$n1i[i],
  e2 = yu$ci[i], n2 = yu$n2i[i]))
ref$yusuf <- list(rows = yu_rows)
r <- rma.peto(ai = ai, n1i = n1i, ci = ci, n2i = n2i, data = yu)
ref$yusuf$PETO <- list(estimate = num(r$b), se = num(r$se), Q = num(r$QE), k = r$k)
r <- rma.mh(ai = ai, n1i = n1i, ci = ci, n2i = n2i, data = yu, measure = "OR")
ref$yusuf$MH_OR <- list(estimate = num(r$b), se = num(r$se), Q = num(r$QE), k = r$k)
dyo <- escalc(measure = "OR", ai = ai, n1i = n1i, ci = ci, n2i = n2i, data = yu, drop00 = TRUE)
ref$yusuf$OR <- list(yi = num(dyo$yi), vi = num(dyo$vi))

json <- function(x) {
  # minimális JSON-író (a jsonlite nem feltétlenül elérhető)
  if (is.null(x)) return("null")
  if (is.list(x)) {
    nm <- names(x)
    if (!is.null(nm) && all(nm != "")) {
      return(paste0("{", paste0(sprintf("\"%s\":%s", nm, vapply(x, json, "")), collapse = ","), "}"))
    }
    return(paste0("[", paste0(vapply(x, json, ""), collapse = ","), "]"))
  }
  if (is.character(x) || is.factor(x)) {
    x <- as.character(x)
    s <- sprintf("\"%s\"", gsub("\"", "\\\\\"", x))
  } else if (is.logical(x)) {
    s <- ifelse(is.na(x), "null", ifelse(x, "true", "false"))
  } else {
    s <- ifelse(is.na(x) | is.infinite(x), "null", formatC(x, digits = 17, format = "g"))
  }
  if (length(x) == 1) return(s)
  paste0("[", paste0(s, collapse = ","), "]")
}
out_file <- file.path("tests", "reference", "metafor_reference.json")
writeLines(json(ref), out_file, useBytes = TRUE)
cat("OK:", out_file, "\n")
