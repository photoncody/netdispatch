"""Type text into a QEMU test VM via its monitor socket.

usage: sudo python3 sendkeys.py <bios|uefi|uefi-sb|win|win-sb> "text\\n"
'\\n' becomes Enter. Special keys: pass e.g. "{esc}" or "{meta_l-r}" (QEMU key names).
Note: the Windows key is unreliable through QEMU; prefer typing into a focused window.
"""

import re
import socket
import sys
import time

KEYMAP = {" ": "spc", "\n": "ret", "-": "minus", ".": "dot", "/": "slash", "\\": "backslash",
          ":": "shift-semicolon", ">": "shift-dot", "|": "shift-backslash", "_": "shift-minus",
          "=": "equal", ",": "comma", ";": "semicolon", "'": "apostrophe"}

vm = sys.argv[1]
text = " ".join(sys.argv[2:]).replace("\\n", "\n")
keys: list[str] = []
for token in re.split(r"(\{[^}]+\})", text):
    if token.startswith("{") and token.endswith("}"):
        keys.append(token[1:-1])
        continue
    for ch in token:
        keys.append(KEYMAP.get(ch) or (f"shift-{ch.lower()}" if ch.isupper() else ch))

s = socket.socket(socket.AF_UNIX)
s.connect(f"/tmp/nd-{vm}.mon")
s.recv(4096)
for key in keys:
    s.sendall(f"sendkey {key}\n".encode())
    time.sleep(0.08)
    s.recv(4096)
s.close()
