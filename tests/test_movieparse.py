#!/usr/bin/env python3
"""Movie parser tests.

Frames are the archive's primary ranking datum and movieparse.py is a hand port
of TASVideos.Parsers, so a silent off-by-one or a crash on a malformed upload
matters more here than almost anywhere else. Three layers:

1. Fuzz: every format eats empty, tiny, random and truncated input. parse()
   must always return a dict and never raise (intake depends on that contract),
   and must never report a negative frame count.
2. Fixtures: byte-exact synthetic movies per format, asserting the parsed
   (frames, rerecords, start, system).
3. Confusion and bombs: a valid movie under the wrong extension must fail
   cleanly, and compressed formats must not expand unbounded input.

Hermetic: pure in-memory bytes, no network, no archive access.
"""
import io
import json
import math
import pathlib
import random
import struct
import sys
import tarfile
import time
import zipfile
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'archivist'))
import movieparse  # noqa: E402

failures = []


def ck(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  [{detail}]' if detail and not cond else ''))
    if not cond:
        failures.append(name)


# ------------------------------------------------------------------ fixtures
def f_bk2(fmt='bk2'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('Header.txt', 'Platform NES\nrerecordCount 1234\n')
        z.writestr('Input Log.txt', 'LogKey:#Reset|Power|\n' + '|..|........|\n' * 250)
    return buf.getvalue()


def f_fm2():
    return ('version 3\nemuVersion 20604\nrerecordCount 4321\nromFilename game\n'
            + '|0|........|........||\n' * 120).encode()


def f_fm3():
    return ('version 3\nromFilename game.nes\nromChecksum abc\nguid 0000-1111\n'
            'rerecordCount 77\n' + '|0|........|\n' * 55).encode()


def f_dsm():
    return ('version 1\nrerecordCount 99\nromFilename x\n'
            + '|0|.....|\n' * 33).encode()


def f_gmv():
    body = b'Gens Movie TEST' + b'\0'
    body += struct.pack('<i', 555)            # rerecords at 16
    body += b'\0' * 2                          # 20..21
    body += bytes([0])                         # flags at 22
    body += b'\0' * (64 - len(body))
    return body + b'\0' * (3 * 200)            # 200 frames of 3 bytes


def f_vbm():
    b = bytearray(b'VBM\x1a' + b'\0' * 60)
    struct.pack_into('<ii', b, 12, 1500, 42)   # frames, rerecords
    b[20] = 0                                   # power-on
    b[22] = 1                                   # gba
    return bytes(b)


def f_dtm():
    b = bytearray(b'DTM\x1a' + b'\0' * 300)
    b[10] = 0                                   # gamecube
    b[12] = 0                                   # power-on
    struct.pack_into('<q', b, 13, 999)          # VI count (fallback)
    struct.pack_into('<i', b, 45, 12)           # rerecords
    b[151] = 0
    b[152] = 0
    struct.pack_into('<q', b, 237, 486000000 * 2)   # 2 seconds of cycles
    return bytes(b)


def f_m64():
    b = bytearray(b'M64\x1a' + b'\0' * 60)
    struct.pack_into('<I', b, 12, 3600)
    struct.pack_into('<I', b, 16, 8)
    b[20] = 60
    b[28] = 2                                   # power-on
    return bytes(b)


def f_mar():
    b = bytearray(b'MAMETAS\x00' + b'\0' * 80)
    struct.pack_into('<d', b, 48, 60.0)
    struct.pack_into('<ii', b, 56, 4242, 7)
    return bytes(b)


def f_p2m2():
    pos = 1 + 5 + 2 + 43 + 255 + 255
    b = bytearray(b'\0' + b'PCSX2' + b'\0' * (pos - 6 + 16))
    struct.pack_into('<ii', b, pos, 2500, 3)
    b[pos + 8] = 0
    return bytes(b)


def f_ctm():
    pos = 4 + 8 + 20 + 8 + 8 + 32
    b = bytearray(b'CTM\x1b' + b'\0' * (pos + 32))
    struct.pack_into('<i', b, pos, 15)
    struct.pack_into('<Q', b, pos + 4, 234 * 100)   # 100 "frames" of inputs
    return bytes(b)


def f_wtf():
    b = bytearray(struct.pack('<i', 41374822) + b'\0' * 1020)
    struct.pack_into('<i', b, 8, 21)
    struct.pack_into('<I', b, 20, 61)               # fps-1 convention
    return bytes(b) + b'\0' * (8 * 300)             # 300 frames


def f_gzm():
    frame_count, seed = 3, 1
    b = struct.pack('>II', frame_count, seed) + b'\0' * 4
    b += b'\0' * (frame_count * 6) + b'\0' * (seed * 12)
    b += struct.pack('>III', 0, 0, 0)
    b += struct.pack('>II', 17, 1234)               # rerecords, frames
    return b


def f_lsmv():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('gametype', 'snes_ntsc\n')
        z.writestr('rerecords', '888\n')
        z.writestr('input', ''.join('F1 0 0 0\n' for _ in range(64)))
    return buf.getvalue()


def f_ltm():
    cfg = ('[General]\nframe_count=7200\nrerecord_count=64\n'
           'framerate_num=60\nframerate_den=1\n')
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        for name, text in (('config.ini', cfg), ('annotations.txt', 'Platform: linux\n')):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def f_jrsr():
    return ('JRSR\n!BEGIN header\n+RERECORDS 55\n!END\n'
            '!BEGIN events\n+0 KEYEDGE 1\n+1000000000 KEYEDGE 2\n!END\n').encode()


def f_tas():
    return ('FileTime: 00:01:00.000(3600)\nTotalRecordCount: 250\n').encode()


def f_ctas():
    b = bytearray(struct.pack('<I', 0x53415443) + b'\0' * 32)
    struct.pack_into('<III', b, 4, 4, 9000, 0)      # version 4, framecount
    struct.pack_into('<I', b, 16, 31)               # rerecords
    return bytes(b)


def f_3ct():
    return b'0 0 0\n1000 1 0\n2001 0 1\n'


def f_fbm():
    b = bytearray(b'FB1 ' + b'\0' + b'FR1 ' + b'\0' * 40)
    struct.pack_into('<ii', b, 13, 5400, 19)
    return bytes(b)


def f_omr():
    import gzip as _gz
    xml = ("""<openmsx><replay><reRecordCount>66</reRecordCount>"""
           """<snapshots><item><time>0</time></item></snapshots>"""
           """<scheduler><currentTime><time>0</time></currentTime></scheduler>"""
           """<palTiming>false</palTiming>"""
           """<events><item type="Input"><StateChange><time><time>3437563200</time>"""
           """</time></StateChange></item></events></replay></openmsx>""")
    return _gz.compress(xml.encode())


def f_dft():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        for name, text in (('main.txt', '#comment\n' + 'A\n' * 40),):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# name -> (bytes, expected frames or None, expected system or None)
def f_htas():
    return (b'name: sample\ntype: immediate\nlength: 6000\nfps: 60\n\n'
            b'000001: LT LY:32767 // sprint\n000031: ~LT A\n')

def f_hltas():
    return (b'version 1\ndemo bhop\nframetime0ms 0.0000001\nhlstrafe_version 1\nframes\n'
            b'----------|------|------|0.001|-|-|1|sensitivity 0\n'
            b's03-------|------|------|0.001|170|0|400\n'
            b's03l-D----|------|------|0.001|90|-|5315\n')

def f_p2tas():
    return (b'version 7\nstart map testchamber_01\n\n'
            b'120>0 1 // start moving\n+10>||J1\nrepeat 2\n+5>||J1\nend\n+35>||O1\n')

def f_srctas():
    return (b'save start\nsettings y_spt_autojump 1\n\nframes\n'
            b'<<<<<<<<<<|<<<<<<|<<<<<<<<|-|-|-1|-attack\n'
            b'<<<<<<<<<<|<<<<<<|<<<<<<<<|-|-|0|+duck\n'
            b'<<<<<<<<<<|<<<<<<|<<<<<<<<|-|-|26|\n'
            b'<<<<<<<<<<|<<<<<<|<<<<<<<<|-|-|100|walk\n')

def f_qtas():
    return (b'+1:\n\ttas_strafe_version 2\n\tcl_maxfps 72\n'
            b'+7:\n\ttas_strafe_yaw 108.2\n+64:\n\ttas_strafe 1\n')

def f_mctas():
    head = (b'##################### TASfile ####################\n'
            b'Flavor: beta1\n\nTitle:Test\nAuthor:Ada\nRerecords:77\n'
            b'##################################################\n')
    return head + b''.join(b'%d|W;w|;0,0,0|0.0;0.0\n\t1|W;|;|0.0;0.0\n' % i for i in range(40))

def f_replay():
    # v2, frame-typed (the writer streams the type as ASCII), 240 fps
    return (b'RPLY' + bytes([2, 0x31]) + struct.pack('<f', 240.0)
            + struct.pack('<IB', 3000, 1) + struct.pack('<IB', 14400, 0))

def f_inputs():
    return (b'# a TrackMania input script\n0 press up\n2.00 press left\n'
            b'3.00 rel left\n1:23.45 press down; 1:24.00 rel down\n84500 steer 13292\n')

def f_itf():
    return (b'// Generated\n  11\n   1,escape\n   7\n   1,enter\nSave: sector\n  40,R,U\nEnd\n  999\n')

def f_otts():
    return json.dumps({'entries': [
        {'type': 'action', 'frame': 100, 'jump': True},
        {'type': 'comment', 'comment': 'mid'},
        {'type': 'action', 'frame': 4500}], 'boss_frame': 9999999999}).encode()

def f_tas_ballance():
    raw = b''.join(struct.pack('<fI', 16.0, 0x21) for _ in range(30))
    return struct.pack('<I', len(raw)) + zlib.compress(raw)

FIXTURES = {
    'bk2': (f_bk2(), 250, 'nes'),
    'tasproj': (f_bk2('tasproj'), 250, 'nes'),
    'gbmv': (f_bk2('gbmv'), 250, 'nes'),
    'fm2': (f_fm2(), 120, 'nes'),
    'fm3': (f_fm3(), 55, 'nes'),
    'dsm': (f_dsm(), 33, 'ds'),
    'gmv': (f_gmv(), 200, 'genesis'),
    'vbm': (f_vbm(), 1500, 'gba'),
    'dtm': (f_dtm(), 120, 'gc'),
    'm64': (f_m64(), 3600, 'n64'),
    'mar': (f_mar(), 4242, 'arcade'),
    'p2m2': (f_p2m2(), 2500, 'ps2'),
    'ctm': (f_ctm(), None, '3ds'),
    'wtf': (f_wtf(), 300, 'pc'),
    'gzm': (f_gzm(), 1234, 'n64'),
    'lsmv': (f_lsmv(), 64, 'snes'),
    'ltm': (f_ltm(), 7200, 'linux'),
    'jrsr': (f_jrsr(), 60, 'dos'),
    'tas': (f_tas(), 3600, 'pc'),
    'ctas': (f_ctas(), 9000, 'pc'),
    '3ct': (f_3ct(), 2000, 'nes'),
    'dft': (f_dft(), 40, 'pc'),
    'fbm': (f_fbm(), 5400, 'arcade'),
    'omr': (f_omr(), None, 'msx'),
    'htas': (f_htas(), 6000, 'pc'),
    'hltas': (f_hltas(), 5716, 'pc'),
    'p2tas': (f_p2tas(), 175, 'pc'),
    'srctas': (f_srctas(), 126, 'pc'),
    'qtas': (f_qtas(), 72, 'pc'),
    'mctas': (f_mctas(), 40, 'pc'),
    'replay': (f_replay(), 14400, 'pc'),
    'inputs': (f_inputs(), 8450, 'pc'),
    'itf': (f_itf(), 61, 'pc'),
    'otts': (f_otts(), 4500, 'pc'),
}
RERECORDS = {'bk2': 1234, 'fm2': 4321, 'fm3': 77, 'dsm': 99, 'gmv': 555,
             'vbm': 42, 'dtm': 12, 'm64': 8, 'mar': 7, 'p2m2': 3, 'ctm': 15,
             'wtf': 21, 'gzm': 17, 'lsmv': 888, 'ltm': 64, 'jrsr': 55,
             'tas': 250, 'ctas': 31, 'fbm': 19, 'omr': 66, 'mctas': 77}


def main():
    rnd = random.Random(20260815)

    # ---------------- 0. a JRSR saved from a running machine (#31) ----------------
    # JPC-RR embeds the machine state it was saved from and names it in the
    # header; the events still replay from the initialization, so the file
    # is accepted and reads exactly like the same movie with the state removed.
    plain = f_jrsr()
    with_state = plain.replace(
        b'!BEGIN header\n', b'!BEGIN header\n+SAVESTATEID abc123\n').replace(
        b'!BEGIN events', b'!BEGIN savestate\n+SOMEBLOB 0123456789\n+MORE 1\n!END\n!BEGIN events')
    a, b = movieparse.parse('x.jrsr', with_state), movieparse.parse('x.jrsr', plain)
    ck('a jrsr carrying a savestate is accepted and reads like the stripped one',
       a.get('ok') and a == b and a['start'] == 'power-on', f'{a} vs {b}')

    # ---------------- 1. the never-raises contract ----------------
    raised, negative, malformed = [], [], []
    for ext in movieparse.PARSERS:
        samples = [b'', b'\x00', b'\xff' * 7, b'PK\x03\x04', bytes(rnd.randrange(256) for _ in range(1024))]
        good = FIXTURES.get(ext, (b'',))[0]
        if good:
            samples += [good[:len(good) * i // 8] for i in range(1, 8)]
            samples.append(good + b'\xff' * 64)
        for i, data in enumerate(samples):
            try:
                res = movieparse.parse(f'x.{ext}', data)
            except Exception as e:                      # noqa: BLE001
                raised.append(f'{ext}#{i}: {e.__class__.__name__}: {e}')
                continue
            if not isinstance(res, dict) or 'ok' not in res:
                malformed.append(f'{ext}#{i}: {res!r}')
                continue
            if res.get('ok') and (res.get('frames') or 0) < 0:
                negative.append(f'{ext}#{i}: frames={res.get("frames")}')
    ck('parse() never raises on hostile input', not raised, str(raised[:4]))
    ck('parse() always returns a result dict', not malformed, str(malformed[:3]))
    ck('parse() never reports negative frames', not negative, str(negative[:4]))

    # ---------------- 2. per-format fixtures ----------------
    for ext, (data, frames, system) in sorted(FIXTURES.items()):
        res = movieparse.parse(f'movie.{ext}', data)
        ok = res.get('ok')
        ck(f'{ext}: fixture parses', ok, str(res.get('error')))
        if not ok:
            continue
        if frames is not None:
            ck(f'{ext}: frame count', res.get('frames') == frames,
               f'got {res.get("frames")} want {frames}')
        if system is not None:
            ck(f'{ext}: system', res.get('system') == system,
               f'got {res.get("system")} want {system}')
        if ext in RERECORDS:
            ck(f'{ext}: rerecords', res.get('rerecords') == RERECORDS[ext],
               f'got {res.get("rerecords")} want {RERECORDS[ext]}')
        ck(f'{ext}: start type is known',
           res.get('start') in ('power-on', 'savestate', 'sram'), str(res.get('start')))

    # lmp is a heuristic cascade over eight historical Doom variants with no
    # magic number; the fuzz layer covers it and a fixture would only pin one
    # guess of the cascade.
    ck('every parser except lmp has a fixture',
       set(FIXTURES) | {'lmp'} >= set(movieparse.PARSERS),
       str(sorted(set(movieparse.PARSERS) - set(FIXTURES) - {'lmp'})))

    # ---------------- 2b. the .tas family: four formats, one extension ----------------
    res = movieparse.parse('m.tas', f_tas_ballance())
    ck('tas: a Ballance TASSupport record parses (frames from the size field)',
       res.get('ok') and res['frames'] == 30 and abs(res['frames'] / res['fps'] - 0.48) < 0.01, str(res))
    res = movieparse.parse('m.tas', b'[1,2]1,1,33,0,17,36,')
    ck('tas: a PICO-8 Celeste Classic line parses at 30 fps',
       res.get('ok') and res['frames'] == 6 and res['fps'] == 30.0, str(res))
    res = movieparse.parse('m.tas', b'#Start\n   1,J\n 545\n@23100,30200,1\n  35,L,J\n***\n')
    ck('tas: a ShootMe-family file sums its frame lines (@ costs one, *** none)',
       res.get('ok') and res['frames'] == 582 and res['fps'] == 60.0 and res['warnings'], str(res))

    # ---------------- 3. confusion and bombs ----------------
    res = movieparse.parse('movie.bk2', FIXTURES['lsmv'][0])
    ck('lsmv under a .bk2 name fails cleanly', not res.get('ok'), str(res))
    res = movieparse.parse('movie.exe', FIXTURES['bk2'][0])
    ck('unknown extension is refused', not res.get('ok') and 'no parser' in res.get('error', ''))
    res = movieparse.parse('MOVIE.BK2', FIXTURES['bk2'][0])
    ck('extension matching is case-insensitive', res.get('ok'), str(res.get('error')))
    res = movieparse.parse('noextension', FIXTURES['bk2'][0])
    ck('a name without an extension is refused', not res.get('ok'))

    # zip bomb: one entry that expands ~100x, and a tar of the same
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('input', b'\0' * (64 * 1024 * 1024))
    bomb_bytes = bomb.getvalue()
    t0 = time.time()
    res = movieparse.parse('movie.lsmv', bomb_bytes)
    ck('compressed bomb does not hang the parser', time.time() - t0 < 20,
       f'{time.time() - t0:.1f}s')
    ck('compressed bomb yields a result', isinstance(res, dict) and 'ok' in res)

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
