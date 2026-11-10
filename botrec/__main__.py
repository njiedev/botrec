"""Command-line tool.

    python -m botrec info  <file>            summary and losslessness check
    python -m botrec dump  <file> [out.csv]  per-tick CSV
    python -m botrec keys  <file>            button and axis changes over time

<file> holds a share code; use '-' to read it from stdin.
"""
import argparse
import csv
import sys

from . import TICK_RATE, pressed, split, timeline, verify


def _read(path):
    return sys.stdin.read() if path == '-' else open(path, encoding='ascii').read()


def cmd_info(a):
    payload, identical = verify(_read(a.file))
    start, count, first, header_len, recs = split(payload)
    print(f'records:   {len(recs)} (~{len(recs) / TICK_RATE:.2f}s at {TICK_RATE} tick)')
    print(f'start:     x={start[0]:.2f} y={start[1]:.2f} z={start[2]:.2f} '
          f'pitch={start[3]:.2f} yaw={start[4]:.2f}')
    print(f'lossless:  yes (re-encodes byte-identical: {"yes" if identical else "no"})')


def cmd_dump(a):
    rows = timeline(verify(_read(a.file))[0])
    keys = sorted({k for r in rows for k in r}, key=lambda k: [int(p) for p in k.split('.')])
    out = open(a.out, 'w', newline='') if a.out else sys.stdout
    w = csv.DictWriter(out, fieldnames=keys)
    w.writeheader()
    w.writerows(rows)


def cmd_keys(a):
    rows = timeline(verify(_read(a.file))[0])
    t0 = rows[0]['1.2']
    prev = None
    for r in rows:
        cur = (r.get('1.3.1', 0), r.get('1.5', 0.0), r.get('1.6', 0.0))
        if cur != prev:
            print(f"{(r['1.2'] - t0) / TICK_RATE:7.2f}s  held={','.join(pressed(cur[0])) or '-':<20} "
                  f'fwd={cur[1]:+.2f} side={cur[2]:+.2f}')
        prev = cur


def main(argv=None):
    ap = argparse.ArgumentParser(prog='botrec', description='Deadlock bot recording share codes')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('info')
    p.add_argument('file')
    p.set_defaults(fn=cmd_info)
    p = sub.add_parser('dump')
    p.add_argument('file')
    p.add_argument('out', nargs='?')
    p.set_defaults(fn=cmd_dump)
    p = sub.add_parser('keys')
    p.add_argument('file')
    p.set_defaults(fn=cmd_keys)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == '__main__':
    main()
