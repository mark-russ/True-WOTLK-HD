"""Minimal BLP2 writer (DXT1 / DXT5 with full mip chain) using a vectorised numpy encoder."""
import struct
import numpy as np
from PIL import Image

def _to565(c):
    # round to the nearest 5/6-bit level. (Truncating with >> drops up to 7 levels of red and blue but only 3 of
    # green, which tints every endpoint slightly green.)
    c = np.clip(c, 0, 255).astype(np.float64)
    r = np.rint(c[..., 0] * 31 / 255).astype(np.int32); g = np.rint(c[..., 1] * 63 / 255).astype(np.int32)
    b = np.rint(c[..., 2] * 31 / 255).astype(np.int32)
    return (r << 11) | (g << 5) | b

def _from565(v):
    r = (v >> 11) & 31; g = (v >> 5) & 63; b = v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.float32)

def _blocks(a):
    h, w = a.shape[:2]
    ph, pw = (-h) % 4, (-w) % 4
    if ph or pw:
        a = np.pad(a, ((0, ph), (0, pw), (0, 0)), mode='edge')
    h, w = a.shape[:2]
    return a.reshape(h//4, 4, w//4, 4, -1).transpose(0, 2, 1, 3, 4).reshape(-1, 16, a.shape[2])

def _color_block(px):
    """px: (N,16,3) float -> (N,) uint64-ish bytes of DXT1 colour block (8 bytes each)."""
    mean = px.mean(1, keepdims=True)
    d = px - mean
    cov = np.einsum('nki,nkj->nij', d, d)
    v = np.ones((px.shape[0], 3), np.float32)
    for _ in range(6):
        v = np.einsum('nij,nj->ni', cov, v)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
    proj = np.einsum('nki,ni->nk', d, v)
    lo = mean[:, 0] + v * proj.min(1)[:, None]
    hi = mean[:, 0] + v * proj.max(1)[:, None]
    c0 = _to565(hi); c1 = _to565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    p0 = _from565(c0); p1 = _from565(c1)
    pal = np.stack([p0, p1, (2*p0 + p1)/3, (p0 + 2*p1)/3], 1)          # (N,4,3)
    dist = ((px[:, :, None, :] - pal[:, None, :, :])**2).sum(-1)       # (N,16,4)
    idx = dist.argmin(-1).astype(np.uint32)
    idx = np.where((c0 == c1)[:, None], 0, idx)
    bits = (idx << (2*np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.zeros((px.shape[0], 8), np.uint8)
    out[:, 0:2] = c0.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = c1.astype('<u2').view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = bits.astype('<u4').view(np.uint8).reshape(-1, 4)
    return out

def _alpha_block(al):
    """al: (N,16) float -> (N,8) DXT5 alpha block."""
    a0 = np.rint(al.max(1)).astype(np.int32); a1 = np.rint(al.min(1)).astype(np.int32)
    a0 = np.where(a0 == a1, np.minimum(a0 + 1, 255), a0)
    a1 = np.where(a0 == a1, a1 - 1, a1)
    f = np.arange(8, dtype=np.float32)
    w0 = np.array([1, 0, 6/7, 5/7, 4/7, 3/7, 2/7, 1/7], np.float32)
    pal = a0[:, None]*w0 + a1[:, None]*(1 - w0)
    pal[:, 0] = a0; pal[:, 1] = a1
    idx = np.abs(al[:, :, None] - pal[:, None, :]).argmin(-1).astype(np.uint64)
    bits = (idx << (3*np.arange(16, dtype=np.uint64))).sum(1)
    out = np.zeros((al.shape[0], 8), np.uint8)
    out[:, 0] = a0; out[:, 1] = a1
    for k in range(6):
        out[:, 2+k] = ((bits >> np.uint64(8*k)) & np.uint64(255)).astype(np.uint8)
    return out

def encode(img, alpha):
    a = np.asarray(img.convert('RGBA'), np.float32)
    bl = _blocks(a)
    col = _color_block(bl[:, :, :3])
    if alpha:
        return np.concatenate([_alpha_block(bl[:, :, 3]), col], 1).tobytes()
    return col.tobytes()

def save_blp(img, path, alpha=False):
    img = img.convert('RGBA')
    w, h = img.size
    mips = []
    cur = img
    while True:
        mips.append(encode(cur, alpha))
        if cur.size == (1, 1) or len(mips) == 16: break
        cur = cur.resize((max(1, cur.size[0]//2), max(1, cur.size[1]//2)), Image.BOX)
    hdr = bytearray(b'BLP2') + struct.pack('<I', 1)
    hdr += bytes([2, 8 if alpha else 0, 7 if alpha else 0, 1]) + struct.pack('<II', w, h)
    offs, sizes = [0]*16, [0]*16
    pos = 1172
    for i, m in enumerate(mips):
        offs[i] = pos; sizes[i] = len(m); pos += len(m)
    hdr += struct.pack('<16I', *offs) + struct.pack('<16I', *sizes) + bytes(1024)
    with open(path, 'wb') as f:
        f.write(hdr); [f.write(m) for m in mips]

def save_blp_palettized(img, path, alpha=False):
    """BLP2 compression 1 (256-colour palette), alphaDepth 0/8, alphaType 8, full mips."""
    img = img.convert('RGBA'); w, h = img.size
    pal_img = img.convert('RGB').quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = pal_img.getpalette()[:768] + [0] * (768 - len(pal_img.getpalette()[:768]))
    palette = b''.join(bytes([pal[3*i+2], pal[3*i+1], pal[3*i], 0]) for i in range(256))   # BGRA, A=0
    mips = []; cur = img
    while True:
        q = cur.convert('RGB').quantize(palette=pal_img, dither=Image.Dither.NONE)
        data = q.tobytes()
        if alpha: data += cur.getchannel('A').tobytes()
        mips.append(data)
        if cur.size == (1, 1) or len(mips) == 16: break
        cur = cur.resize((max(1, cur.size[0]//2), max(1, cur.size[1]//2)), Image.BOX)
    hdr = bytearray(b'BLP2') + struct.pack('<I', 1) + bytes([1, 8 if alpha else 0, 8, 1]) + struct.pack('<II', w, h)
    offs, sizes, pos = [0]*16, [0]*16, 1172
    for i, m in enumerate(mips): offs[i] = pos; sizes[i] = len(m); pos += len(m)
    hdr += struct.pack('<16I', *offs) + struct.pack('<16I', *sizes) + palette
    with open(path, 'wb') as f:
        f.write(hdr); [f.write(m) for m in mips]

def save_blp_pal(img, path, alpha=False):
    """BLP2 compression 1 (256-colour palette + separate 8-bit alpha plane), full mip chain.
    This is the format 3.3.5a's character-texture compositor reads (all stock/worgoblin skins use it)."""
    img = img.convert('RGBA')
    w, h = img.size
    rgb = img.convert('RGB')
    a = np.asarray(rgb)
    if h == w and h >= 2 and (a[h//2:] == a[h//2 - 1]).all():
        # retail1024 skin / bake: the bottom half only repeats the last used row and is never read. Left in, it
        # is half the pixels and takes most of the 256 colours, so the real art is quantised coarsely and the
        # dithering below scatters off-colour specks over it. Pick the palette from the used top half only.
        rgb = rgb.crop((0, 0, w, h // 2))
    q = rgb.quantize(colors=256, method=Image.Quantize.MEDIANCUT, kmeans=3, dither=Image.Dither.NONE)
    palimg = q
    pal = q.getpalette()[:768]
    pal += [0] * (768 - len(pal))
    mips = []; cur = img
    while True:
        idx = cur.convert('RGB').quantize(palette=palimg, dither=Image.Dither.FLOYDSTEINBERG)
        data = bytes(idx.tobytes())
        if alpha:
            data += bytes(np.asarray(cur.getchannel('A'), np.uint8).tobytes())
        mips.append(data)
        if cur.size == (1, 1) or len(mips) == 16: break
        cur = cur.resize((max(1, cur.size[0]//2), max(1, cur.size[1]//2)), Image.BOX)
    hdr = bytearray(b'BLP2') + struct.pack('<I', 1)
    hdr += bytes([1, 8 if alpha else 0, 8, 1]) + struct.pack('<II', w, h)
    offs, sizes = [0]*16, [0]*16; pos = 1172
    for i, m in enumerate(mips):
        offs[i] = pos; sizes[i] = len(m); pos += len(m)
    palette = b''.join(bytes([pal[3*i+2], pal[3*i+1], pal[3*i], 0]) for i in range(256))
    hdr += struct.pack('<16I', *offs) + struct.pack('<16I', *sizes) + palette
    with open(path, 'wb') as f:
        f.write(hdr); [f.write(m) for m in mips]

def load_blp(path):
    """reader that handles palettized BLP2 with a separate alpha plane (Pillow doesn't)."""
    d = open(path, 'rb').read()
    comp, ad, at, _ = d[8], d[9], d[10], d[11]
    w, h = struct.unpack_from('<II', d, 12)
    o0, s0 = struct.unpack_from('<I', d, 20)[0], struct.unpack_from('<I', d, 84)[0]
    if comp != 1:
        return Image.open(path).convert('RGBA')
    pal = np.frombuffer(d, np.uint8, 1024, 148).reshape(256, 4)[:, [2, 1, 0]]
    idx = np.frombuffer(d, np.uint8, w*h, o0)
    rgb = pal[idx].reshape(h, w, 3)
    if ad == 8: a = np.frombuffer(d, np.uint8, w*h, o0 + w*h).reshape(h, w)
    elif ad == 0: a = np.full((h, w), 255, np.uint8)
    else: raise ValueError('alpha depth %d' % ad)
    return Image.fromarray(np.dstack([rgb, a]), 'RGBA')
