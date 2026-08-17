from __future__ import annotations

import sys

from webPage.server import WEB_URL, main

if __name__ == "__main__":
    if len(sys.argv) != 1:
        print(
            f"AutoEnv Web has one fixed startup endpoint ({WEB_URL}) and accepts no options.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
