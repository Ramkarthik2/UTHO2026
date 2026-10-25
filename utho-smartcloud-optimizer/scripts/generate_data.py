"""Generate the synthetic dataset (SQLite database and CSV exports).

Usage:
    python3 scripts/generate_data.py               # database + inventory CSVs
    python3 scripts/generate_data.py --with-metrics  # also writes data/csv/metrics.csv (large)

Every output is synthetic. The seed is fixed, so the output is identical on every run.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import config as C, synth  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(C.DB_PATH))
    ap.add_argument("--seed", type=int, default=C.SEED)
    ap.add_argument("--with-metrics", action="store_true", help="also export metrics.csv")
    args = ap.parse_args()

    summary = synth.generate(Path(args.db), args.seed)
    print("Generated synthetic database:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    files = synth.write_csvs(Path(args.db), C.CSV_DIR, include_metrics=args.with_metrics)
    print("CSV exports:")
    for f in files:
        print("  ", f)


if __name__ == "__main__":
    main()
