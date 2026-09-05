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
import re
import struct
import sys
import tarfile
import time
import zipfile
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'archivist'))
import movieparse  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

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


def f_bk2_dosbox():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('Header.txt', 'Platform DOS\nCore DOSBox-X\nClockRate 1000\n'
                                 'CycleCount 10000\nrerecordCount 5\n')
        z.writestr('Input Log.txt', 'LogKey:#Reset|Power|\n' + '|..|........|\n' * 600)
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

def f_gmtas():
    payload = ((0).to_bytes(16, 'little') + struct.pack('<i', 99)
               + struct.pack('<Q', 0) + struct.pack('<Q', 5400) + b'\x00' * 16)
    lit = len(payload)
    tok = bytearray([15 << 4]); r = lit - 15
    while r >= 255: tok.append(255); r -= 255
    tok.append(r)
    return struct.pack('<IQ', 1, lit) + bytes(tok) + payload

def f_tas_ballance():
    raw = b''.join(struct.pack('<fI', 16.0, 0x21) for _ in range(30))
    return struct.pack('<I', len(raw)) + zlib.compress(raw)

def f_smv():
    head = (b'SMV\x1a' + struct.pack('<III', 1, 0, 777) + struct.pack('<I', 4321)
            + bytes([0x01, 0x03, 0, 0]) + struct.pack('<II', 0x20, 0x20))
    return head + b'\x00' * (4321 + 1) * 2

def f_zmv():
    h = bytearray(b'ZMV' + struct.pack('<H', 0x0151))
    h += struct.pack('<I', 0xDEADBEEF) + struct.pack('<II', 2500, 321)
    h += b'\x00' * (0x27 - len(h)) + bytes([0x40]) + b'\x00\x00\x00'
    return bytes(h)

def f_fcm():
    return (b'FCM\x1a' + struct.pack('<I', 2) + bytes([0x02, 0, 0, 0])
            + struct.pack('<IIIII', 6000, 88, 0, 0x38, 0x38)
            + b'\x00' * 16 + struct.pack('<I', 98) + b'rom\x00')

def f_fmv():
    head = (b'FMV\x1a' + bytes([0x00, 0x80]) + b'\x00' * 4
            + struct.pack('<I', 41) + b'\x00' * 2 + b'\x00' * 64 + b'\x00' * 64)
    assert len(head) == 0x90
    return head + b'\x00' * 900

def f_vmv():
    h = bytearray(b'VirtuaNES MV' + struct.pack('<HH', 0x0400, 0x0400))
    h += struct.pack('<I', 0x41) + struct.pack('<I', 0) + struct.pack('<HH', 0, 0)
    h += struct.pack('<I', 55) + bytes([0, 0, 0, 0]) + b'\x00' * 8
    h += struct.pack('<IIII', 0x40, 0x40, 0x40, 1800) + struct.pack('<I', 0)
    assert len(h) == 0x40
    return bytes(h) + b'\x00' * 1800

def f_nmv():
    payload = (bytes([1, 0, 0, 0x01]) + struct.pack('<I', 66) + struct.pack('<I', 0)
               + struct.pack('<I', 720) + b'\x00' * 720)
    body = b'NMOV' + struct.pack('<I', len(payload)) + payload
    return b'NSS\x1a' + b'0960' + struct.pack('<I', len(body)) + b'NMOV' + body

def f_mmv():
    h = (b'MMV\x00' + struct.pack('<IIII', 1, 3300, 44, 1)
         + struct.pack('<III', 0xF4, 0xF4, 2) + b'\x00' * 64
         + struct.pack('<I', 0) + b'\x00' * 128 + b'\x00' * 16)
    assert len(h) == 0xF4
    return h + b'\x00' * 3300 * 2

def f_mcm():
    h = bytearray(b'MDFNMOVI' + struct.pack('<II', 0, 0) + b'0' * 32 + b'\x00' * 64)
    h += struct.pack('<I', 12) + b'pce\x00\x00' + b'\x00' * 32
    h += b'\x00' * (0x100 - len(h))
    return bytes(h) + b'\x00' * 11 * 240


def f_pjm():
    return (b'PJM ' + struct.pack('<I', 2) + b'\x00' * 4 + struct.pack('<H', 0)
            + bytes([4, 4]) + struct.pack('<II', 12345, 678)
            + struct.pack('<IIIIII', 0, 0, 0, 0, 0x34, 0x35) + struct.pack('<I', 0)
            + b'\x00' + b'\x00' * 5 * 4)

