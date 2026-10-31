import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mautonomy.data.splits import make_split

_SPLITS_JSON = Path(__file__).parents[1] / "data" / "splits.json"

if __name__ == "__main__":
    manifest = make_split(seed=42)
    _SPLITS_JSON.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Written {_SPLITS_JSON}: dev={len(manifest['dev'])}, eval={len(manifest['eval'])}")
