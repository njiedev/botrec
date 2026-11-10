"""Read, write and edit Deadlock bot recording share codes ("BOTREC.<...>").

Share codes come from `citadel_bot_copyrecording` and are loaded with
`citadel_bot_pasterecording`, which reads the Windows clipboard.

Container: "BOTREC." + base64url with '-' written as '.', decoding to
    0x01 (version) | crc32(payload) as 4 bytes little-endian | zstd(payload, level 19)

Payload:
    u32 header length | 6 x f32 start (x, y, z, pitch, yaw, roll) | u32 count
    | varint len + first usercmd (full) | records...

Each record is a varint length plus a protobuf usercmd, delta-encoded against the
previous tick: only changed fields are present, and a bare key with wiretype 7 means
"this field is now cleared". Wiretype 7 is not valid standard protobuf.

Records are CCitadelUserCmdPB messages (names from the game's protobuf schema):
    1      base (CBaseUserCmdPB)
    1.2    client_tick (+1 per record)
    1.3.1  buttons_pb.buttonstate1: held (bitmask, see BUTTONS)
    1.3.2  buttons_pb.buttonstate2: changed this tick (set on both press and release)
    1.4.1  viewangles pitch (f32)                1.4.2  viewangles yaw (f32, turning right lowers it)
    1.5    forwardmove (f32, W=+1, S=-1)         1.6    leftmove (f32, A=+1, D=-1)
           axes are fractional on the press and release ticks (sub-tick timing)
    1.10   random_seed
    1.14   pawn_entity_handle                    1.17   prediction_offset_ticks_x256
    2.1-3  vec_camera_position (the third-person camera, not the pawn), ignored by playback
    10     enemy_hero_aimed_at                   13     is_in_third_person_view
"""
import base64
import random
import struct
import zlib

import zstandard

__version__ = '0.1.0'

PREFIX = 'BOTREC.'
TICK_RATE = 64

# Bit index in field 1.3.1, from a calibration recording with each key pressed alone.
BUTTONS = {
    'crouch': 2,
    'forward': 3,
    'back': 4,
    'left': 9,
    'right': 10,
    'dash': 44,
    'jump': (48, 56),  # both bits are always set together
}

CLEARED = object()


# --- container -------------------------------------------------------------

def _b64d(s):
    s = s.replace('.', '-')
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().replace('-', '.')


def unpack(code):
    """Share code -> raw payload bytes."""
    code = code.strip()
    if not code.startswith(PREFIX):
        raise ValueError('not a BOTREC share code')
    raw = _b64d(code[len(PREFIX):])
    if raw[0] != 1:
        raise ValueError(f'unknown BOTREC version {raw[0]}')
    payload = zstandard.ZstdDecompressor().decompressobj().decompress(raw[5:])
    if zlib.crc32(payload) != int.from_bytes(raw[1:5], 'little'):
        raise ValueError('BOTREC checksum mismatch (was the code altered in transit?)')
    return payload


def pack(payload):
    """Raw payload bytes -> share code. Byte-identical to the game for unmodified payloads."""
    z = zstandard.ZstdCompressor(level=19).compress(payload)
    return PREFIX + _b64e(b'\x01' + zlib.crc32(payload).to_bytes(4, 'little') + z)


# --- protobuf wire format (with the wiretype-7 "cleared" extension) ---------

def read_varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7f) << s
        s += 7
        if c < 0x80:
            return r, i


def write_varint(n):
    o = bytearray()
    while True:
        c = n & 0x7f
        n >>= 7
        if n:
            o.append(c | 0x80)
        else:
            o.append(c)
            return bytes(o)


def parse(b):
    """Protobuf bytes -> list of [field, wiretype, value].

    wt0: int, wt2: bytes, wt1/wt5: raw bytes, wt7: None (field cleared).
    """
    i, out = 0, []
    while i < len(b):
        k, i = read_varint(b, i)
        f, wt = k >> 3, k & 7
        if wt == 0:
            v, i = read_varint(b, i)
        elif wt == 5:
            v = b[i:i + 4]
            i += 4
        elif wt == 1:
            v = b[i:i + 8]
            i += 8
        elif wt == 2:
            n, i = read_varint(b, i)
            v = b[i:i + n]
            i += n
        elif wt == 7:
            v = None
        else:
            raise ValueError(f'unsupported wiretype {wt}')
        out.append([f, wt, v])
    return out