def f_pxm():
    return (b'PXM ' + struct.pack('<I', 2) + b'\x00' * 4 + bytes([0, 0])
            + bytes([4, 4]) + struct.pack('<II', 999, 56)
            + struct.pack('<IIIIII', 0, 0, 0, 0, 0x34, 0x35) + struct.pack('<I', 0)
            + b'\x00' + b'\x00' * 5 * 4)

def f_mc2():
    return (b'version 1\nemuVersion 1\nrerecordCount 9\nports 1\nPCECD 0\n'
            + b'|0|........|\n' * 100)

def f_ymv():
    return (b'version 1\nemuVersion 1\nrerecordCount 4\nisPal 0\n'
            + b'|0|.............|\n' * 50)

def f_bkm():
    return (b'MovieVersion BizHawk v1.0.0\nPlatform SNES\nrerecordCount 13\n'
            b'StartsFromSavestate False\nPAL False\n'
            + b'|.|............|\n' * 77)

def f_dof():
    return (struct.pack('<IiiIII', 0x1A564F44, 3000, 20, 9000, 34, 1)
            + b'\x00' * 4296 + b'\x00' * 16 * 9000)

def f_rec():
    return (struct.pack('<iIii', 900, 0x83, 0, 0) + struct.pack('<I', 0)
            + b'QWQUICK1'.ljust(16, b'\x00') + b'\x00' * (900 * 27)
            + struct.pack('<i', 0) + struct.pack('<i', 0x00492F75))

def f_chimeraproject(idle=30, axes=False, last_input=None, rerecords=91):
    """A Chimera project: the JSON wrapper and a BizHawk-shaped input lump.

    Twelve frames of Down, twelve of Right, then `idle` frames of nothing:
    the run ends on frame 23, whatever the log's length.
    """
    key = '#P1 Up|P1 Down|P1 Left|P1 Right|P1 A|P1 B|P1 Select|P1 Start|'
    def row(mask, axis):
        return ('|' + (f'{axis:5d},{axis:5d},|' if axes else '') + mask + '|')
    lines = ([row('........', 128)]
             + [row('.D......', 128)] * 12
             + [row('...R....', 128)] * 11
             + [row('........', 128)] * idle)
    doc = {'title': 'gridWalker', 'core': {'name': 'Synth', 'version': '1',
                                           'sha1': 'a' * 40},
           'headers': {'MovieVersion': 'Chimera v1', 'Platform': 'NES'},
           'rerecords': rerecords,
           'input': '[Input]\nLogKey:' + key + '\n' + '\n'.join(lines) + '\n[/Input]'}
    if last_input is not None:
        doc['headers']['LastInputFrame'] = str(last_input)
    return json.dumps(doc).encode()

