# Differential-testing oracle: metafor results for the fuzz datasets (see gen.py).
#
# Usage:  Rscript tests/fuzz/run_metafor.R <datasets.json> <out.json> [group1,group2,...]
#
# Groups (third argument; empty = all):
#   escalc rma confint regtest ranktest trimfill leave1out influence mods subgroup cumul
#   mh peto harbord peters rma_es
#
# Every metafor call is wrapped in safe(): an error becomes list(error = "<message>") and the
# warnings raised during the call are kept in $warn, so one failing call never aborts the run.
# Output: {"<dataset id>": {"<group>": {"<key>": {...}}}} written with jsonlite (17 significant
# digits; NA/NaN/Inf as the strings "NA"/"NaN"/"Inf"/"-Inf").
#
# Convergence control: metafor's Fisher-scoring threshold and uniroot tolerances are absolute,
# so they are scaled by the median sampling variance s (threshold 1e-12*s, retried with 1e-10*s
# and 1e-8*s on non-convergence; uniroot tol 1e-12*s); tau2.max = 1e8.

suppressPackageStartupMessages({
  library(metafor)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("usage: Rscript run_metafor.R <datasets.json> <out.json> [groups]")
inp <- args[1]
outp <- args[2]
only <- if (length(args) >= 3 && nzchar(args[3])) strsplit(args[3], ",")[[1]] else character(0)
want <- function(g) length(only) == 0L || g %in% only

D <- fromJSON(inp, simplifyVector = FALSE)

num <- function(x) as.numeric(unlist(x))

safe <- function(expr) {
  w <- character(0)
  val <- withCallingHandlers(
    tryCatch(expr, error = function(e) list(error = conditionMessage(e))),
    warning = function(cnd) {
      w <<- c(w, conditionMessage(cnd))
      invokeRestart("muffleWarning")
    })
  if (length(w) && is.list(val) && is.null(val$error)) val$warn <- unique(w)
  val
}

ctrl_for <- function(vi, f = 1e-12) {
  s <- stats::median(vi)
  list(threshold = f * s, tol = 1e-12 * s, maxiter = 10000, tau2.max = 1e8, stepadj = 1)
}
cictrl_for <- function(vi) {
  s <- stats::median(vi)
  list(tol = 1e-12 * s, maxiter = 10000, tau2.max = 1e8)
}

# rma.uni with scaled convergence control; on Fisher-scoring non-convergence retry with looser
# thresholds and then with step halving (stepadj 0.5, 0.25). attr(fit, "thr") / "stepadj" record
# what was needed (the comparator tags such fits as "mf_loose_thr").
fit_rma <- function(yi, vi, method, test = "z", mods = NULL, tau2 = NULL) {
  last <- NULL
  tries <- list(c(1e-12, 1), c(1e-10, 1), c(1e-8, 1), c(1e-10, 0.5), c(1e-8, 0.25))
  for (tr in tries) {
    ctrl <- ctrl_for(vi, tr[1])
    ctrl$stepadj <- tr[2]
    res <- tryCatch({
      if (is.null(mods)) {
        if (is.null(tau2)) rma(yi, vi, method = method, test = test, control = ctrl)
        else rma(yi, vi, method = method, test = test, tau2 = tau2, control = ctrl)
      } else {
        rma(yi, vi, mods = mods, method = method, test = test, control = ctrl)
      }
    }, error = function(e) e)
    if (!inherits(res, "error")) {
      attr(res, "thr") <- if (tr[2] < 1) tr[1] * 10 else tr[1]
      return(res)
    }
    last <- res
    if (!grepl("converge", conditionMessage(res))) break
  }
  stop(conditionMessage(last))
}

# Same model fitted with step halving (stepadj = 0.5): leave1out()/influence()/cumul()/trimfill()
# reuse the fit's control list for their internal refits, so this is how an internal
# non-convergence can be retried.
fit_rma_sa <- function(yi, vi, method, test = "z") {
  ctrl <- ctrl_for(vi, 1e-10)
  ctrl$stepadj <- 0.5
  f <- rma(yi, vi, method = method, test = test, control = ctrl)
  attr(f, "thr") <- 1e-9
  f
}

# Run fun(fit); if it fails with a convergence error or returns NA in `key`, rerun it on the
# step-halving fit and fill the NA positions (marks the result with $retry = TRUE).
with_retry <- function(fun, yi, vi, method, test = "z", key = "estimate") {
  fit <- fit_rma(yi, vi, method, test)
  res <- tryCatch(fun(fit), error = function(e) e)
  bad <- inherits(res, "error") || (method %in% c("REML", "ML") && any(is.na(res[[key]])))
  if (!bad) return(res)
  res2 <- tryCatch(fun(fit_rma_sa(yi, vi, method, test)), error = function(e) e)
  if (inherits(res2, "error")) {
    if (inherits(res, "error")) stop(conditionMessage(res))
    return(res)
  }
  if (inherits(res, "error")) {
    res2$retry <- TRUE
    return(res2)
  }
  na <- is.na(res[[key]])
  for (nm in names(res)) {
    if (length(res[[nm]]) == length(na) && length(res2[[nm]]) == length(na)) res[[nm]][na] <- res2[[nm]][na]
  }
  res$retry <- TRUE
  res
}

rma_out <- function(fit) {
  o <- list(beta = c(fit$beta), se = fit$se, zval = fit$zval, pval = fit$pval,
            ci.lb = fit$ci.lb, ci.ub = fit$ci.ub, tau2 = fit$tau2, QE = fit$QE, QEp = fit$QEp,
            I2 = fit$I2, H2 = fit$H2, thr = attr(fit, "thr"))
  o
}

pred_out <- function(fit) {
  p <- predict(fit)
  r <- predict(fit, pi.type = "riley")
  list(pi.lb = p$pi.lb, pi.ub = p$pi.ub, pi.lb.riley = r$pi.lb, pi.ub.riley = r$pi.ub)
}

mr_out <- function(fit) {
  list(b = c(fit$beta), se = fit$se, zval = fit$zval, pval = fit$pval, ci.lb = fit$ci.lb,
       ci.ub = fit$ci.ub, QM = fit$QM, QMp = fit$QMp, QE = fit$QE, QEp = fit$QEp,
       tau2 = fit$tau2, I2 = if (is.null(fit$I2)) NA else fit$I2,
       R2 = if (is.null(fit$R2)) NA else fit$R2, thr = attr(fit, "thr"))
}

METHODS <- c("FE", "DL", "REML", "ML", "PM", "HE", "SJ")
TESTS <- c("z", "t", "knha", "adhoc")

# ------------------------------------------------------------------ yi/vi datasets
do_uni <- function(d) {
  yi <- num(d$yi); vi <- num(d$vi); k <- length(yi)
  R <- list()
  if (want("rma")) {
    R$rma <- list()
    for (m in METHODS) for (tst in TESTS) {
      R$rma[[paste(m, tst, sep = "_")]] <- safe({
        fit <- fit_rma(yi, vi, m, tst)
        o <- rma_out(fit)
        if (m != "FE") o <- c(o, pred_out(fit))
        o
      })
    }
  }
  if (want("confint") && k >= 2) {
    R$confint <- list()
    for (m in c("REML", "DL")) {
      R$confint[[m]] <- safe({
        fit <- fit_rma(yi, vi, m)
        ci <- confint(fit, control = cictrl_for(vi))
        r <- ci$random
        list(tau2 = unname(r["tau^2", ]), I2 = unname(r["I^2(%)", ]), H2 = unname(r["H^2", ]))
      })
    }
  }
  if (k >= 3) {
    if (want("regtest")) {
      R$regtest <- list(lm = safe({
        rt <- regtest(yi, vi, model = "lm")
        cf <- coef(summary(rt$fit))
        ci <- confint(rt$fit)
        list(zval = rt$zval, pval = rt$pval, dfs = rt$dfs, est = rt$est, est.ci.lb = rt$ci.lb,
             est.ci.ub = rt$ci.ub, b_int = cf[1, 1], se_int = cf[1, 2], b_sei = cf[2, 1],
             se_sei = cf[2, 2], ci.lb_sei = ci[2, 1], ci.ub_sei = ci[2, 2])
      }))
    }
    if (want("ranktest")) {
      R$ranktest <- list(default = safe({
        r <- ranktest(yi, vi)
        list(tau = r$tau, pval = r$pval)
      }))
    }
    if (want("trimfill")) {
      R$trimfill <- list()
      for (m in c("FE", "DL", "REML")) for (est in c("L0", "R0")) {
        R$trimfill[[paste(m, est, sep = "_")]] <- safe({
          fit <- fit_rma(yi, vi, m)
          tf <- tryCatch(trimfill(fit, estimator = est), error = function(e) e)
          if (inherits(tf, "error")) {
            if (!grepl("converge", conditionMessage(tf))) stop(conditionMessage(tf))
            tf <- trimfill(fit_rma_sa(yi, vi, m), estimator = est)   # internal refit retry
          }
          o <- list(k0 = tf$k0, side = tf$side, se.k0 = tf$se.k0, beta = c(tf$beta), se = tf$se,
                    tau2 = tf$tau2, ci.lb = tf$ci.lb, ci.ub = tf$ci.ub, pval = tf$pval,
                    fill = if (is.null(tf$fill)) numeric(0) else as.numeric(tf$yi[tf$fill]))
          # metafor 4.4 refits the filled data WITHOUT the model's control list (default
          # threshold 1e-5), so for REML/ML its estimate is only ~5 digits accurate: refit the
          # same filled data (same method, test z, 95 %) with the tight control instead.
          if (!is.null(tf$fill) && any(tf$fill)) {
            rf <- fit_rma(as.numeric(tf$yi), as.numeric(tf$vi), m)
            o$beta <- c(rf$beta); o$se <- rf$se; o$tau2 <- rf$tau2; o$ci.lb <- rf$ci.lb
            o$ci.ub <- rf$ci.ub; o$pval <- rf$pval
            o$beta_metafor_default_ctrl <- c(tf$beta)
          }
          o
        })
      }
    }
  }
  if (want("leave1out") && k >= 2) {
    R$leave1out <- list()
    for (cfg in list(c("REML", "z"), c("DL", "knha"), c("FE", "z"), c("PM", "z"))) {
      R$leave1out[[paste(cfg, collapse = "_")]] <- safe(with_retry(function(fit) {
        l <- leave1out(fit)
        list(estimate = l$estimate, se = l$se, zval = l$zval, pval = l$pval, ci.lb = l$ci.lb,
             ci.ub = l$ci.ub, Q = l$Q, Qp = l$Qp, tau2 = l$tau2, I2 = l$I2, H2 = l$H2)
      }, yi, vi, cfg[1], cfg[2]))
    }
  }
  if (want("influence") && k >= 2) {
    R$influence <- list()
    for (m in c("REML", "DL", "FE")) {
      R$influence[[m]] <- safe(with_retry(function(fit) {
        inf <- influence(fit)
        x <- inf$inf
        list(rstudent = x$rstudent, dffits = x$dffits, cook.d = x$cook.d, cov.r = x$cov.r,
             tau2.del = x$tau2.del, QE.del = x$QE.del, hat = x$hat, weight = x$weight,
             dfbs = as.numeric(inf$dfbs[[1]]), inf = as.logical(inf$is.infl), tau2 = fit$tau2)
      }, yi, vi, m, key = "rstudent"))
    }
  }
  if (want("mods") && !is.null(d$mods)) {
    X <- do.call(rbind, lapply(d$mods, num))
    R$mods <- list()
    for (m in METHODS) {
      t0 <- tryCatch(fit_rma(yi, vi, m)$tau2, error = function(e) NA_real_)   # R2 reference model
      for (tst in c("z", "knha")) {
        R$mods[[paste(m, tst, sep = "_")]] <- safe(c(mr_out(fit_rma(yi, vi, m, tst, mods = X)), list(tau2_0 = t0)))
      }
    }
  }
  if (want("subgroup") && !is.null(d$groups)) {
    g <- factor(unlist(d$groups))
    levs <- levels(g)
    R$subgroup <- list()
    for (m in c("REML", "DL")) {
      R$subgroup[[paste0(m, "_sep")]] <- safe({
        ests <- ses <- tau2s <- ks <- numeric(0)
        for (lev in levs) {
          sel <- g == lev
          f <- fit_rma(yi[sel], vi[sel], m)
          ests <- c(ests, c(f$beta)); ses <- c(ses, f$se); tau2s <- c(tau2s, f$tau2); ks <- c(ks, sum(sel))
        }
        o <- list(levels = levs, estimate = ests, se = ses, tau2 = tau2s, k = ks)
        if (length(levs) >= 2) {
          qb <- rma(ests, sei = ses, mods = ~ factor(levs), method = "FE")
          o$QM <- qb$QM; o$QMp <- qb$QMp
        }
        o
      })
      R$subgroup[[paste0(m, "_common")]] <- safe({
        Xg <- model.matrix(~ g)[, -1, drop = FALSE]
        f <- fit_rma(yi, vi, m, mods = Xg)
        ests <- ses <- numeric(0)
        for (lev in levs) {
          sel <- g == lev
          fg <- fit_rma(yi[sel], vi[sel], m, tau2 = f$tau2)
          ests <- c(ests, c(fg$beta)); ses <- c(ses, fg$se)
        }
        list(levels = levs, tau2 = f$tau2, QM = f$QM, QMp = f$QMp, estimate = ests, se = ses)
      })
    }
  }
  if (want("cumul") && !is.null(d$year) && k >= 2) {
    yr <- num(d$year)
    R$cumul <- list()
    for (cfg in list(c("REML", "z"), c("DL", "knha"))) {
      o <- order(yr)        # stable, ties keep the input order (as the engine)
      R$cumul[[paste(cfg, collapse = "_")]] <- safe(with_retry(function(fit) {
        cu <- cumul(fit)
        list(estimate = cu$estimate, se = cu$se, ci.lb = cu$ci.lb, ci.ub = cu$ci.ub,
             tau2 = cu$tau2, I2 = cu$I2, order = o)
      }, yi[o], vi[o], cfg[1], cfg[2]))
    }
  }
  R
}

esc <- function(...) {
  safe({
    e <- escalc(...)
    list(yi = as.numeric(e$yi), vi = as.numeric(e$vi))
  })
}

rma_es <- function(e, method) {
  safe({
    if (!is.null(e$error)) stop(e$error)
    ok <- is.finite(e$yi) & is.finite(e$vi) & e$vi > 0
    fit <- fit_rma(e$yi[ok], e$vi[ok], method)
    list(beta = c(fit$beta), se = fit$se, tau2 = fit$tau2, k = fit$k)
  })
}

do_bin <- function(d) {
  df <- do.call(rbind, lapply(d$rows, function(r) data.frame(e1 = as.numeric(r$e1), n1 = as.numeric(r$n1),
                                                             e2 = as.numeric(r$e2), n2 = as.numeric(r$n2))))
  R <- list()
  if (want("escalc")) {
    E <- list()
    for (m in c("OR", "RR")) {
      E[[paste0(m, "_d00")]] <- esc(m, ai = df$e1, n1i = df$n1, ci = df$e2, n2i = df$n2, drop00 = TRUE)
    }
    E$RD_def <- esc("RD", ai = df$e1, n1i = df$n1, ci = df$e2, n2i = df$n2)
    for (m in c("OR", "RR", "RD")) {
      E[[paste0(m, "_all")]] <- esc(m, ai = df$e1, n1i = df$n1, ci = df$e2, n2i = df$n2, to = "all")
      E[[paste0(m, "_none")]] <- esc(m, ai = df$e1, n1i = df$n1, ci = df$e2, n2i = df$n2, to = "none")
    }
    R$escalc <- E
    if (want("rma_es")) {
      R$rma_es <- list(OR_d00_REML = rma_es(E$OR_d00, "REML"), RD_all_DL = rma_es(E$RD_all, "DL"))
    }
  }
  if (want("mh")) {
    R$mh <- list()
    for (m in c("OR", "RR", "RD")) {
      R$mh[[m]] <- safe({
        f <- rma.mh(ai = df$e1, n1i = df$n1, ci = df$e2, n2i = df$n2, measure = m)
        list(beta = c(f$beta), se = f$se, zval = f$zval, pval = f$pval, ci.lb = f$ci.lb, ci.ub = f$ci.ub,
             QE = f$QE, QEp = f$QEp, I2 = f$I2, H2 = f$H2, k = f$k, k.yi = f$k.yi)
      })
    }
  }
  if (want("peto")) {
    R$peto <- list(OR = safe({
      f <- rma.peto(ai = df$e1, n1i = df$n1, ci = df$e2, n2i = df$n2)
      list(beta = c(f$beta), se = f$se, zval = f$zval, pval = f$pval, ci.lb = f$ci.lb, ci.ub = f$ci.ub,
           QE = f$QE, QEp = f$QEp, I2 = f$I2, H2 = f$H2, k = f$k, k.yi = f$k.yi)
    }))
  }
  # Harbord and Peters tests: re-implemented with R's lm() from their published definitions
  # (metafor's regtest() has no exact equivalent: its weights are 1/vi)
  if (want("harbord")) {
    R$harbord <- list(lm = safe({
      a <- df$e1; c <- df$e2; m1 <- df$n1; m2 <- df$n2; n <- m1 + m2; ev <- a + c; nev <- n - ev
      ok <- n > 1 & ev > 0 & nev > 0
      a <- a[ok]; c <- c[ok]; m1 <- m1[ok]; m2 <- m2[ok]; n <- n[ok]; ev <- ev[ok]; nev <- nev[ok]
      Z <- a - ev * m1 / n
      V <- m1 * m2 * ev * nev / (n^2 * (n - 1))
      if (length(Z) < 3) stop("k < 3")
      cf <- coef(summary(lm(I(Z / sqrt(V)) ~ sqrt(V))))
      list(intercept = cf[1, 1], se = cf[1, 2], t = cf[1, 3], p = cf[1, 4], slope = cf[2, 1], k = length(Z))
    }))
  }
  if (want("peters")) {
    R$peters <- list(lm = safe({
      a <- df$e1; b <- df$n1 - df$e1; c <- df$e2; dd <- df$n2 - df$e2
      ok <- !((a == 0 & c == 0) | (b == 0 & dd == 0))
      a <- a[ok]; b <- b[ok]; c <- c[ok]; dd <- dd[ok]
      w <- 1 / (1 / (a + c) + 1 / (b + dd))
      x <- 1 / (a + b + c + dd)
      z0 <- pmin(a, b, c, dd) == 0
      a[z0] <- a[z0] + 0.5; b[z0] <- b[z0] + 0.5; c[z0] <- c[z0] + 0.5; dd[z0] <- dd[z0] + 0.5
      y <- log(a * dd / (b * c))
      if (length(y) < 3) stop("k < 3")
      cf <- coef(summary(lm(y ~ x, weights = w)))
      list(slope = cf[2, 1], se = cf[2, 2], t = cf[2, 3], p = cf[2, 4], k = length(y))
    }))
  }
  R
}

do_cont <- function(d) {
  df <- do.call(rbind, lapply(d$rows, function(r) data.frame(m1 = r$m1, sd1 = r$sd1, n1 = r$n1,
                                                             m2 = r$m2, sd2 = r$sd2, n2 = r$n2)))
  R <- list()
  if (want("escalc")) {
    E <- list()
    a <- function(measure, ...) esc(measure, m1i = df$m1, sd1i = df$sd1, n1i = df$n1, m2i = df$m2,
                                   sd2i = df$sd2, n2i = df$n2, ...)
    E$MD_LS <- a("MD", vtype = "LS")
    E$MD_HO <- a("MD", vtype = "HO")
    for (vt in c("LS", "LS2", "UB")) {
      E[[paste0("SMD_", vt)]] <- a("SMD", vtype = vt)
      E[[paste0("SMD1_", vt)]] <- a("SMD1", vtype = vt)
    }
    E$COHEN_LS <- a("SMD", vtype = "LS", correct = FALSE)
    E$COHEN_LS2 <- a("SMD", vtype = "LS2", correct = FALSE)
    E$SMD1H <- a("SMD1H")
    E$ROM <- a("ROM")
    R$escalc <- E
    if (want("rma_es")) R$rma_es <- list(SMD_LS_REML = rma_es(E$SMD_LS, "REML"))
  }
  R
}

do_paired <- function(d) {
  df <- do.call(rbind, lapply(d$rows, function(r) data.frame(m1 = r$m1, m2 = r$m2, sd1 = r$sd1,
                                                             sd2 = r$sd2, r = r$r, n = r$n)))
  R <- list()
  if (want("escalc")) {
    a <- function(measure, ...) esc(measure, m1i = df$m1, m2i = df$m2, sd1i = df$sd1, sd2i = df$sd2,
                                   ri = df$r, ni = df$n, ...)
    R$escalc <- list(MC = a("MC"), SMCC_LS = a("SMCC", vtype = "LS"), SMCC_LS2 = a("SMCC", vtype = "LS2"))
  }
  R
}

do_prop <- function(d) {
  df <- do.call(rbind, lapply(d$rows, function(r) data.frame(x = r$x, n = r$n)))
  R <- list()
  if (want("escalc")) {
    E <- list()
    for (m in c("PR", "PLN", "PLO", "PAS", "PFT")) {
      E[[m]] <- esc(m, xi = df$x, ni = df$n)
      E[[paste0(m, "_all")]] <- esc(m, xi = df$x, ni = df$n, to = "all")
    }
    R$escalc <- E
    if (want("rma_es")) R$rma_es <- list(PLO_REML = rma_es(E$PLO, "REML"))
  }
  R
}

do_cor <- function(d) {
  df <- do.call(rbind, lapply(d$rows, function(r) data.frame(r = r$r, n = r$n)))
  R <- list()
  if (want("escalc")) {
    E <- list(COR = esc("COR", ri = df$r, ni = df$n), ZCOR = esc("ZCOR", ri = df$r, ni = df$n))
    R$escalc <- E
    if (want("rma_es")) R$rma_es <- list(ZCOR_REML = rma_es(E$ZCOR, "REML"))
  }
  R
}

HANDLERS <- list(uni = do_uni, bin = do_bin, cont = do_cont, paired = do_paired, prop = do_prop, cor = do_cor)

OUT <- list()
for (d in D) {
  h <- HANDLERS[[d$type]]
  OUT[[d$id]] <- if (is.null(h)) list(error = paste("unknown type", d$type)) else safe(h(d))
}
writeLines(toJSON(OUT, auto_unbox = TRUE, digits = I(17), na = "string", null = "null"), outp,
           useBytes = TRUE)
cat("ok", length(OUT), "\n")
