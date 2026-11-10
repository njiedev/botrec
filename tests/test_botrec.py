from pathlib import Path

import pytest

import botrec

DATA = Path(__file__).parent / 'data'
GAME_CODES = ['wasd_jump_dash.botrec.txt', 'calibration_keys.botrec.txt']


@pytest.mark.parametrize('name', GAME_CODES)
def test_game_codes_round_trip_byte_identical(name):
    code = (DATA / name).read_text().strip()
    payload, identical = botrec.verify(code)
    assert identical
    assert botrec.pack(payload) == code


def test_corrupted_code_fails_checksum():
    code = (DATA / 'wasd_jump_dash.botrec.txt').read_text().strip()
    i = len(code) // 2
    bad = code[:i] + ('A' if code[i] != 'A' else 'B') + code[i + 1:]
    with pytest.raises(Exception):
        botrec.unpack(bad)


def test_calibration_key_order():
    rows = botrec.timeline(botrec.unpack((DATA / 'calibration_keys.botrec.txt').read_text()))
    seen = []
    for r in rows:
        for name in botrec.pressed(r.get('1.3.1', 0)):
            if name not in seen:
                seen.append(name)
    # Recorded as W, S, A, D, jump, dash (with S held), crouch.
    assert seen == ['forward', 'back', 'left', 'right', 'jump', 'dash', 'crouch']


def test_axes_follow_keys():
    rows = botrec.timeline(botrec.unpack((DATA / 'calibration_keys.botrec.txt').read_text()))
    for r in rows:
        held = r.get('1.3.1', 0)
        if held == botrec.bits('forward') and r.get('1.5') == 1.0:
            break
    else:
        pytest.fail('no tick with forward held at full axis')
    assert max(r.get('1.6', 0) for r in rows) == 1.0   # A
    assert min(r.get('1.6', 0) for r in rows) == -1.0  # D


def test_build_produces_requested_inputs():
    template = botrec.unpack((DATA / 'calibration_keys.botrec.txt').read_text())
    start, *_ = botrec.split(template)
    frames = ([{}] * 4 + [{'buttons': botrec.bits('forward'), 'fwd': 1.0}] * 10
              + [{'yaw': start[4] - 90}] * 4)
    payload = botrec.build(start, frames, template)
    rows = botrec.timeline(botrec.unpack(botrec.pack(payload)))
    assert len(rows) == len(frames)
    assert botrec.pressed(rows[5].get('1.3.1', 0)) == ['forward']
    assert rows[5]['1.5'] == 1.0
    assert rows[-1].get('1.3.1', 0) == 0 and '1.5' not in rows[-1]
    assert rows[-1]['1.4.2'] == pytest.approx(start[4] - 90)


def test_strip_positions_removes_field_2():
    payload = botrec.unpack((DATA / 'wasd_jump_dash.botrec.txt').read_text())
    rows = botrec.timeline(botrec.strip_positions(payload))
    assert not any(k.startswith('2.') for r in rows for k in r)