FIXTURES = {
    'chimeraproject': (f_chimeraproject(), 24, 'nes'),
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
    'gmtas': (f_gmtas(), 5400, 'pc'),
    'smv': (f_smv(), 4321, 'snes'),
    'zmv': (f_zmv(), 2500, 'snes'),
    'fcm': (f_fcm(), 6000, 'nes'),
    'fmv': (f_fmv(), 900, 'nes'),
    'vmv': (f_vmv(), 1800, 'nes'),
    'nmv': (f_nmv(), 720, 'nes'),
    'mmv': (f_mmv(), 3300, 'sms'),
    'mcm': (f_mcm(), 240, 'pce'),
    'pjm': (f_pjm(), 12345, 'psx'),
    'pxm': (f_pxm(), 999, 'psx'),
    'mc2': (f_mc2(), 100, 'pce'),
    'ymv': (f_ymv(), 50, 'saturn'),
    'bkm': (f_bkm(), 77, 'snes'),
    'dof': (f_dof(), 9000, 'dos'),
    'rec': (f_rec(), 900, 'pc'),
}
RERECORDS = {'chimeraproject': 91, 'bk2': 1234, 'fm2': 4321, 'fm3': 77, 'dsm': 99, 'gmv': 555,
             'vbm': 42, 'dtm': 12, 'm64': 8, 'mar': 7, 'p2m2': 3, 'ctm': 15,
             'wtf': 21, 'gzm': 17, 'lsmv': 888, 'ltm': 64, 'jrsr': 55,
             'tas': 250, 'ctas': 31, 'fbm': 19, 'omr': 66, 'mctas': 77, 'smv': 777, 'zmv': 321, 'fcm': 88,
             'fmv': 42, 'vmv': 55, 'nmv': 66, 'mmv': 44, 'mcm': 12,
             'pjm': 678, 'pxm': 56, 'mc2': 9, 'ymv': 4, 'bkm': 13, 'dof': 34}


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

    # DOSBox-X: milliseconds in CycleCount; frames are the input lines (#67)
    res = movieparse.parse('m.bk2', f_bk2_dosbox())
    ck('bk2/DOSBox-X: frames are the input lines, the rate follows the ms count',
       res.get('ok') and res['frames'] == 600 and abs(res['frames'] / res['fps'] - 10.0) < 1e-9,
       str(res))

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

    # --- Chimera projects: the run ends where the input does ---
    res = movieparse.parse('run.chimeraProject', f_chimeraproject(idle=300))
    ck('chimeraProject: idle frames after the last press are not run time',
       res.get('frames') == 24, str(res))
    ck('chimeraProject: the idle tail is said out loud',
       any('not counted as run time' in w for w in res.get('warnings', [])),
       str(res.get('warnings')))
    res = movieparse.parse('run.chimeraProject', f_chimeraproject(idle=0))
    ck('chimeraProject: a run that ends on its last frame counts them all',
       res.get('frames') == 24 and not any('not counted' in w for w in res['warnings']),
       str(res))
    res = movieparse.parse('run.chimeraProject', f_chimeraproject(axes=True))
    ck('chimeraProject: an axis resting at its usual value is not a press',
       res.get('frames') == 24, str(res))
    ck('chimeraProject: an inferred axis rest is admitted',
       any('analog axes' in w for w in res.get('warnings', [])), str(res.get('warnings')))
    res = movieparse.parse('run.chimeraProject', f_chimeraproject(last_input=40))
    ck('chimeraProject: a project that states the last input is believed',
       res.get('frames') == 41, str(res))
    legacy = json.loads(f_chimeraproject().decode())
    legacy['lastInputFrame'] = 40                 # the shape first proposed
    res = movieparse.parse('run.chimeraProject', json.dumps(legacy).encode())
    ck('chimeraProject: a top-level last input is read too',
       res.get('frames') == 41, str(res))
    stale = json.loads(f_chimeraproject().decode())
    stale['markers'] = [{'frame': 900, 'text': 'Last input'},
                        {'frame': 950, 'text': 'Run end'}]
    res = movieparse.parse('run.chimeraProject', json.dumps(stale).encode())
    ck('chimeraProject: a stale "Last input" marker is not believed',
       res.get('frames') == 24, str(res))
    # the real thing: two projects Chimera wrote and replayed itself
    chimera_dir = pathlib.Path(__file__).resolve().parent / 'fixtures' / 'chimera'
    for fixture_name, want in (('last-input-early.chimeraProject', 25),
                               ('input-to-the-end.chimeraProject', 70)):
        real = chimera_dir / fixture_name
        res = movieparse.parse(real.name, real.read_bytes())
        ck(f'chimeraProject: {fixture_name} reads as {want} frames',
           res.get('ok') and res.get('frames') == want, str(res)[:200])
        ck(f'chimeraProject: {fixture_name} says whether input idles at the end',
           any('not counted as run time' in w for w in res.get('warnings', []))
           == (want != 70), str(res.get('warnings')))

    presents = json.loads(f_chimeraproject(idle=0).decode())
    presents['core']['name'] = 'PCSX2'
    res = movieparse.parse('run.chimeraProject', json.dumps(presents).encode())
    ck('chimeraProject: a presenting core makes the time a lower bound, and says so',
       any('lower bound' in w for w in res.get('warnings', [])), str(res.get('warnings')))

    cyc = json.loads(f_chimeraproject(idle=0).decode())
    cyc['headers'].update({'CycleCount': '1000000', 'ClockRate': '1000000',
                           'VsyncNumerator': '60', 'VsyncDenominator': '1'})
    res = movieparse.parse('run.chimeraProject', json.dumps(cyc).encode())
    ck('chimeraProject: the cycle count over the clock rate beats every rate',
       res.get('fps') and abs(res['fps'] - 24.0) < 1e-9, str(res.get('fps')))
    pal = json.loads(f_chimeraproject().decode())
    pal['headers']['PAL'] = '1'
    res = movieparse.parse('run.chimeraProject', json.dumps(pal).encode())
    ck('chimeraProject: a PAL project admits the rate is the system\'s own',
       any('PAL' in w for w in res.get('warnings', [])), str(res.get('warnings')))
    vs = json.loads(f_chimeraproject().decode())
    vs['headers'].update({'VsyncNumerator': '60000', 'VsyncDenominator': '1001'})
    res = movieparse.parse('run.chimeraProject', json.dumps(vs).encode())
    ck('chimeraProject: a stated vsync is the rate',
       res.get('fps') and abs(res['fps'] - 59.94005994) < 1e-6, str(res.get('fps')))
    empty = json.loads(f_chimeraproject().decode())
    empty['input'] = '[Input]\nLogKey:#P1 A|\n' + '|.|\n' * 40 + '[/Input]'
    res = movieparse.parse('run.chimeraProject', json.dumps(empty).encode())
    ck('chimeraProject: a project with no input at all is its log, not one frame',
       res.get('frames') == 40
       and any('nothing is pressed' in w for w in res['warnings']), str(res))
    only_first = json.loads(f_chimeraproject().decode())
    only_first['input'] = '[Input]\nLogKey:#P1 A|\n|A|\n' + '|.|\n' * 39 + '[/Input]'
    res = movieparse.parse('run.chimeraProject', json.dumps(only_first).encode())
    ck('chimeraProject: a run whose only press is frame zero is one frame',
       res.get('frames') == 1, str(res))
    flat = json.loads(f_chimeraproject().decode())
    flat['headers'].update({'Platform': 'A26', 'VsyncNumerator': '60',
                            'VsyncDenominator': '1'})
    res = movieparse.parse('run.chimeraProject', json.dumps(flat).encode())
    ck('chimeraProject: a nominal 60 defers to the system rate the archive keeps',
       res.get('fps') is None and res.get('system') == 'a2600'
       and any('nominal' in w for w in res['warnings']), str(res))
    res = movieparse.parse('run.chimeraProject', f_chimeraproject(rerecords=None))
    ck('chimeraProject: a missing rerecord count is a warning, not a failure',
       res.get('ok') and res.get('rerecords') is None
       and 'missing rerecord count' in res.get('warnings', []), str(res))
    res = movieparse.parse('run.chimeraProject', json.dumps({'title': 'x'}).encode())
    ck('chimeraProject: no input log, no parse', not res.get('ok'), str(res))
    res = movieparse.parse('run.chimeraProject', b'{ this is not json')
    ck('chimeraProject: a broken file fails cleanly', not res.get('ok'), str(res))
    res = movieparse.parse('run.chimeraProject', FIXTURES['bk2'][0])
    ck('chimeraProject: a bk2 under the wrong extension is refused',
       not res.get('ok'), str(res))

    # --- the archive must accept every format intake does ---
    # An author may attach a second movie file, and the archivist takes any
    # format movieparse knows. The archive's own validator carries a copy of
    # that roster, and the two drifting is not a theory: a .chimeraProject
    # attachment archived cleanly and then failed the archive (M100053).
    # the checkout wherever it is: named on the command line, beside the
    # website in CI's workspace, or in the usual place on a working machine
    candidates = ([pathlib.Path(sys.argv[1])] if len(sys.argv) > 1 else []) + [
        pathlib.Path('archive'), REPO.parent / 'archive',
        pathlib.Path.home() / 'ToolAssisted-archive']
    validator = next((c / 'validate.py' for c in candidates
                      if (c / 'validate.py').exists()), pathlib.Path('nowhere'))
    if validator.exists():
        declared = set(re.findall(r"'(\.[a-z0-9]+)'",
                                  validator.read_text().split('MOVIE_ATTACH_EXT = {')[1]
                                  .split('}')[0]))
        ours = {'.' + e for e in set(movieparse.PARSERS) | set(movieparse.KNOWN_UNPARSED)}
        ck('the archive accepts every movie format intake does',
           not (ours - declared), str(sorted(ours - declared)[:6]))
        # The same drift, in the other roster: intake's text attachments
        # (settings.ATTACH_EXTS) widened once and two .wch watch files
        # archived cleanly and then failed the archive (M6986). Read as text
        # so this stays hermetic and needs none of settings' environment.
        vtext = validator.read_text()
        arch_txt = set(re.findall(r"'(\.[a-z0-9]+)'",
                                  vtext.split('ALLOWED_ATTACH_EXT = {')[1].split('}')[0]))
        stext = (REPO / 'archivist' / 'settings.py').read_text()
        ours_txt = set(re.findall(r"'(\.[a-z0-9]+)'",
                                  stext.split('ATTACH_EXTS = {')[1].split('}')[0]))
        ck('the archive accepts every text attachment intake does',
           ours_txt and not (ours_txt - arch_txt), str(sorted(ours_txt - arch_txt)[:6]))
    else:
        print('SKIP archive attachment roster: no archive checkout')

    print('---', len(failures), 'failures')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
