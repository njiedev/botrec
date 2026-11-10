# botrec

Read, write and edit Deadlock bot recording share codes (`BOTREC.…`).

In a sandbox or practice lobby, Deadlock can record your inputs onto a bot and replay them (`citadel_bot_record`, `citadel_bot_playrecording`). `citadel_bot_copyrecording` exports a recording as a share code, and `citadel_bot_pasterecording` loads one from the clipboard. This library decodes those codes into per-tick inputs and writes new ones the game plays back.

## Install

```
pip install -e .
```

Requires Python 3.10+ and `zstandard`.

## Command line

```
python -m botrec info  recording.botrec.txt         # summary and round-trip check
python -m botrec keys  recording.botrec.txt         # which keys were held, over time
python -m botrec dump  recording.botrec.txt out.csv # every field, every tick
```

Use `-` instead of a file to read the code from stdin.

## Library

```python
import botrec

payload = botrec.unpack(open('recording.botrec.txt').read())
for tick in botrec.timeline(payload):
    print(botrec.pressed(tick.get('1.3.1', 0)), tick.get('1.4.2'))  # keys held, yaw
```

Write a new recording, using any in-game recording as a template:

```python
template = botrec.unpack(open('mine.botrec.txt').read())
start, *_ = botrec.split(template)
frames = [{'buttons': botrec.bits('forward'), 'fwd': 1.0}] * 64      # 1 s of W
frames += [{'buttons': botrec.bits('jump')}] * 6                      # jump
print(botrec.pack(botrec.build(start, frames, template)))
```

See `examples/l_walk.py` for a full example: walk, turn 90°, walk, jump.

To play a code: copy it to the clipboard, then in the game console run `citadel_bot_pasterecording` and `citadel_bot_playrecording`. Load it on the map it was recorded on, because the bot is teleported to the recorded start position.

## What has been tested in-game

- Game recordings decode and re-encode **byte-identically** (tests use two real recordings).
- Edited recordings play correctly: a start rotated 90° runs the same moves in the new direction, and a recording with every logged camera position removed plays identically.
- A recording written entirely by `build()` plays correctly.
- Replays end in the same place every time, including at `host_timescale` 4 and 10 (checked by eye).
- Loading codes does **not** work on a `-dedicated` server: it reports "clipboard is empty".

Tested on game build 10931 (November 2026). Patches may change the format.

## Format

```
"BOTREC." + base64url('-' written as '.') of:
    0x01 | crc32(payload) LE | zstd(payload, level 19)

payload:
    u32 header length | f32 x, y, z, pitch, yaw, roll (start) | u32 count
    | varint len + first usercmd | (varint len + record) × (count - 1)
```

Each record is one tick: a `CCitadelUserCmdPB` protobuf (the same usercmd the game sends from client to server), **delta-encoded** against the previous tick. Only changed fields are present. A bare field key with **wire type 7** means the field was cleared back to 0. Wire type 7 isn't valid in standard protobuf, so off-the-shelf decoders reject these records.

Field names come from the game's protobuf schema (shipped with the Deadworks server mod); meanings were checked against recordings.

| Field | Schema name | Meaning |
|---|---|---|
| `1.2` | `base.client_tick` | Tick number |
| `1.3.1` | `base.buttons_pb.buttonstate1` | Buttons held (bitmask, below) |
| `1.3.2` | `base.buttons_pb.buttonstate2` | Buttons that changed this tick, on both press and release |
| `1.4.1`, `1.4.2` | `base.viewangles` | View pitch and yaw (turning right lowers yaw) |
| `1.5` | `base.forwardmove` | W = +1, S = −1 |
| `1.6` | `base.leftmove` | A = +1, D = −1 |
| `1.10` | `base.random_seed` | New every tick |
| `1.14` | `base.pawn_entity_handle` | Copied from the template |
| `1.17` | `base.prediction_offset_ticks_x256` | Copied from the template |
| `2.1`–`2.3` | `vec_camera_position` | Third-person camera position while recording, about 100 units above the hero. Ignored by playback |
| `10` | `enemy_hero_aimed_at` | |
| `13` | `is_in_third_person_view` | Copied from the template |

The axes are fractional on the press and release ticks (for example 0.75, then 1.0, then 0.25), which looks like sub-tick timing.

| Button | Bit |
|---|---|
| Crouch | 2 |
| Forward (W) | 3 |
| Back (S) | 4 |
| Left (A) | 9 |
| Right (D) | 10 |
| Dash | 44 |
| Jump | 48 and 56 (always together) |

The header length isn't always exact: one game recording stores 81 for a 79-byte header. `split()` finds records by parsing rather than trusting it, and `join(..., header_len=None)` writes the true value, which the game accepts.

## Caveats

- Unofficial and based on observation. A few fields (entity handle, prediction offset) are copied from a template recording rather than computed.
- For private lobbies and research only.
