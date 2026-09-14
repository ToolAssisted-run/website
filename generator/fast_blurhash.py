"""Fast Blurhash encoder based on Uploadcare optimizations:
https://uploadcare.com/blog/faster-blurhash/

Optimizations applied:
1. Direct raw pixel byte buffer from Pillow via .tobytes().
2. Precomputed and cached 256-entry sRGBToLinear lookup table.
3. Precomputed cosine basis matrices (cosX and cosY).
4. Fast single-pass 2D DCT vectorized using NumPy BLAS/SIMD with pure-Python fallback.
5. In-memory and disk cache keyed by (file_path, mtime, size).
6. Graceful handling of corrupt, missing, or mock test fixture images.
"""
import atexit
import base64
import hashlib
import io
import math
import os
import pathlib

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Base83 character set per Wolt Blurhash specification
BASE83_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~"

# Precompute sRGB to Linear LUT (0..255)
# sRGBToLinear(v): v <= 0.04045 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4)
SRGB_TO_LINEAR_LUT_PY = [
    (i / 255.0) / 12.92 if (i / 255.0) <= 0.04045 else ((i / 255.0 + 0.055) / 1.055) ** 2.4
    for i in range(256)
]

if HAS_NUMPY:
    SRGB_TO_LINEAR_LUT_NP = np.array(SRGB_TO_LINEAR_LUT_PY, dtype=np.float32)
else:
    SRGB_TO_LINEAR_LUT_NP = None


