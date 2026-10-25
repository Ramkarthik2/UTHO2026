"""Write a saved copy of the resource inventory PDF to docs/generated/.

The app regenerates the PDF from the current data on every request, so the
"View Resource Inventory PDF" link is always up to date. Run this script only
to keep an offline copy, for example to attach to a submission. If the
dataset changes, run it again.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import config as C  # noqa: E402
from server import resource_pdf  # noqa: E402
from server.api import Service  # noqa: E402


def main() -> int:
    db = Path(C.DB_PATH)
    if not db.exists():
        print(f"No dataset found at {db}. Run: python3 scripts/generate_data.py")
        return 1
    data = resource_pdf.build_report(Service(db))
    out = ROOT / "docs" / "generated" / "resource-inventory.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
