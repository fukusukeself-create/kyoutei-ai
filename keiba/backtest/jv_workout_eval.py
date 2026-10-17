"""JRA-VAN の調教タイム (坂路 HC・ウッド WC) が、学習外の予想 (oos.csv) に上乗せできるかの検証。
レース前14日の調教を、同じ日・同じトレセン・同じコースの中で標準化して使う。2025年で混ぜ方を決め、2026年で確かめる。
jravan/data/SLOP_HC_*.txt・WOOD_WC_*.txt が必要。 python backtest/jv_workout_eval.py
"""
import glob,sqlite3,numpy as np,pandas as pd,datetime as dt
from scipy.optimize import minimize
def t(s):
    try: v=int(s)
    except ValueError: return np.nan
    return v/10 if v>0 else np.nan
rows=[]
for f in sorted(glob.glob('jravan/data/SLOP_HC_202[4-6].txt')):
    for l in open(f,encoding='utf-8'):
        if len(l.rstrip())<58: continue
        rows.append((l[24:34],l[12:20],int(l[11]),'H',t(l[34:38]),t(l[55:58])))
for f in sorted(glob.glob('jravan/data/WOOD_WC_202[4-6].txt')):
    for l in open(f,encoding='utf-8'):
        l=l.rstrip()
        if len(l)<60: continue
        tail=l[-24:]
        rows.append((l[24:34],l[12:20],int(l[11]),'W',t(tail[0:4]),t(tail[21:24])))
w=pd.DataFrame(rows,columns=['horse_id','wdate','center','kind','f4','l1']).dropna(subset=['f4'])
w=w[w.wdate>='20241101']
g=w.groupby(['kind','center','wdate'])
for c in ('f4','l1'):
    w[c+'_z']=-(w[c]-g[c].transform('mean'))/g[c].transform('std')
print('workouts',len(w))
d=pd.read_csv('backtest/data/oos.csv',dtype={'race_id':str}); d=d[d.finish>0]
con=sqlite3.connect('backtest/data/races.sqlite')
ru=pd.read_sql("select race_id,umaban as umaban_id,horse_id from runners",con)
d=d.merge(ru,on=['race_id','umaban_id'],how='left')
d['dord']=pd.to_datetime(d.date.astype(str))
w['word']=pd.to_datetime(w.wdate)
m=d[['race_id','umaban_id','horse_id','dord']].merge(w,on='horse_id')
m=m[(m.word<m.dord)&(m.word>=m.dord-pd.Timedelta(days=14))]
agg=m.groupby(['race_id','umaban_id']).agg(w_n=('f4','size'),w_best4=('f4_z','max'),w_best1=('l1_z','max'),
    w_hn=('kind',lambda s:(s=='H').sum()))
last=m.sort_values('word').groupby(['race_id','umaban_id']).tail(1).set_index(['race_id','umaban_id'])[['f4_z','l1_z']].rename(columns={'f4_z':'w_last4','l1_z':'w_last1'})
d=d.merge(agg,on=['race_id','umaban_id'],how='left').merge(last,on=['race_id','umaban_id'],how='left')
print('coverage', d.w_n.notna().mean().round(3))
F=['w_best4','w_best1','w_last4','w_last1']
for c in F: d[c+'_m']=d[c].isna().astype(float); d[c]=d[c].fillna(0).clip(-4,4)
d['w_n']=d.w_n.fillna(0).clip(0,8)
for c in ('p_model','p_market'):
    d[c]=d[c].clip(1e-6); d[c]=d[c]/d.groupby('race_id')[c].transform('sum')
d['lmo']=np.log(d.p_model); d['lmk']=np.log(d.p_market)
fit,test=d[d.date<20260101].copy(),d[d.date>=20260101].copy()
def prob(wv,df,cols):
    s=sum(wi*df[c] for wi,c in zip(wv,cols))
    e=np.exp(s-s.groupby(df.race_id).transform('max')); return e/e.groupby(df.race_id).transform('sum')
nll=lambda p,df:-np.log(p[df.is_win==1]).mean()
top1=lambda p,df:df.assign(p=p).loc[lambda x:x.groupby('race_id').p.idxmax()].is_win.mean()
print(f"model only test {nll(test.p_model,test):.4f} top1 {top1(test.p_model,test):.3f}")
for name,cols in [('model+work',['lmo']+F+['w_n','w_best4_m']),('market+work',['lmk']+F+['w_n','w_best4_m'])]:
    wv=minimize(lambda wv:nll(prob(wv,fit,cols),fit),[1.0]+[0.0]*(len(cols)-1),method='Powell').x
    p=prob(wv,test,cols)
    print(f"{name:12s} w={np.round(wv,3)} fit {nll(prob(wv,fit,cols),fit):.4f} test {nll(p,test):.4f} top1 {top1(p,test):.3f}")
cols=['lmo']+F+['w_n','w_best4_m']
wv=minimize(lambda wv:nll(prob(wv,fit,cols),fit),[1.0]+[0.0]*(len(cols)-1),method='Powell').x
for df in (fit,test):
    df['p_w']=prob(wv,df,cols)
import json
pay={r:json.loads(p or '{}') for r,p in con.execute("select race_id,payouts from races")}
def roi(df,col,th,excl=True):
    x=df[(df[col]*df.odds>=th)&(df[col]>=0.05)]
    return len(x),(x.is_win*x.odds).sum()/max(len(x),1)
for name,df in (('2025',fit),('2026',test)):
    for th in (1.0,1.1,1.2):
        a=roi(df,'p_model',th); b=roi(df,'p_w',th)
        print(f"{name} EV>={th}: model {a[0]}点 回収{a[1]*100:.0f}% | +調教 {b[0]}点 回収{b[1]*100:.0f}%")
    top=df.sort_values('p_w',ascending=False).groupby('race_id').head(1)
    for lo in (0.4,0.5):
        x=top[top.p_w>=lo]
        print(f"{name} 本命(+調教)勝率{lo:.0%}以上 {len(x)}R 単勝的中 {x.is_win.mean()*100:.1f}% 3着内 {(x.finish<=3).mean()*100:.1f}%")
