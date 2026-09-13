# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import sys
from pathlib import Path
from types import ModuleType

PACKAGE_NAME = "card_janitor"
SRC_DIR = Path(__file__).resolve().parents[1] / "src"

package = ModuleType(PACKAGE_NAME)
package.__path__ = [str(SRC_DIR)]
sys.modules.setdefault(PACKAGE_NAME, package)
sys.path.insert(0, str(SRC_DIR))
