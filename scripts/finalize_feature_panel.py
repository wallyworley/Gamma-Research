"""Join chain features to same-day/trailing bars without constructing outcomes."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.vol_forecast_experiment import load_bars  # noqa:E402
from src.research.features import add_price_features  # noqa:E402
def main():
 ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--chain',required=True); ap.add_argument('--bars',required=True); ap.add_argument('--market-bars'); ap.add_argument('--out',required=True); a=ap.parse_args()
 x=pd.DataFrame(json.loads(Path(a.chain).read_text())); x.index=pd.to_datetime(x.pop('date'))
 b=load_bars(a.bars); m=load_bars(a.market_bars)['close'] if a.market_bars else None
 out=add_price_features(x,b,m); Path(a.out).parent.mkdir(parents=True,exist_ok=True); out.reset_index(names='date').to_csv(a.out,index=False)
 print(json.dumps({'rows':len(out),'columns':list(out.columns),'out':a.out}))
if __name__=='__main__': main()