def serialize(m):
    o = bytearray()
    for f, wt, v in m:
        o += write_varint(f << 3 | wt)
        if wt == 0:
            o += write_varint(v)
        elif wt == 2:
            v = serialize(v) if isinstance(v, list) else v
            o += write_varint(len(v)) + v
        elif wt != 7:
            o += v
    return bytes(o)


# --- payload layout ----------------------------------------------------------

def split(payload):
    """Payload -> (start[6], count, first_cmd_bytes, header_len, [record_bytes]).

    header_len is the leading u32. The game doesn't always write it exactly (one
    recording stores 81 for a 79-byte header), so records are located by parsing,
    not by trusting it. count = len(records) + 1.
    """
    header_len = struct.unpack('<I', payload[:4])[0]
    start = list(struct.unpack('<6f', payload[4:28]))
    count = struct.unpack('<I', payload[28:32])[0]
    n, i = read_varint(payload, 32)
    first = payload[i:i + n]
    i += n
    recs = []
    while i < len(payload):
        n, i = read_varint(payload, i)
        recs.append(payload[i:i + n])
        i += n
    return start, count, first, header_len, recs


def join(start, count, first, header_len, recs):
    """Inverse of split. Pass header_len=None to write the true length (for edited headers)."""
    hdr = struct.pack('<6f', *start) + struct.pack('<I', count) + write_varint(len(first)) + first
    if header_len is None:
        header_len = len(hdr)
    body = b''.join(write_varint(len(r)) + r for r in recs)
    return struct.pack('<I', header_len) + hdr + body


def verify(code):
    """Check decode -> parse -> re-serialize is lossless. Returns (payload, byte_identical)."""
    payload = unpack(code)
    parts = split(payload)
    if join(*parts) != payload:
        raise ValueError('container not lossless')
    if serialize(parse(parts[2])) != parts[2] or any(serialize(parse(r)) != r for r in parts[4]):
        raise ValueError('records not lossless')
    return payload, pack(payload) == code.strip()


# --- reading -------------------------------------------------------------------

def _flatten(msg, prefix=''):
    """Decode into {dotted_field: value}; nested messages that fail to parse stay as hex."""
    out = {}
    for f, wt, v in parse(msg):
        k = prefix + str(f)
        if wt == 2:
            try:
                out.update(_flatten(v, k + '.'))
            except (ValueError, IndexError):
                out[k] = v.hex()
        elif wt == 5:
            out[k] = struct.unpack('<f', v)[0]
        elif wt == 1:
            out[k] = struct.unpack('<d', v)[0]
        elif wt == 7:
            out[k] = CLEARED
        else:
            out[k] = v
    return out


def _apply(state, delta):
    for k, v in delta.items():
        if v is CLEARED:
            for sk in [sk for sk in state if sk == k or sk.startswith(k + '.')]:
                del state[sk]
        else:
            state[k] = v


def timeline(payload):
    """Per-tick state with deltas applied: one {dotted_field: value} dict per record.

    Cleared fields are absent, which means 0.
    """
    start, count, first, header_len, recs = split(payload)
    state = {}
    _apply(state, _flatten(first))
    rows = []
    for r in recs:
        _apply(state, _flatten(r))
        rows.append(dict(state))
    return rows


def pressed(buttons):
    """Bitmask -> sorted list of BUTTONS names that are set."""
    names = []
    for name, b in BUTTONS.items():
        bs = b if isinstance(b, tuple) else (b,)
        if all(buttons >> x & 1 for x in bs):
            names.append(name)
    return names


# --- editing -------------------------------------------------------------------

def edit_field(msg, path, fn):
    """Apply fn(wiretype, value) to the field at a nested path; fn returning None deletes it."""
    out = []
    for f, wt, v in parse(msg):
        if f == path[0]:
            if len(path) == 1:
                nv = fn(wt, v)
                if nv is not None:
                    out.append([f, wt, nv])
                continue
            if wt == 2:
                out.append([f, wt, edit_field(v, path[1:], fn)])
                continue
        out.append([f, wt, v])
    return serialize(out)


