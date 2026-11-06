"""Record a command's terminal output, chunk by chunk, for the sign-in tests.

Runs the command on a PTY of the given size, writes every chunk it reads as one
JSON line {"t": seconds, "data": text}, and forwards stdin to the command, so
a person (or a FIFO) can drive it. Usage:

    python record.py --cols 120 --out raw.jsonl -- claude setup-token
"""

import argparse
import fcntl
import json
import os
import pty
import select
import struct
import sys
import termios
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=120)
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--out", required=True)
    ap.add_argument("cmd", nargs="+")
    args = ap.parse_args()

    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(args.cmd[0], args.cmd)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", args.rows, args.cols, 0, 0))

    start = time.monotonic()
    stdin = sys.stdin.fileno()
    watch = [fd, stdin]
    with open(args.out, "w") as out:
        while True:
            ready, _, _ = select.select(watch, [], [])
            if stdin in ready:
                data = os.read(stdin, 4096)
                if data:
                    os.write(fd, data)
                else:
                    watch.remove(stdin)
            if fd in ready:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", "replace")
                out.write(json.dumps({"t": round(time.monotonic() - start, 3), "data": text}) + "\n")
                out.flush()
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


if __name__ == "__main__":
    sys.exit(main())
