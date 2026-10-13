#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py -- command line front end.

    python3 cli.py --text "Petrol price may increase by Rs 8 per litre"
    cat bullets.txt | python3 cli.py
    python3 cli.py --json-in '{"title_en":"...","bullets_en":["..."]}' --json
    python3 cli.py --mock                      # offline plumbing test
    python3 cli.py --serve --port 8080         # start the HTTP API
    python3 cli.py --config                    # show effective configuration

Exit codes: 0 = validated translation, 1 = fallback/validation issue,
2 = empty input. Safe to branch on from shell automation.
"""

from __future__ import annotations

import json
import sys

try:                                    # used as a package
    from . import translator as T
except ImportError:                     # folder copied flat / run as a script
    import translator as T              # type: ignore[no-redef]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--config" in argv:
        print(json.dumps(T.effective_config(), ensure_ascii=False, indent=2))
        return 0
    return T.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
