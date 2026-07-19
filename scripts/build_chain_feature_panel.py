"""Build chain-only EXP-2026-001 features from canonical VPS partitions."""

from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.calibration.signmap import stable_sign_lookup  # noqa: E402
from src.ingest.io import iter_partitions, read_canonical  # noqa: E402
from src.research.features import chain_features  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True); ap.add_argument("--symbol", required=True)
    ap.add_argument("--sign-map"); ap.add_argument("--out", required=True)
    args = ap.parse_args()
    lookup = None
    if args.sign_map:
        payload = json.loads(Path(args.sign_map).read_text())
        lookup = stable_sign_lookup(payload["sign_map"])
    rows=[]
    for i,(sym,d,_p) in enumerate(iter_partitions(args.root, symbol=args.symbol),1):
        rows.append(chain_features(read_canonical(args.root,sym,d), lookup))
        if i%100==0: print(f"{args.symbol} {i}",flush=True)
    p=Path(args.out); p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(f".{p.name}.{os.getpid()}.tmp"); tmp.write_text(json.dumps(rows,indent=1,sort_keys=True)+"\n"); os.replace(tmp,p)
    print(json.dumps({"symbol":args.symbol,"rows":len(rows),"out":args.out}))
if __name__=="__main__": main()
