#!/usr/bin/env python3
"""Tests for Blurhash generation, caching, and client-side integration.

Usage: python tests/test_blurhash.py
"""
import io
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "generator"))
import fast_blurhash
from PIL import Image

failures = []

def ck(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""), flush=True)
    if not cond:
        failures.append(name)


def test_encoder_solid():
    img = Image.new("RGB", (120, 90), color=(255, 0, 0))
    bh = fast_blurhash.encode_blurhash_from_image(img, 4, 3)
    ck("solid red generates valid 28-character blurhash", len(bh) == 28 and isinstance(bh, str))
    ck("solid red hash matches expected base83 structure", bh.startswith("L"))


def test_encoder_gradient():
    img = Image.new("RGB", (320, 180))
    for y in range(180):
        for x in range(320):
            img.putpixel((x, y), (int(x * 255 / 320), int(y * 255 / 180), 100))
    bh = fast_blurhash.encode_blurhash_from_image(img, 4, 3)
    ck("gradient generates valid 28-character blurhash", len(bh) == 28)


def test_performance():
    img = Image.new("RGB", (640, 360), color=(40, 80, 160))
    # Warmup
    fast_blurhash.encode_blurhash_from_image(img, 4, 3)

    t0 = time.perf_counter()
    n = 20
    for _ in range(n):
        fast_blurhash.encode_blurhash_from_image(img, 4, 3)
    elapsed_per_img = (time.perf_counter() - t0) / n
    ck(f"fast blurhash encoding is under 5ms per image ({elapsed_per_img * 1000:.2f} ms)", elapsed_per_img < 0.005)


def test_file_cache():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "test.png"
        img = Image.new("RGB", (100, 100), color=(0, 255, 0))
        img.save(p)

        bh1 = fast_blurhash.encode_blurhash_file(p)
        ck("file encoding returns blurhash", bool(bh1))

        t0 = time.perf_counter()
        bh2 = fast_blurhash.encode_blurhash_file(p)
        t_cache = time.perf_counter() - t0
        ck("cached lookup is identical and sub-millisecond", bh1 == bh2 and t_cache < 0.001)


def test_corrupt_or_mock_images():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "mock.png"
        # Synthetic PNG mock bytes as used in test fixtures
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 50)
        bh = fast_blurhash.encode_blurhash_file(p)
        ck("synthetic mock image gracefully returns None without crashing", bh is None)

        p_missing = pathlib.Path(td) / "nonexistent.png"
        bh_missing = fast_blurhash.encode_blurhash_file(p_missing)
        ck("missing file returns None without crashing", bh_missing is None)


def test_blurhash_to_data_url():
    bh = "LEHV6nWB2yk8pyo0adR*.7kCMdnj"
    durl = fast_blurhash.blurhash_to_data_url(bh)
    ck("valid blurhash decodes to data URL", bool(durl and durl.startswith("data:image/")))
    ck("data URL is ultra-compact (< 200 chars)", len(durl) < 200)

    # Repeat should hit cache and be identical
    t0 = time.perf_counter()
    durl_cached = fast_blurhash.blurhash_to_data_url(bh)
    t_cached = time.perf_counter() - t0
    ck("cached data URL is identical and instant", durl == durl_cached and t_cached < 0.001)

    # Corrupt or empty blurhash gracefully returns None
    ck("empty blurhash returns None", fast_blurhash.blurhash_to_data_url("") is None)
    ck("invalid blurhash returns None", fast_blurhash.blurhash_to_data_url("short") is None)


def main():
    test_encoder_solid()
    test_encoder_gradient()
    test_performance()
    test_file_cache()
    test_corrupt_or_mock_images()
    test_blurhash_to_data_url()

    print("---", len(failures), "failures")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
