"""Create and migrate a NEW isolated research database; refuse all existing files."""
import argparse
from pathlib import Path

from fde_api.forward.workspace import initialize_workspace

parser = argparse.ArgumentParser()
parser.add_argument("--database-file", type=Path, required=True)
args = parser.parse_args()
try:
    initialize_workspace(args.database_file)
except (FileExistsError, ValueError) as exc:
    parser.error(str(exc))