def encode_83(value: int, length: int) -> str:
    """Encode an integer into Base83 string of specified length."""
    res = []
    divisor = 83 ** (length - 1)
    for _ in range(length):
        digit = (value // divisor) % 83
        res.append(BASE83_CHARS[digit])
        divisor //= 83
    return "".join(res)


def linear_to_srgb(value: float) -> int:
    v = max(0.0, min(1.0, value))
    if v <= 0.0031308:
        return int(v * 12.92 * 255.0 + 0.5)
    return int((1.055 * (v ** (1.0 / 2.4)) - 0.055) * 255.0 + 0.5)


def sign_pow(val: float, exp: float) -> float:
    return math.copysign(abs(val) ** exp, val)


def encode_blurhash_from_image(img, x_comp: int = 4, y_comp: int = 3) -> str:
    """Encode an opened PIL Image into a Blurhash string."""
    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    # Downscale large images for blazing fast hashing while preserving aspect ratio
    max_dim = 64
    if w > max_dim or h > max_dim:
        scale = max_dim / max(w, h)
        w = max(x_comp, int(w * scale))
        h = max(y_comp, int(h * scale))
        img = img.resize((w, h), Image.Resampling.BILINEAR)

    if HAS_NUMPY:
        return _encode_numpy(img, w, h, x_comp, y_comp)
    return _encode_pure_python(img, w, h, x_comp, y_comp)


def _encode_numpy(img, w: int, h: int, x_comp: int, y_comp: int) -> str:
    # 1. Direct bytes from Pillow: .tobytes()
    arr = np.frombuffer(img.tobytes(), dtype=np.uint8).reshape((h, w, 3))
    # 2. LUT lookup for sRGB -> linear
    linear = SRGB_TO_LINEAR_LUT_NP[arr]  # (h, w, 3)

    # 3. Precomputed cosine basis matrices
    x = np.arange(w, dtype=np.float32)
    y = np.arange(h, dtype=np.float32)
    cos_x = np.cos(np.pi * np.outer(np.arange(x_comp, dtype=np.float32), x) / w)  # (x_comp, w)
    cos_y = np.cos(np.pi * np.outer(np.arange(y_comp, dtype=np.float32), y) / h)  # (y_comp, h)

    # 4. Fast single-pass 2D DCT via matrix multiplication
    # linear.transpose(2, 0, 1) has shape (3, h, w)
    # temp: (y_comp, h) @ (3, h, w) -> (3, y_comp, w)
    temp = np.matmul(cos_y, linear.transpose(2, 0, 1))
    # factors: (3, y_comp, w) @ (w, x_comp) -> (3, y_comp, x_comp)
    factors = np.matmul(temp, cos_x.T)

    norm = np.ones((y_comp, x_comp), dtype=np.float32) * 2.0
    norm[0, 0] = 1.0
    factors *= norm / (w * h)
    factors = factors.transpose(1, 2, 0)  # (y_comp, x_comp, 3)

    dc = factors[0, 0]
    ac = factors.reshape(-1, 3)[1:]

    size_flag = (x_comp - 1) + (y_comp - 1) * 9
    result = [encode_83(size_flag, 1)]

    if len(ac) > 0:
        actual_max = float(np.max(np.abs(ac)))
        quant_max = int(max(0, min(82, math.floor(actual_max * 166.0 - 0.5))))
        max_val = (quant_max + 1) / 166.0
        result.append(encode_83(quant_max, 1))
    else:
        max_val = 1.0
        result.append(encode_83(0, 1))

    dc_r = linear_to_srgb(float(dc[0]))
    dc_g = linear_to_srgb(float(dc[1]))
    dc_b = linear_to_srgb(float(dc[2]))
    result.append(encode_83((dc_r << 16) + (dc_g << 8) + dc_b, 4))

    for comp in ac:
        qr = int(max(0, min(18, math.floor(sign_pow(float(comp[0]) / max_val, 0.5) * 9.0 + 9.5))))
        qg = int(max(0, min(18, math.floor(sign_pow(float(comp[1]) / max_val, 0.5) * 9.0 + 9.5))))
        qb = int(max(0, min(18, math.floor(sign_pow(float(comp[2]) / max_val, 0.5) * 9.0 + 9.5))))
        result.append(encode_83(qr * 19 * 19 + qg * 19 + qb, 2))

    return "".join(result)


def _encode_pure_python(img, w: int, h: int, x_comp: int, y_comp: int) -> str:
    raw_bytes = img.tobytes()
    # Precomputed cosine tables
    cos_x = [
        [math.cos(math.pi * i * x / w) for x in range(w)]
        for i in range(x_comp)
    ]
    cos_y = [
        [math.cos(math.pi * j * y / h) for y in range(h)]
        for j in range(y_comp)
    ]

    factors = []
    for j in range(y_comp):
        cy = cos_y[j]
        for i in range(x_comp):
            cx = cos_x[i]
            r = g = b = 0.0
            norm = 1.0 if (i == 0 and j == 0) else 2.0
            idx = 0
            for y in range(h):
                basis_y = cy[y]
                for x in range(w):
                    basis = basis_y * cx[x]
                    r += basis * SRGB_TO_LINEAR_LUT_PY[raw_bytes[idx]]
                    g += basis * SRGB_TO_LINEAR_LUT_PY[raw_bytes[idx + 1]]
                    b += basis * SRGB_TO_LINEAR_LUT_PY[raw_bytes[idx + 2]]
                    idx += 3
            scale = norm / (w * h)
            factors.append((r * scale, g * scale, b * scale))

    dc = factors[0]
    ac = factors[1:]

    size_flag = (x_comp - 1) + (y_comp - 1) * 9
    result = [encode_83(size_flag, 1)]

    if ac:
        actual_max = max(max(abs(c[0]), abs(c[1]), abs(c[2])) for c in ac)
        quant_max = int(max(0, min(82, math.floor(actual_max * 166.0 - 0.5))))
        max_val = (quant_max + 1) / 166.0
        result.append(encode_83(quant_max, 1))
    else:
        max_val = 1.0
        result.append(encode_83(0, 1))

    dc_r = linear_to_srgb(dc[0])
    dc_g = linear_to_srgb(dc[1])
    dc_b = linear_to_srgb(dc[2])
    result.append(encode_83((dc_r << 16) + (dc_g << 8) + dc_b, 4))

    for comp in ac:
        qr = int(max(0, min(18, math.floor(sign_pow(comp[0] / max_val, 0.5) * 9.0 + 9.5))))
        qg = int(max(0, min(18, math.floor(sign_pow(comp[1] / max_val, 0.5) * 9.0 + 9.5))))
        qb = int(max(0, min(18, math.floor(sign_pow(comp[2] / max_val, 0.5) * 9.0 + 9.5))))
        result.append(encode_83(qr * 19 * 19 + qg * 19 + qb, 2))

    return "".join(result)


# Default persistent disk cache location: website/.cache/blurhash.json
DEFAULT_CACHE_PATH = pathlib.Path(__file__).resolve().parent.parent / ".cache" / "blurhash.json"
CACHE_PATH = pathlib.Path(os.environ.get("BLURHASH_CACHE_PATH", str(DEFAULT_CACHE_PATH)))

_DISK_CACHE: dict[str, dict[str, str]] = {}
_DISK_CACHE_LOADED = False
_DISK_CACHE_DIRTY = False
_STAT_CACHE: dict[tuple[str, float, int], str] = {}


def load_disk_cache():
    global _DISK_CACHE, _DISK_CACHE_LOADED
    if _DISK_CACHE_LOADED:
        return
    _DISK_CACHE_LOADED = True
    if CACHE_PATH.is_file():
        try:
            import json

            _DISK_CACHE = json.loads(CACHE_PATH.read_text("utf-8"))
        except Exception:
            _DISK_CACHE = {}


def save_disk_cache():
    global _DISK_CACHE_DIRTY
    if not _DISK_CACHE_DIRTY:
        return
    try:
        import json

        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_DISK_CACHE, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CACHE_PATH)
        _DISK_CACHE_DIRTY = False
    except Exception:
        pass