def rotate_start(payload, degrees):
    """Same inputs with the start yaw rotated. Playback simulates inputs, so the run rotates too."""
    start, count, first, header_len, recs = split(payload)
    start[4] += degrees
    rot = lambda wt, v: struct.pack('<f', struct.unpack('<f', v)[0] + degrees)
    return join(start, count, edit_field(first, [1, 4, 2], rot), header_len, recs)


def strip_positions(payload):
    """Remove the logged camera positions (field 2). Playback ignores them."""
    start, count, first, header_len, recs = split(payload)
    strip = lambda b: edit_field(b, [2], lambda wt, v: None)
    return join(start, count, strip(first), None, [strip(r) for r in recs])


# --- writing -------------------------------------------------------------------

def bits(*names):
    """Button bitmask from BUTTONS names, e.g. bits('forward', 'jump')."""
    m = 0
    for n in names:
        b = BUTTONS[n]
        for x in (b if isinstance(b, tuple) else (b,)):
            m |= 1 << x
    return m


def _f32(x):
    return struct.pack('<f', x)


def build(start, frames, template, first_tick=None, seed=0):
    """Write a recording from scratch.

    start:    (x, y, z, pitch, yaw, roll) the bot is teleported to before playback.
    frames:   one dict per tick: {'buttons': bitmask, 'fwd': float, 'side': float,
              'pitch': float, 'yaw': float}. Missing keys mean 0; angles default to start.
    template: payload of a game recording. Its first cmd supplies pawn_entity_handle (1.14),
              prediction_offset_ticks_x256 (1.17) and is_in_third_person_view (13), copied
              as the game wrote them.
    """
    rng = random.Random(seed)
    _, _, tfirst, _, _ = split(template)
    tbase = [v for f, wt, v in parse(tfirst) if f == 1][0]
    keep = [[f, wt, v] for f, wt, v in parse(tbase) if f in (14, 17)]
    top_keep = [[f, wt, v] for f, wt, v in parse(tfirst) if f == 13]
    tick = first_tick if first_tick is not None else [v for f, wt, v in parse(tbase) if f == 2][0]

    pitch0, yaw0 = start[3], start[4]
    angles = serialize([[1, 5, _f32(pitch0)], [2, 5, _f32(yaw0)]])
    first_base = [[2, 0, tick]] + keep[1:] + [[4, 2, angles], [10, 0, rng.getrandbits(31)]] + keep[:1]
    first = serialize([[1, 2, serialize(first_base)]] + top_keep)

    prev = {'held': 0, 'changed': 0, 'fwd': 0.0, 'side': 0.0, 'pitch': pitch0, 'yaw': yaw0}
    recs = []
    for i, fr in enumerate(frames):
        held = fr.get('buttons', 0)
        cur = {'held': held, 'changed': held ^ prev['held'], 'fwd': fr.get('fwd', 0.0),
               'side': fr.get('side', 0.0), 'pitch': fr.get('pitch', pitch0), 'yaw': fr.get('yaw', yaw0)}
        base = [[2, 0, tick + 1 + i]]
        if (cur['held'], cur['changed']) != (prev['held'], prev['changed']):
            if cur['held'] == 0 and cur['changed'] == 0:
                base.append([3, 7, None])
            else:
                sub = []
                for fnum, key in ((1, 'held'), (2, 'changed')):
                    if cur[key] != prev[key]:
                        sub.append([fnum, 0, cur[key]] if cur[key] else [fnum, 7, None])
                base.append([3, 2, serialize(sub)])
        ang = [[fnum, 5, _f32(cur[key])] for fnum, key in ((1, 'pitch'), (2, 'yaw')) if cur[key] != prev[key]]
        if ang:
            base.append([4, 2, serialize(ang)])
        for fnum, key in ((5, 'fwd'), (6, 'side')):
            if cur[key] != prev[key]:
                base.append([fnum, 5, _f32(cur[key])] if cur[key] else [fnum, 7, None])
        base.append([10, 0, rng.getrandbits(31)])
        recs.append(serialize([[1, 2, serialize(base)]]))
        prev = cur
    return join(list(start), len(recs) + 1, first, None, recs)
