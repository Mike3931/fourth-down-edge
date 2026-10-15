"""Create and migrate a NEW isolated research database; refuse all existing files."""
import argparse
from pathlib import Path

from fde_api.forward.workspace import initialize_workspace

parser = argparse.ArgumentParser()
parser.add_argument("--database-file", type=Path, required=True)
parser.add_argument("--prospective", action="store_true")
args = parser.parse_args()
try:
    initialize_workspace(args.database_file, purpose="PROSPECTIVE" if args.prospective else "CAPTURE_ONLY")
except (FileExistsError, ValueError) as exc:
    parser.error(str(exc))
