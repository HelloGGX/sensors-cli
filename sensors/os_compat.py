"""Cross-platform subprocess creation flags."""

from __future__ import annotations

import os
import subprocess

#: ``CREATE_NO_WINDOW`` on Windows stops every spawned console process
#: (cmd.exe, powershell, runners) from popping up a terminal window. This
#: matters when the parent has no console (detached worker, agent pipes):
#: Windows would otherwise allocate a fresh console with a visible window
#: for each child. On POSIX the flag does not exist and must stay 0.
NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
