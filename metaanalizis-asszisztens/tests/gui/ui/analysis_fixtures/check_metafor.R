suppressMessages(library(metafor))
d <- dat.bcg
es <- escalc(measure="RR", ai=tpos, bi=tneg, ci=cpos, di=cneg, data=d, slab=paste(author, year))
m <- rma(yi, vi, data=es, method="REML", test="knha")
k <- m$k
crit <- qt(0.975, k-2)
pi <- c(m$b - crit*sqrt(m$tau2 + m$se^2), m$b + crit*sqrt(m$tau2 + m$se^2))
l1 <- leave1out(m)
# a motor befolyás-diagnosztikája a Wald-modell (method = "REML", test = "z") influence()-ével egyezik
mw <- rma(yi, vi, data=es, method="REML")
inf <- influence(mw)
cu <- cumul(m, order=d$year)
fe <- rma(yi, vi, data=es, method="FE")
infFE <- influence(fe)
sub <- lapply(c("random","alternate","systematic"), function(g) rma(yi, vi, data=es, subset=alloc==g, method="REML", test="knha"))
# Q_between Wald (nem HKSJ): a csoportbecslések Wald-SE-jéből
subw <- lapply(c("random","alternate","systematic"), function(g) rma(yi, vi, data=es, subset=alloc==g, method="REML"))
est <- sapply(subw, function(x) as.numeric(x$b)); se <- sapply(subw, function(x) x$se)
Qb <- rma(est, sei=se, method="FE")$QE
tf <- trimfill(m)
cat(jsonlite::toJSON(list(
  est=as.numeric(m$b), lo=m$ci.lb, hi=m$ci.ub, tau2=m$tau2, I2=m$I2, pval=m$pval, pi=as.numeric(pi),
  loo_est=l1$estimate, loo_lo=l1$ci.lb, loo_hi=l1$ci.ub,
  rstudent=inf$inf$rstudent, dffits=inf$inf$dffits, cook=inf$inf$cook.d, covr=inf$inf$cov.r, hat=inf$inf$hat, dfbs=inf$dfbs$intrcpt, infl=as.logical(inf$is.infl),
  feinfl=as.logical(infFE$is.infl),
  cum_est=cu$estimate, cum_lo=cu$ci.lb, cum_hi=cu$ci.ub,
  sub_est=sapply(sub, function(x) as.numeric(x$b)), sub_lo=sapply(sub, function(x) x$ci.lb), sub_hi=sapply(sub, function(x) x$ci.ub),
  Qb=Qb, k0=tf$k0, slab=es$slab
), digits=NA))
