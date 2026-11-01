from pathlib import Path
import csv, io, re, json
from datetime import datetime, timezone
from decimal import Decimal
import requests
from bs4 import BeautifulSoup

URL = 'https://www.globalxetfs.com.hk/funds/covered-call-etf/'
D = lambda x: Decimal(x.replace(',', '').replace('HKD$', '').strip())
r = requests.get(URL, timeout=45, headers={'User-Agent':'Mozilla/5.0 (compatible; holdings-research/1.0)'})
r.raise_for_status(); r.encoding = 'utf-8'
s = BeautifulSoup(r.text, 'html.parser')
table = s.select_one('#top-ten')
if not table: raise ValueError('Holdings table missing')
heading = table.find_previous('h3')
date_text = heading.select_one('small').get_text(' ', strip=True).removeprefix('As of ')
date = datetime.strptime(date_text, '%d %b %Y').date().isoformat()
option_heading = s.find('h3', id='Holdings')
if not option_heading or date_text != option_heading.select_one('small').get_text(' ',strip=True).removeprefix('As of '): raise ValueError('Option and holdings dates differ')
rows = [[c.get_text(' ',strip=True) for c in tr.select('td')] for tr in table.select('tbody tr')]
if len(rows) < 20 or any(len(row)!=7 for row in rows): raise ValueError('Unexpected holdings table')
stock = future = option_contracts = Decimal(0)
for name,ticker,exchange,price,qty,value,weight in rows:
    q=D(qty)
    if ticker.endswith(' HK'):
        if ticker == '3137 HK': continue
        if ' ETF' in name.upper(): raise ValueError('Unclassified ETF: '+name)
        if q < 0: raise ValueError('Short security: '+name)
        stock += D(value)
    elif re.fullmatch(r'HANG SENG IDX FUT \d\d/\d\d/\d\d',name):
        if q < 0: raise ValueError('Short futures: '+name)
        future += q*D(price)*50
    elif re.fullmatch(r'CALL HSI (?:WEEKLY )?OPTIONS? \d\d/\d\d/\d\d [\d,]+',name):
        if q >= 0: raise ValueError('Non-short option: '+name)
        option_contracts += -q
    else: raise ValueError('Unclassified position: '+repr((name,ticker)))
index_rows = [tr for tr in s.select('tr') if 'Closing level of Hang Seng Index' in tr.get_text(' ',strip=True)]
if len(index_rows)!=1: raise ValueError('Index close missing')
index=D(index_rows[0].select('td')[1].get_text(strip=True))
option_tables=[t for t in s.select('table') if 'Option Position' in t.get_text(' ',strip=True) and 'Notional Exposure' in t.get_text(' ',strip=True)]
if len(option_tables)!=1: raise ValueError('Option details table missing')
option_rows=[[c.get_text(' ',strip=True) for c in tr.select('td')] for tr in option_tables[0].select('tr') if tr.select('td')]
if not option_rows or any(len(x)<2 or not re.search(r'Short.*Call Option',x[0],re.I) for x in option_rows): raise ValueError('Unknown option disclosure')
official=sum(-D(x[1]) for x in option_rows)
calculated=option_contracts*index*50
if not option_contracts or abs(calculated-official)>max(Decimal('1'),official*Decimal('0.0001')): raise ValueError('Option notional mismatch')
if stock+future<=0: raise ValueError('Invalid denominator')
out=Path('data'); raw=out/'holdings'; raw.mkdir(parents=True,exist_ok=True)
filename=raw/(date+'.csv')
buf=io.StringIO(newline=''); writer=csv.writer(buf);writer.writerow([c.get_text(' ',strip=True) for c in table.select('thead th')]);writer.writerows(rows)
content=buf.getvalue()
if filename.exists() and filename.read_text(encoding='utf-8-sig').splitlines()!=content.splitlines(): raise ValueError('Existing snapshot differs; inspect revision manually')
filename.write_text(content,encoding='utf-8-sig',newline='')
fields=['date','equity_hkd','futures_notional_hkd','short_call_notional_hkd','coverage_pct','hsi_close','option_contracts','source']
path=out/'coverage.csv'; prior={}
if path.exists():
    with path.open(encoding='utf-8-sig',newline='') as f: prior={x['date']:x for x in csv.DictReader(f)}
prior[date]=dict(zip(fields,[date,str(stock),str(future),str(official),str((official/(stock+future)*100).quantize(Decimal('.0001'))),str(index),str(option_contracts),URL]))
with path.open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(prior[k] for k in sorted(prior))
meta={'date':date,'captured_at_utc':datetime.now(timezone.utc).isoformat(),'index_close':str(index),'option_details':option_rows,'url':URL}
mp=out/'details';mp.mkdir(exist_ok=True); mpath=mp/(date+'.json')
if not mpath.exists(): mpath.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(prior[date],ensure_ascii=False))


