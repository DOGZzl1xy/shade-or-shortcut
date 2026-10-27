"""Local preview server for the website with HTTP Range support (PMTiles reads byte ranges; Python's
http.server does not). GitHub Pages supports ranges, so this is only for local checks.

    uv run python -m scripts.serve_site [port] [directory]      # default 8770, site/_site
"""

import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

from coolpath import ROOT


class RangeHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        start = int(m.group(1)) if m.group(1) else size - int(m.group(2))
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        n = getattr(self, "_remaining", None)
        if n is None:
            return super().copyfile(source, outputfile)
        outputfile.write(source.read(n))
        self._remaining = None


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    d = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "site" / "_site")
    ThreadingHTTPServer(("127.0.0.1", port), partial(RangeHandler, directory=d)).serve_forever()


if __name__ == "__main__":
    main()
