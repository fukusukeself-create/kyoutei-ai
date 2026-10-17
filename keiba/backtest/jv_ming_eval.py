"""JRA-VAN のマイニング予想 (DM・TM) が、学習外の予想 (oos.csv) に上乗せできるかの検証。
2025年で混ぜ方を決め、2026年で確かめる。jravan/data/MING_*.txt が必要。 python backtest/jv_ming_eval.py
"""
import glob,numpy as np,pandas as pd
from scipy.optimize import minimize
rows=[]
for f in glob.glob('jravan/data/MING_DM_*.txt'):
    for l in open(f,encoding='utf-8'):
        rid=l[11:15]+l[19:21]+l[21:23]+l[23:25]+l[25:27]
        b=l[31:]
        for k in range(18):
            e=b[k*15:(k+1)*15]
            if len(e)<15 or not e[:2].strip() or e[:2]=='00': continue
            t=e[2:7]
            if not t.isdigit() or int(t)==0: continue
            rows.append((rid,int(e[:2]),int(t[0])*60+int(t[1:3])+int(t[3:5])/100))
dm=pd.DataFrame(rows,columns=['race_id','umaban_id','dm_time'])
rows=[]
for f in glob.glob('jravan/data/MING_TM_*.txt'):
    for l in open(f,encoding='utf-8'):
        rid=l[11:15]+l[19:21]+l[21:23]+l[23:25]+l[25:27]
        b=l[31:]
        for k in range(18):
            e=b[k*6:(k+1)*6]
            if len(e)<6 or not e[:2].strip() or e[:2]=='00' or not e[2:].isdigit(): continue
            rows.append((rid,int(e[:2]),int(e[2:])/10))
tm=pd.DataFrame(rows,columns=['race_id','umaban_id','tm'])
print(len(dm),len(tm))
d=pd.read_csv('backtest/data/oos.csv',dtype={'race_id':str}); d=d[d.finish>0]
d=d.merge(dm,on=['race_id','umaban_id'],how='left').merge(tm,on=['race_id','umaban_id'],how='left')
print('coverage dm',d.dm_time.notna().mean().round(3),'tm',d.tm.notna().mean().round(3))
d=d[d.groupby('race_id').dm_time.transform(lambda s:s.notna().all()) & d.groupby('race_id').tm.transform(lambda s:s.notna().all())].copy()
for c in ('p_model','p_market','p_pure'):
    d[c]=d[c].clip(1e-6); d[c]=d[c]/d.groupby('race_id')[c].transform('sum')
g=d.groupby('race_id')
d['dm_z']=-(d.dm_time-g.dm_time.transform('mean'))/g.dm_time.transform('std').replace(0,1)
d['tm_z']=(d.tm-g.tm.transform('mean'))/g.tm.transform('std').replace(0,1)
fit,test=d[d.date<20260101],d[d.date>=20260101]
def prob(w,df,cols):
    s=sum(wi*df[c] for wi,c in zip(w,cols))
    e=np.exp(s-s.groupby(df.race_id).transform('max')); return e/e.groupby(df.race_id).transform('sum')
def nll(p,df): return -np.log(p[df.is_win==1]).mean()
def top1(p,df): return df.assign(p=p).loc[lambda x:x.groupby('race_id').p.idxmax()].is_win.mean()
for name,cols in [('mining only',['dm_z','tm_z']),('market+mining',['lmk','dm_z','tm_z']),('model+mining',['lmo','dm_z','tm_z'])]:
    for df in (fit,test): df['lmk']=np.log(df.p_market); df['lmo']=np.log(df.p_model)
    w=minimize(lambda w:nll(prob(w,fit,cols),fit),[1.0]*len(cols),method='Nelder-Mead').x
    p=prob(w,test,cols)
    print(f"{name:14s} w={np.round(w,3)} test logloss {nll(p,test):.4f} top1 {top1(p,test):.3f}")
print(f"{'market':14s} test logloss {nll(test.p_market,test):.4f} top1 {top1(test.p_market,test):.3f}")
print(f"{'model':14s} test logloss {nll(test.p_model,test):.4f} top1 {top1(test.p_model,test):.3f}  races {test.race_id.nunique()} / fit {fit.race_id.nunique()}")
