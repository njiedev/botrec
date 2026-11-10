"""Write a recording from scratch: walk forward, turn 90 degrees right, walk, jump.

Usage: python examples/l_walk.py <template.botrec.txt> [out.botrec.txt]

The template is any recording made in-game. It supplies the start position and a few
fields whose meaning is unknown. Load the output in a lobby on the same map with
citadel_bot_pasterecording, then citadel_bot_playrecording.
"""
import sys

import botrec

template = botrec.unpack(open(sys.argv[1]).read())
start, *_ = botrec.split(template)
yaw = start[4]
T = botrec.TICK_RATE
fwd = {'buttons': botrec.bits('forward'), 'fwd': 1.0}

frames = []
frames += [{} for _ in range(T // 2)]                                     # 0.5s idle
frames += [dict(fwd) for _ in range(T)]                                   # 1s forward
frames += [{'yaw': yaw - 90} for _ in range(T // 4)]                      # turn right
frames += [dict(fwd, yaw=yaw - 90) for _ in range(T)]                     # 1s forward
frames += [{'buttons': botrec.bits('jump'), 'yaw': yaw - 90} for _ in range(6)]
frames += [{'yaw': yaw - 90} for _ in range(T)]                           # 1s idle

code = botrec.pack(botrec.build(start, frames, template))
if len(sys.argv) > 2:
    open(sys.argv[2], 'w').write(code)
else:
    print(code)
