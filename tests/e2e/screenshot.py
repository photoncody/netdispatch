"""Screenshot a QEMU test VM's display via its monitor socket, saved as PNG.

usage: sudo python3 screenshot.py <bios|uefi|win> <out.png>
(no dependencies: converts QEMU's PPM screendump with zlib)
"""

import socket
import struct
import sys
import time
import zlib

mode, out = sys.argv[1], sys.argv[2]
ppm = f"/tmp/nd-{mode}.ppm"
s = socket.socket(socket.AF_UNIX)
s.connect(f"/tmp/nd-{mode}.mon")
s.recv(4096)
s.sendall(f"screendump {ppm}\n".encode())
time.sleep(1.5)
s.close()

data = open(ppm, "rb").read()
magic, dims, _maxval, pixels = data.split(b"\n", 3)
assert magic == b"P6", magic
w, h = map(int, dims.split())
raw = b"".join(b"\x00" + pixels[y * w * 3 : (y + 1) * w * 3] for y in range(h))


def chunk(tag: bytes, body: bytes) -> bytes:
    c = struct.pack(">I", len(body)) + tag + body
    return c + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
open(out, "wb").write(png)
print(f"{out}: {w}x{h}")
