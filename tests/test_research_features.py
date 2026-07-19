import unittest
import numpy as np
import pandas as pd
from tests.test_gex_metrics import mini_chain
from src.research.features import add_price_features, chain_features
class TestResearchFeatures(unittest.TestCase):
 def test_chain_features_no_outcome_and_bad_iv_excluded(self):
  df=mini_chain([{'type':'call','strike':100,'gamma':999,'open_interest':100,'iv':None},{'type':'put','strike':90,'gamma':.01,'open_interest':100,'iv':.2}],spot=100)
  for c,v in [('bid',1.0),('ask',1.2),('delta',.25),('volume',10)]: df[c]=v
  r=chain_features(df,{})
  self.assertFalse(any('target' in k for k in r)); self.assertTrue(np.isfinite(r['gex_norm_bs_naive']))
 def test_price_features_trailing_only(self):
  idx=pd.date_range('2026-01-01',periods=30,freq='B'); p=pd.DataFrame({'option_volume_notional':1.0},index=idx); b=pd.DataFrame({'close':np.arange(100,130)},index=idx)
  out=add_price_features(p,b); self.assertFalse(any('target' in c for c in out)); self.assertTrue(pd.isna(out['return_5d'].iloc[4])); self.assertTrue(pd.notna(out['return_5d'].iloc[5]))
if __name__=='__main__': unittest.main()