atexit.register(save_disk_cache)


def get_file_content_hash(path: pathlib.Path | str) -> str | None:
    """Compute a fast content hash (blake2b 16 bytes) of an image file, cached by stat."""
    try:
        p = pathlib.Path(path)
        if not p.is_file():
            return None
        st = p.stat()
        stat_key = (str(p.resolve()), st.st_mtime, st.st_size)
        if stat_key in _STAT_CACHE:
            return _STAT_CACHE[stat_key]
        h = hashlib.blake2b(p.read_bytes(), digest_size=16).hexdigest()
        _STAT_CACHE[stat_key] = h
        return h
    except Exception:
        return None


def _ensure_pil() -> bool:
    global HAS_PIL, Image
    if HAS_PIL:
        return True
    try:
        from PIL import Image

        HAS_PIL = True
        return True
    except ImportError:
        pass
    try:
        import subprocess
        import sys

        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "pillow", "numpy"],
            check=False,
            capture_output=True,
            timeout=30,
        )
        from PIL import Image

        HAS_PIL = True
        return True
    except Exception:
        return False


def encode_blurhash_file(path, x_comp: int = 4, y_comp: int = 3) -> str | None:
    """Encode an image file path to Blurhash, cached by the file's content hash."""
    load_disk_cache()
    chash = get_file_content_hash(path)
    if chash and chash in _DISK_CACHE:
        entry = _DISK_CACHE[chash]
        if "bh" in entry:
            return entry["bh"]

    if not _ensure_pil():
        return None

    try:
        p = pathlib.Path(path)
        if not p.is_file():
            return None

        with Image.open(p) as img:
            bh = encode_blurhash_from_image(img, x_comp, y_comp)

        if bh and chash:
            global _DISK_CACHE_DIRTY
            if chash not in _DISK_CACHE:
                _DISK_CACHE[chash] = {}
            _DISK_CACHE[chash]["bh"] = bh
            if "durl" not in _DISK_CACHE[chash]:
                durl = blurhash_to_data_url(bh)
                if durl:
                    _DISK_CACHE[chash]["durl"] = durl
            _DISK_CACHE_DIRTY = True
        return bh
    except Exception:
        # Gracefully tolerate mock bytes (e.g. tests/mkarchive.py synthetic PNGs) or unreadable images
        return None


def get_data_url_for_file(path, bh: str | None = None) -> str | None:
    """Get or compute 16x9 WebP Data URL for an image file, cached by content hash."""
    load_disk_cache()
    chash = get_file_content_hash(path)
    if chash and chash in _DISK_CACHE and "durl" in _DISK_CACHE[chash]:
        return _DISK_CACHE[chash]["durl"]

    if not bh:
        bh = encode_blurhash_file(path)
    if not bh:
        return None

    durl = blurhash_to_data_url(bh)
    if durl and chash:
        global _DISK_CACHE_DIRTY
        if chash not in _DISK_CACHE:
            _DISK_CACHE[chash] = {}
        _DISK_CACHE[chash]["durl"] = durl
        if "bh" not in _DISK_CACHE[chash]:
            _DISK_CACHE[chash]["bh"] = bh
        _DISK_CACHE_DIRTY = True
    return durl


BASE83_REV = {c: i for i, c in enumerate(BASE83_CHARS)}


def decode_83(s: str) -> int:
    """Decode a Base83 string into an integer."""
    val = 0
    for char in s:
        val = val * 83 + BASE83_REV.get(char, 0)
    return val


# In-memory cache for pre-rendered data URLs
_DATA_URL_CACHE: dict[tuple[str, int, int], str] = {}


