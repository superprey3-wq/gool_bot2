from __future__ import annotations

# Compatibility launcher: monkey_start historically starts this module under the
# child name "matchbook". The actual production exchange source is now BETDAQ.
# Keeping the launcher avoids a risky startup rewrite while removing Matchbook
# network/auth from the running system.
from .betdaq_market_worker import main


if __name__ == "__main__":
    main()
