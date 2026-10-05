import json, math, os
W = os.environ.get('MA_FIXTURE_WORK', '.')
m = json.load(open(os.path.join(W, 'metafor.json')))
g = json.load(open(os.path.join(W, 'gen_state.json')))
p = g['plots']['primary']
fx = g['plots']['fixed']
def mx(a, b): return max(abs(x - y) for x, y in zip(a, b))
ov = p['summaries'][0]
print('overall', abs(ov['estimate'] - m['est'][0]), abs(ov['ci_lower'] - m['lo'][0]), abs(ov['ci_upper'] - m['hi'][0]))
print('PI', abs(ov['pi_lower'] - m['pi'][0]), abs(ov['pi_upper'] - m['pi'][1]))
print('tau2', abs(p['heterogeneity']['tau2'] - m['tau2'][0]))
slab = m['slab']
lbl = [s.replace('Frimodt-Moller', 'Frimodt-Moller') for s in slab]
# metafor slab: "Aronson 1948" etc. 
by = {e['label']: e for e in p['loo']}
names = ['Aronson 1948','Ferguson & Simes 1949','Rosenthal et al 1960','Hart & Sutherland 1977','Frimodt-Moller et al 1973','Stein & Aronson 1953','Vandiviere et al 1973','TPT Madras 1980','Coetzee & Berjak 1968','Rosenthal et al 1961','Comstock et al 1974','Comstock & Webster 1969','Comstock et al 1976']
print('LOO est', mx([by[n]['estimate'] for n in names], m['loo_est']), 'lo', mx([by[n]['ci_lower'] for n in names], m['loo_lo']), 'hi', mx([by[n]['ci_upper'] for n in names], m['loo_hi']))
inf = {e['label']: e for e in p['influence']}
for key, mk in (('rstudent','rstudent'),('dffits','dffits'),('cook_d','cook'),('cov_ratio','covr'),('hat','hat'),('dfbetas','dfbs')):
    print('infl', key, mx([inf[n][key] for n in names], m[mk]))
print('infl flags REML egyezik:', [inf[n]['influential'] for n in names] == m['infl'])
finf = {e['label']: e for e in fx['influence']}
print('infl flags FE egyezik:', [finf[n]['influential'] for n in names] == m['feinfl'], [n for n, f in zip(names, m['feinfl']) if f])
c = p['cumulative']['entries']
print('cumul est', mx([e['estimate'] for e in c], m['cum_est']), mx([e['ci_lower'] for e in c], m['cum_lo']), mx([e['ci_upper'] for e in c], m['cum_hi']))
secs = {s['id']: s['summary'] for s in p['sections']}
print('sub', mx([secs[x]['estimate'] for x in ('random','alternate','systematic')], m['sub_est']), mx([secs[x]['ci_lower'] for x in ('random','alternate','systematic')], m['sub_lo']))
print('Qb', abs(p['subgroup_test']['Q'] - m['Qb'][0]))
print('k0', m['k0'])