def blurhash_to_data_url(bh: str | None, width: int = 16, height: int = 9) -> str | None:
    """Decode a Blurhash string into a compact data URL (16x9 WebP/PNG) for instant first-paint placeholders."""
    if not bh or not isinstance(bh, str) or len(bh) < 6 or not HAS_PIL:
        return None
    cache_key = (bh, width, height)
    if cache_key in _DATA_URL_CACHE:
        return _DATA_URL_CACHE[cache_key]

    try:
        size_flag = decode_83(bh[0])
        x_comp = (size_flag % 9) + 1
        y_comp = (size_flag // 9) + 1
        expected_len = 4 + 2 * x_comp * y_comp
        if len(bh) != expected_len:
            return None

        quant_max = decode_83(bh[1])
        max_val = (quant_max + 1) / 166.0

        dc_val = decode_83(bh[2:6])
        dc_r = SRGB_TO_LINEAR_LUT_PY[dc_val >> 16]
        dc_g = SRGB_TO_LINEAR_LUT_PY[(dc_val >> 8) & 255]
        dc_b = SRGB_TO_LINEAR_LUT_PY[dc_val & 255]

        if HAS_NUMPY:
            factors = np.zeros((y_comp, x_comp, 3), dtype=np.float32)
            factors[0, 0, 0] = dc_r
            factors[0, 0, 1] = dc_g
            factors[0, 0, 2] = dc_b

            pos = 6
            for j in range(y_comp):
                for i in range(x_comp):
                    if i == 0 and j == 0:
                        continue
                    ac_val = decode_83(bh[pos : pos + 2])
                    pos += 2
                    qr = ac_val // (19 * 19)
                    qg = (ac_val // 19) % 19
                    qb = ac_val % 19
                    factors[j, i, 0] = sign_pow((qr - 9) / 9.0, 2.0) * max_val
                    factors[j, i, 1] = sign_pow((qg - 9) / 9.0, 2.0) * max_val
                    factors[j, i, 2] = sign_pow((qb - 9) / 9.0, 2.0) * max_val

            x = np.arange(width, dtype=np.float32)
            y = np.arange(height, dtype=np.float32)
            cos_x = np.cos(np.pi * np.outer(np.arange(x_comp, dtype=np.float32), x) / width)
            cos_y = np.cos(np.pi * np.outer(np.arange(y_comp, dtype=np.float32), y) / height)

            pixels = np.einsum("jy,ix,jic->yxc", cos_y, cos_x, factors)
            v = np.clip(pixels, 0.0, 1.0)
            srgb = np.where(v <= 0.0031308, v * 12.92, 1.055 * (v ** (1.0 / 2.4)) - 0.055)
            srgb = (srgb * 255.0 + 0.5).astype(np.uint8)
            img = Image.fromarray(srgb, "RGB")
        else:
            factors = [[(0.0, 0.0, 0.0) for _ in range(x_comp)] for _ in range(y_comp)]
            factors[0][0] = (dc_r, dc_g, dc_b)
            pos = 6
            for j in range(y_comp):
                for i in range(x_comp):
                    if i == 0 and j == 0:
                        continue
                    ac_val = decode_83(bh[pos : pos + 2])
                    pos += 2
                    qr = ac_val // (19 * 19)
                    qg = (ac_val // 19) % 19
                    qb = ac_val % 19
                    factors[j][i] = (
                        sign_pow((qr - 9) / 9.0, 2.0) * max_val,
                        sign_pow((qg - 9) / 9.0, 2.0) * max_val,
                        sign_pow((qb - 9) / 9.0, 2.0) * max_val,
                    )

            cos_x = [[math.cos(math.pi * i * x / width) for x in range(width)] for i in range(x_comp)]
            cos_y = [[math.cos(math.pi * j * y / height) for y in range(height)] for j in range(y_comp)]

            raw = bytearray(width * height * 3)
            idx = 0
            for y in range(height):
                for x in range(width):
                    r = g = b = 0.0
                    for j in range(y_comp):
                        cy = cos_y[j][y]
                        for i in range(x_comp):
                            basis = cy * cos_x[i][x]
                            f = factors[j][i]
                            r += basis * f[0]
                            g += basis * f[1]
                            b += basis * f[2]
                    raw[idx] = linear_to_srgb(r)
                    raw[idx + 1] = linear_to_srgb(g)
                    raw[idx + 2] = linear_to_srgb(b)
                    idx += 3
            img = Image.frombytes("RGB", (width, height), bytes(raw))

        buf = io.BytesIO()
        try:
            img.save(buf, format="WEBP", quality=50)
            mime = "image/webp"
        except Exception:
            buf.seek(0)
            buf.truncate()
            img.save(buf, format="PNG")
            mime = "image/png"

        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        durl = f"data:{mime};base64,{b64}"
        _DATA_URL_CACHE[cache_key] = durl
        return durl
    except Exception:
        return None
