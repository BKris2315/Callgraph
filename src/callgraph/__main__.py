"""``python -m callgraph ...`` — same as the ``callgraph`` command."""

import sys

from .cli import main

sys.exit(main())
