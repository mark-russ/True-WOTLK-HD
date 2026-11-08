#!/usr/bin/env python3
"""
Modern (Legion+/MD21, v274) character M2  ->  WotLK 3.3.5a (MD20, v264) M2 + .skin

What it does
  * strips the chunked wrapper (MD21/SFID/AFID/TXID/...), writes plain MD20 v264
  * inlines every external AFM2 .anim into the .m2 (all sequences flagged 0x20)
  * drops sequences whose AnimationData ID the 3.3.5a client doesn't have
  * converts cameras (116 -> 100 bytes, FOV track -> float, far clip /100)
  * remaps texture types / materials / shader ids to things 3.3.5a understands
  * remaps geosets (see GEOSET_RULES) and splits submeshes so no draw call
    needs more than MAX_BONES bones
  * remaps body UVs from the modern 1024x512 layout to the 3.3.5a (x2) 512x512 layout, or (retail1024)
    keeps the retail layout and moves only the body corner pieces into the face square
  * writes a layout.json the texture builder uses (where the relocated UV island went)
"""
import struct, json, math, os, sys, argparse
import numpy as np

SEQ_FMT = '<HHIfIhHIIHH7fhH'          # 64 bytes
VERT_DT = np.dtype([('p', '<3f4'), ('w', '4u1'), ('b', '4u1'), ('n', '<3f4'),
                    ('t0', '<2f4'), ('t1', '<2f4')])

# ----------------------------------------------------------------- reading
def read_chunks(path):
    d = open(path, 'rb').read()
    if d[:4] == b'MD20':
        return {'MD21': d}
    ch, o = {}, 0
    while o < len(d):
        tag = d[o:o+4].decode('latin1'); sz = struct.unpack_from('<I', d, o+4)[0]
        ch[tag] = d[o+8:o+8+sz]; o += 8 + sz
    return ch

def arr(md, off):
    return struct.unpack_from('<II', md, off)

class Seq:
    def __init__(s, raw):
        f = struct.unpack(SEQ_FMT, raw)
        (s.id, s.var, s.dur, s.speed, s.flags, s.freq, s.pad, s.rmin, s.rmax,
         s.bin, s.bout) = f[:11]
        s.bounds = f[11:18]; s.varnext = f[18]; s.alias = f[19]

def load_anims(ch, folder, stem):
    """(animID, subAnimID) -> bytes of the AFM2 payload (offsets are relative to it)."""
    out = {}
    if 'AFID' not in ch:
        return out
    af = ch['AFID']
    for i in range(len(af)//8):
        aid, sub, fdid = struct.unpack_from('<HHI', af, 8*i)
        if not fdid: continue
        p = os.path.join(folder, '%s%04d-%02d.anim' % (stem, aid, sub))
        if not os.path.exists(p):
            print('  ! missing anim file', p); continue
        d = open(p, 'rb').read()
        if d[:4] in (b'AFM2', b'AFSA', b'AFSB'):
            # chunked: AFM2 = the m2's own tracks; with a .skel, AFSB = bone tracks, AFSA = attachment tracks
            # (offsets inside each chunk are relative to that chunk). Without a .skel AFM2 holds everything.
            parts, o = {}, 0
            while o + 8 <= len(d):
                tag = d[o:o+4]; sz = struct.unpack_from('<I', d, o+4)[0]; parts[tag] = d[o+8:o+8+sz]; o += 8 + sz
            m2 = parts.get(b'AFM2', b'')
            out[(aid, sub)] = {'m2': m2, 'bone': parts.get(b'AFSB', m2), 'att': parts.get(b'AFSA', m2)}
        else:
            out[(aid, sub)] = {'m2': d, 'bone': d, 'att': d}
    return out

def load_skeleton(ch, folder, stem):
    """modern models with a SKID chunk keep sequences, bones and attachments in <stem>.skel. Returns
    (buffer, array offset) pairs so the converter reads each part from its own chunk; offsets inside a
    chunk are relative to it. Without a .skel everything comes from the m2 at the usual header offsets."""
    md = ch['MD21']
    plain = {'loops': (md, 20), 'seq': (md, 28), 'bones': (md, 44), 'keybone': (md, 52),
             'att': (md, 240), 'attlk': (md, 248), 'afid': ch}
    if 'SKID' not in ch or arr(md, 44)[0]: return plain       # bones inline (e.g. worgen): ignore the .skel
    p = os.path.join(folder, stem + '.skel')
    if not os.path.exists(p):
        raise SystemExit('%s uses a skeleton file but %s is not in the export' % (stem, os.path.basename(p)))
    sk = read_chunks(p)
    if 'SKPD' in sk:
        raise SystemExit('%s.skel inherits from a parent skeleton (SKPD): not supported yet' % stem)
    print('skeleton', os.path.basename(p), 'chunks', sorted(sk))
    empty = struct.pack('<IIII', 0, 0, 0, 0)
    sks, skb, ska = sk.get('SKS1', empty * 2), sk.get('SKB1', empty), sk.get('SKA1', empty)
    return {'loops': (sks, 0), 'seq': (sks, 8), 'bones': (skb, 0), 'keybone': (skb, 8),
            'att': (ska, 0), 'attlk': (ska, 8), 'afid': sk}

class Track:
    """Generic M2Track: per-sequence (timestamps bytes, values bytes)."""
    __slots__ = ('interp', 'gseq', 'subs', 'vsize')
    def __init__(s, interp, gseq, subs, vsize):
        s.interp, s.gseq, s.subs, s.vsize = interp, gseq, subs, vsize

def read_track(md, off, vsize, seqs, anims, base=False, part='m2'):
    if base:
        interp, gseq, tn, to = struct.unpack_from('<hhII', md, off); vn = vo = 0
    else:
        interp, gseq, tn, to, vn, vo = struct.unpack_from('<hhIIII', md, off)
    subs = []
    for i in range(tn):
        c, o = arr(md, to + 8*i)
        if base: vc, voff = 0, 0
        else: vc, voff = arr(md, vo + 8*i)
        src = md
        if gseq < 0 and i < len(seqs) and not (seqs[i].flags & 0x20):
            src = anims.get((seqs[i].id, seqs[i].var), {}).get(part)
            if not src or c == 0:
                subs.append((b'', b'')); continue
        ts = bytes(src[o:o+4*c]) if c else b''
        vals = bytes(src[voff:voff+vsize*vc]) if (vc and not base) else b''
        if len(ts) != 4*c or (not base and len(vals) != vsize*vc):
            raise ValueError('track out of range')
        subs.append((ts, vals))
    return Track(interp, gseq, subs, vsize)

# ----------------------------------------------------------------- writing
class Writer:
    def __init__(s, header_size):
        s.b = bytearray(header_size)
    def put(s, data, align=16):
        if not data: return 0
        while len(s.b) % align: s.b.append(0)
        o = len(s.b); s.b += data; return o
    def array(s, data, count):
        return struct.pack('<II', count, s.put(data) if count else 0)
    def track(s, t, keep, base=False):
        subs = t.subs if t.gseq >= 0 else [t.subs[i] for i in keep if i < len(t.subs)]
        tsa = bytearray(); va = bytearray()
        for ts, vals in subs:
            tsa += struct.pack('<II', len(ts)//4, s.put(ts))
            if not base: va += struct.pack('<II', len(vals)//t.vsize, s.put(vals))
        out = struct.pack('<hh', t.interp, t.gseq) + s.array(bytes(tsa), len(subs))
        if not base: out += s.array(bytes(va), len(subs))
        return out

# ----------------------------------------------------------------- geosets
# Per-race settings, set by hdport.py before convert() runs (see races/*.json).
CFG = {}

def geoset_rules(sex):
    """modern submesh id -> list of 3.3.5a ids ([] = drop), driven by CFG['geosets']:
         keep_groups : groups copied unchanged (0 = hair + base mesh)
         shift       : {group: n}  add n to the value (e.g. facial hair 'none' must become value 1)
         map         : {modern id: [3.3.5a ids]}  explicit renames / duplicates / always-on (0)
       anything else is dropped."""
    g_cfg = dict(CFG['geosets'])
    for k, v in CFG['sexes'].get(sex, {}).get('geosets', {}).items():     # per-sex overrides, merged per key
        g_cfg[k] = dict(g_cfg.get(k, {}), **v) if isinstance(v, dict) else v
    if g_cfg.get('keep_all'):
        return lambda i: [i]
    keep = set(g_cfg.get('keep_groups', []))
    shift = {int(k): v for k, v in g_cfg.get('shift', {}).items()}
    explicit = {int(k): v for k, v in g_cfg.get('map', {}).items()}
    remap = {int(k): v for k, v in g_cfg.get('remap_groups', {}).items()}
    hair_remap = {int(k): int(v) for k, v in CFG.get('hair_geoset_remap', {}).items()}
    def rule(i):
        if i in hair_remap: return [hair_remap[i]]                     # hair geoset moved to a free ID
        if i in explicit: return list(explicit[i])
        g, v = divmod(i, 100)
        if i == 0: return [0]
        if g in remap: return [remap[g]*100 + v + shift.get(remap[g], 0)]    # e.g. retail 35xx piercings -> 1xx
        if g in shift: return [g*100 + v + shift[g]]
        if g in keep: return [i]
        return []
    return rule

TEX_TYPE_MAP = {1: 1, 2: 2, 8: 8, 0: 0}       # base; races add e.g. {"19": 6} for eyes in the hair slot
def tex_type_map():
    m = dict(TEX_TYPE_MAP); m.update({int(k): v for k, v in CFG.get('texture_types', {}).items()}); return m

# ----------------------------------------------------------------- UV layout
FACE_RECT = (0, 320, 256, 192)       # WotLK x2: FaceUpper (0,320,256,64) + FaceLower (0,384,256,128)

def face_mask(uvpx_face_tris, rect=FACE_RECT):
    """face-area pixels the face triangles use (WotLK px, FACE_RECT-relative), dilated for mip bleed"""
    W, H = rect[2], rect[3]
    mask = np.zeros((H, W), bool)
    for tri in uvpx_face_tris:                         # tri in WotLK px
        xs, ys = tri[:, 0] - rect[0], tri[:, 1] - rect[1]
        x0, x1 = int(max(xs.min()-3, 0)), int(min(xs.max()+3, W-1))
        y0, y1 = int(max(ys.min()-3, 0)), int(min(ys.max()+3, H-1))
        if x0 > x1 or y0 > y1: continue
        gx, gy = np.meshgrid(np.arange(x0, x1+1)+0.5, np.arange(y0, y1+1)+0.5)
        a, b, c = tri[:, :2] - [rect[0], rect[1]]
        def side(p, q): return (q[0]-p[0])*(gy-p[1]) - (q[1]-p[1])*(gx-p[0])
        d1, d2, d3 = side(a, b), side(b, c), side(c, a)
        inside = ~(((d1 < 0) | (d2 < 0) | (d3 < 0)) & ((d1 > 0) | (d2 > 0) | (d3 > 0)))
        mask[y0:y1+1, x0:x1+1] |= inside
    # dilate a little more for mip bleed
    m = mask.copy()
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            m |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return m

def free_spot(m, w, h):
    """first (x, y) where a w x h box is free in mask m, or None"""
    H, W = m.shape
    integ = np.pad(m.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    if h > H or w > W: return None
    S = integ[h:, w:] - integ[:-h, w:] - integ[h:, :-w] + integ[:-h, :-w]      # sum of every w x h window
    ys, xs = np.nonzero(S[:H-h, :W-w] == 0)
    if not len(ys): return None
    k = np.lexsort((xs, ys))[0]                                                # row-major first, as the old scan
    return int(xs[k]), int(ys[k])

def plan_island(uvpx_face_tris, island_bbox):
    """find a free spot in the WotLK face area for the small UV island that lives in the
    modern layout's (now face) corner. returns (scale, dx, dy)."""
    W, H = FACE_RECT[2], FACE_RECT[3]
    m = face_mask(uvpx_face_tris)
    integ = np.pad(m.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    bw, bh = island_bbox[2] - island_bbox[0], island_bbox[3] - island_bbox[1]
    for s in np.arange(1.0, 0.24, -0.05):
        w, h = int(math.ceil(bw*s)) + 2, int(math.ceil(bh*s)) + 2
        for y in range(0, H-h):
            for x in range(0, W-w):
                if integ[y+h, x+w] - integ[y, x+w] - integ[y+h, x] + integ[y, x] == 0:
                    return float(s), FACE_RECT[0] + x + 1, FACE_RECT[1] + y + 1
    raise SystemExit('no room for UV island')

def island_groups(uv, tris_local, margin=2.0):
    """island triangles (indices into uv) -> groups to move as one: pieces connected by shared
    vertices, merged while their UV boxes overlap (geoset variants share texels). [(bbox, verts)]"""
    parent = {}
    def f(x):
        while parent.setdefault(x, x) != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for t in tris_local:
        for v in t[1:]: parent[f(int(v))] = f(int(t[0]))
    comp = {}
    for t in tris_local:
        for v in t: comp.setdefault(f(int(t[0])), set()).add(int(v))
    groups = [[uv[list(vs)].min(0), uv[list(vs)].max(0), vs] for vs in comp.values()]
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = groups[i], groups[j]
                if (a[0] <= b[1] + margin).all() and (b[0] <= a[1] + margin).all():
                    groups[i] = [np.minimum(a[0], b[0]), np.maximum(a[1], b[1]), a[2] | b[2]]
                    del groups[j]; merged = True; break
            if merged: break
    return [((float(g[0][0]), float(g[0][1]), float(g[1][0]), float(g[1][1])), g[2]) for g in groups]

def pack_islands(uvpx_face_tris, boxes, floor=0.10, rect=FACE_RECT):
    """place every box in the free face area at one common scale, largest first.
    returns (scale, [(dx, dy), ...]) in WotLK px, or None"""
    base = face_mask(uvpx_face_tris, rect)
    order = sorted(range(len(boxes)), key=lambda i: -(boxes[i][2]-boxes[i][0]) * (boxes[i][3]-boxes[i][1]))
    for s in np.arange(1.0, floor - 0.005, -0.05):
        m = base.copy(); dst = [None] * len(boxes)
        for i in order:
            x0, y0, x1, y1 = boxes[i]
            w, h = int(math.ceil((x1-x0)*s)) + 2, int(math.ceil((y1-y0)*s)) + 2
            p = free_spot(m, w, h)
            if p is None: break
            x, y = p
            m[max(y-4, 0):y+h+4, max(x-4, 0):x+w+4] = True        # 4 px clear of the next piece (paste margin)
            dst[i] = (rect[0] + x + 1, rect[1] + y + 1)
        else:
            return float(s), dst
    return None

# ----------------------------------------------------------------- retail1024
RETAIL_FACE = (512, 0, 512, 512)     # retail px (1024x512 layout): the face square = the patched client's face regions
RETAIL_CORNER = (256, 320)           # retail px: body pieces at u < 256, v >= 320 sit in the stock face area

def retail1024_uv(V, body_tris, body_geo, body_verts, texs, sex, margin=16):
    """retail1024 (client patched with patch_component_layout.py): the 1024x1024 character texture holds
    the retail 1024x512 layout in its top half, so body UVs keep u and halve v.
    The client composites only inside its regions. The retail body corner (u < 256, v >= 320 px: tails,
    tentacles, claws, bone and neck pieces) is the stock face area, which no region covers once the face
    moved right, so it draws black. Those pieces are moved into free pixels of the face square, where
    every face texture carries them (assets.Builder.face_area), as in the stock layout; with island_extra
    they are drawn from the skin extra texture (type 8) at full size instead.
    What moves: every body triangle whose centre is in the corner, plus every separate piece of mesh that
    has such a triangle and lies within the corner give or take `margin` px (retail UVs bleed over the
    edge). A vertex used by moving and staying triangles is duplicated, and those triangles are
    re-pointed through TRI_REMAP when the skins are built.
    Returns (V, layout record); V grows by the duplicated vertices."""
    uv_all = V['t0'].astype(np.float64) * [1024, 512]
    lay = {'island_src': (0, 0, 0, 0), 'island_scale': 0, 'island_dst': [0, 0], 'retail1024': True}
    cu, cv = RETAIL_CORNER
    cen = uv_all[body_tris].mean(1)
    isl_t = (cen[:, 0] < cu) & (cen[:, 1] >= cv)
    if isl_t.any():
        # separate pieces of mesh (connected by shared vertices) that only bleed over the corner edge
        parent = list(range(len(V)))
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        for a, b, c in body_tris.tolist():
            ra = find(a); parent[find(b)] = ra; parent[find(c)] = ra
        troot = np.array([find(int(v)) for v in body_tris[:, 0]])
        for r in set(troot[isl_t].tolist()):
            m = troot == r
            p = uv_all[np.unique(body_tris[m])]
            if p[:, 0].max() < cu + margin and p[:, 1].min() >= cv - margin: isl_t |= m
    if not isl_t.any():
        V['t0'][body_verts] = (uv_all[body_verts] / 1024.0).astype(np.float32)    # u kept, v halved (exact)
        print('UV: retail layout (1024x1024 character texture): %d body vertices, v halved, no body corner pieces' % len(body_verts))
        return V, lay
    # a vertex shared by moving and staying triangles: the moving ones get a copy
    shared = sorted(set(body_tris[isl_t].ravel().tolist()) & set(body_tris[~isl_t].ravel().tolist()))
    if shared:
        dup = {v: len(V) + k for k, v in enumerate(shared)}
        V = np.concatenate([V, V[shared]])
        body_tris = body_tris.copy()
        for k in np.nonzero(isl_t)[0]:
            old = tuple(int(v) for v in body_tris[k])
            new = tuple(dup.get(v, v) for v in old)
            if new != old: TRI_REMAP[old] = new; body_tris[k] = new
        uv_all = V['t0'].astype(np.float64) * [1024, 512]
    body_verts = np.unique(body_tris)
    move = np.unique(body_tris[isl_t])
    out = np.zeros((len(V), 2))
    out[body_verts] = uv_all[body_verts] / 1024.0                     # u kept, v halved (exact)
    if CFG['sexes'][sex].get('island_extra'):
        geos = set(body_geo[isl_t].tolist())
        mixed = sorted(g for g in geos if (body_geo[~isl_t] == g).any())
        if mixed: raise SystemExit('island_extra: geosets %s also draw body triangles outside the corner' % mixed)
        if 8 in [t[0] for t in texs]: raise SystemExit('island_extra: the model already uses the skin extra texture (type 8)')
        EXTRA_GEOSETS.clear(); EXTRA_GEOSETS.update(geos)
        out[move, 0] = np.clip(uv_all[move, 0], 0, 256) / 256.0      # -> 256x256 extra texture: (u, v - 320) px
        out[move, 1] = np.clip(uv_all[move, 1] - 320, 0, 192) / 256.0
        lay['island_extra'] = [0, 320, 256, 512]
        print('UV: retail layout; body corner -> skin extra texture, full size (geosets %s)' % sorted(geos))
    else:
        face_tris = [uv_all[t] for t in body_tris[cen[:, 0] >= 512]]
        groups = island_groups(uv_all, body_tris[isl_t].tolist())
        res = pack_islands(face_tris, [g[0] for g in groups], rect=RETAIL_FACE)
        if res is None: raise SystemExit('retail1024: no room in the face square for the body corner pieces')
        s, dsts = res
        islands = []
        for (bb, vs), (gx, gy) in zip(groups, dsts):
            vs = np.array(sorted(vs))
            out[vs, 0] = (gx + (uv_all[vs, 0] - bb[0]) * s) / 1024.0
            out[vs, 1] = (gy + (uv_all[vs, 1] - bb[1]) * s) / 1024.0
            islands.append({'src': bb, 'scale': float(s), 'dst': [gx, gy]})
        lay['islands'] = islands
        print('UV: retail layout; body corner as %d pieces (%d triangles, %d vertices copied) -> face square, scale %.2f'
              % (len(groups), int(isl_t.sum()), len(shared), s))
    V['t0'][body_verts] = out[body_verts].astype(np.float32)
    return V, lay

TRI_REMAP = {}     # retail1024: source triangle (global vertex ids) -> the same triangle on copied corner vertices

# ----------------------------------------------------------------- main
def convert(src_m2, out_dir, stem_out, anim_ids, skin_srcs, max_bones=64, sex='male', internal_name=None):
    folder = os.path.dirname(src_m2)
    stem = os.path.basename(src_m2)[:-3]
    EXTRA_GEOSETS.clear(); TRI_REMAP.clear()
    ch = read_chunks(src_m2); md = ch['MD21']
    assert md[:4] == b'MD20'
    ver = struct.unpack_from('<I', md, 4)[0]
    print('source version', ver)
    txid = struct.unpack('<%dI' % (len(ch['TXID'])//4), ch['TXID']) if 'TXID' in ch else ()

    SK = load_skeleton(ch, folder, stem)
    sbuf, soff = SK['seq']
    n, of = arr(sbuf, soff)
    seqs = [Seq(sbuf[of+64*i:of+64*i+64]) for i in range(n)]
    anims = load_anims(SK['afid'], folder, stem)
    print('sequences', n, 'external anim files', len(anims))

    # ---- which sequences survive
    keep_set = {i for i, s in enumerate(seqs) if s.id in anim_ids}
    changed = True
    while changed:
        changed = False
        for i in list(keep_set):
            s = seqs[i]
            if s.flags & 0x40 and s.alias not in keep_set:
                keep_set.discard(i); changed = True
    keep = sorted(keep_set); newidx = {o: k for k, o in enumerate(keep)}
    print('kept sequences', len(keep), 'dropped', n - len(keep))

    # ---- vertices (only the ones a skin actually references)
    skins = [open(p, 'rb').read() for p in skin_srcs]
    used = 0
    for s in skins:
        c, o = arr(s, 4); used = max(used, int(np.frombuffer(s, '<u2', c, o).max()) + 1)
    nv, ov = arr(md, 60)
    V = np.frombuffer(md, VERT_DT, used, ov).copy()
    print('vertices', nv, '->', used)

    # ---- textures / combos
    nt, ot = arr(md, 80)
    texs = []
    for i in range(nt):
        t, fl, nn, no = struct.unpack_from('<IIII', md, ot+16*i)
        texs.append([t, fl, txid[i] if i < len(txid) else 0])
    nc, oc = arr(md, 128); texcombos = list(struct.unpack_from('<%dH' % nc, md, oc))
    nm, om = arr(md, 112); mats = [list(struct.unpack_from('<HH', md, om+4*i)) for i in range(nm)]

    # ---- body UV remap (based on skin 0 batches)
    s0 = skins[0]
    cvl, ovl = arr(s0, 4); vl0 = np.frombuffer(s0, '<u2', cvl, ovl)
    cil, oil = arr(s0, 12); il0 = np.frombuffer(s0, '<u2', cil, oil)
    nsm, osm = arr(s0, 28); nb, ob = arr(s0, 36)
    subs0 = [struct.unpack_from('<HHHHHH', s0, osm+48*i) for i in range(nsm)]
    layout = {'island_src': (0, 0, 0, 0), 'island_scale': 0, 'island_dst': [0, 0]}
    rule = geoset_rules(sex)
    if CFG.get('body_layout', True):
        body_tris = []; body_geo = []
        other_verts = set()
        rule = geoset_rules(sex)
        for i in range(nb):
            b = struct.unpack_from('<BBHHHHHHHHHHH', s0, ob+24*i)
            sid, lvl, vs, vc, ist, ic = subs0[b[3]]
            if not rule(sid): continue
            st = ist + (lvl << 16)
            tri = vl0[il0[st:st+ic]].reshape(-1, 3)
            if texs[texcombos[b[9]]][0] == 1: body_tris.append(tri); body_geo += [sid] * len(tri)
            else: other_verts.update(np.unique(tri).tolist())
        body_tris = np.concatenate(body_tris); body_geo = np.array(body_geo)
        body_verts = np.unique(body_tris)
        assert not (set(body_verts.tolist()) & other_verts), 'vertex shared between body and non-body batch'
        if CFG.get('layout') == 'retail1024':
            # patched client (patch_component_layout.py): the character texture is 1024x1024 and holds the
            # retail 1024x512 layout in its top half, so body UVs keep u and halve v; nothing moves
            V, layout = retail1024_uv(V, body_tris, body_geo, body_verts, texs, sex); used = len(V)
        else:
            # retail UVs sometimes bleed a pixel or two over the face-square edge (u = 512 px): snap such
            # vertices back to the side their triangle is on, if no triangle on the other side uses them
            tuv = V['t0'][body_tris].astype(np.float64) * [1024, 512]
            tface = tuv[..., 0] >= 512
            mixed = tface.min(1) != tface.max(1)
            if mixed.any():
                side = tuv[mixed][..., 0].mean(1) >= 512                      # triangle centroid side
                fix = {}
                for tri, uvs, f in zip(body_tris[mixed], tuv[mixed], side):
                    for v, (u, _) in zip(tri, uvs):
                        if (u >= 512) != f and abs(u - 512) <= 4: fix[int(v)] = f
                users = {}
                for tri, uvs in zip(body_tris, tuv):
                    for v, (u, _) in zip(tri, uvs):
                        if int(v) in fix: users.setdefault(int(v), set()).add(bool(uvs[:, 0].mean() >= 512))
                ok = [v for v, f in fix.items() if users[v] == {f}]
                for v in ok:
                    V['t0'][v, 0] = (512.5 if fix[v] else 511.5) / 1024
                if ok: print('UV: snapped %d vertices that bled over the face edge' % len(ok))
            uv = V['t0'][body_verts].astype(np.float64) * [1024, 512]
            is_face = uv[:, 0] >= 512
            is_isl = (~is_face) & (uv[:, 0] < 256) & (uv[:, 1] >= 320)
            # consistency per triangle
            cls = np.zeros(used, np.int8); cls[body_verts[is_face]] = 1; cls[body_verts[is_isl]] = 2
            tc = cls[body_tris]
            bad = (tc.min(1) != tc.max(1)).sum()
            assert bad == 0, '%d body triangles straddle layout regions' % bad
            def face_map(px):
                return np.stack([(px[:, 0]-512)*0.5, 320 + px[:, 1]*0.375], 1)
            face_tris = [face_map(V['t0'][t].astype(np.float64)*[1024, 512]) for t in body_tris[tc[:, 0] == 1]]
            iu = uv[is_isl]
            isl_bbox = (float(iu[:, 0].min()), float(iu[:, 1].min()), float(iu[:, 0].max()), float(iu[:, 1].max())) if len(iu) else (0, 0, 0, 0)
            if len(iu):
                try: scale, dx, dy = plan_island(face_tris, isl_bbox)
                except SystemExit: scale, dx, dy = 0.0, 0, 0
            else:
                scale, dx, dy = 1.0, 0, 0
            packed = None
            if len(iu) and CFG['sexes'][sex].get('island_extra'):
                # the island's own geosets draw from the skin extra texture (type 8) at full resolution
                isl_t = tc[:, 0] == 2
                geos = set(body_geo[isl_t].tolist())
                mixed = sorted(g for g in geos if (body_geo[~isl_t] == g).any())
                if mixed: raise SystemExit('island_extra: geosets %s also draw body triangles outside the island' % mixed)
                if 8 in [t[0] for t in texs]: raise SystemExit('island_extra: the model already uses the skin extra texture (type 8)')
                EXTRA_GEOSETS.clear(); EXTRA_GEOSETS.update(geos)
                new = uv.copy()
                new[is_face] = face_map(uv[is_face])
                new[is_isl, 0] = uv[is_isl, 0] * 2.0                     # -> 256x256 extra texture: (u, v - 320) px
                new[is_isl, 1] = (uv[is_isl, 1] - 320) * 2.0
                V['t0'][body_verts] = (new / 512.0).astype(np.float32)
                layout = {'island_src': (0, 0, 0, 0), 'island_scale': 0, 'island_dst': [0, 0], 'island_extra': [0, 320, 256, 512]}
                print('UV island', [round(x, 1) for x in isl_bbox], '-> skin extra texture, full size (geosets %s)' % sorted(geos))
            elif len(iu) and scale < 1.0:
                # one box doesn't fit at full size: move the island's separate pieces on their own
                row = {int(v): k for k, v in enumerate(body_verts)}
                isl_tris = [[row[int(v)] for v in t] for t in body_tris[tc[:, 0] == 2]]
                groups = island_groups(uv, isl_tris)
                res = pack_islands(face_tris, [g[0] for g in groups])
                if res and res[0] > scale + 1e-6: packed = (res[0], groups, res[1])
            if 'island_extra' in layout: pass
            elif packed:
                new = uv.copy()
                new[is_face] = face_map(uv[is_face])
                s, groups, dsts = packed
                print('UV island', [round(x, 1) for x in isl_bbox], 'as %d pieces -> scale %.2f (one box: %.2f)' % (len(groups), s, scale))
                islands = []
                for (bb, vs), (gx, gy) in zip(groups, dsts):
                    vs = np.array(sorted(vs))
                    new[vs, 0] = gx + (uv[vs, 0] - bb[0]) * s
                    new[vs, 1] = gy + (uv[vs, 1] - bb[1]) * s
                    islands.append({'src': bb, 'scale': s, 'dst': [gx, gy]})
                layout = {'island_src': (0, 0, 0, 0), 'island_scale': 0, 'island_dst': [0, 0], 'islands': islands}
            else:
                new = uv.copy()
                new[is_face] = face_map(uv[is_face])
                if not scale: raise SystemExit('no room for UV island')
                print('UV island', [round(x, 1) for x in isl_bbox], '-> scale %.2f at (%d,%d)' % (scale, dx, dy))
                new[is_isl, 0] = dx + (uv[is_isl, 0] - isl_bbox[0]) * scale
                new[is_isl, 1] = dy + (uv[is_isl, 1] - isl_bbox[1]) * scale
                layout = {'island_src': isl_bbox, 'island_scale': scale, 'island_dst': [dx, dy]}
            if 'island_extra' not in layout: V['t0'][body_verts] = (new / 512.0).astype(np.float32)

    # ---- eyes: atlas UVs and halo cards
    eye_verts = set()
    for sid, lvl, vs, vc, ist, ic in subs0:
        if sid == CFG.get('eye_glow', {}).get('geoset', -1):
            st = ist + (lvl << 16); eye_verts.update(vl0[il0[st:st+ic]].tolist())
    eye_verts = np.array(sorted(eye_verts))
    halo = None
    if len(eye_verts):
        u0 = V['t0'][eye_verts].astype(np.float64)
        V['t0'][eye_verts] = np.stack([u0[:, 0]*0.5, u0[:, 1]*0.25], 1).astype(np.float32)       # atlas (0,0)-(256,128)
        V['t1'][eye_verts] = np.stack([u0[:, 0]*0.5, 0.5 + u0[:, 1]*0.25], 1).astype(np.float32) # atlas (0,256)-(256,384)
        halo_v = []; halo_t = []
        ep = V['p'][eye_verts].astype(np.float64)
        en = V['n'][eye_verts].astype(np.float64)
        for side in (ep[:, 1] > 0, ep[:, 1] <= 0):
            cl = ep[side]; ids = eye_verts[side]
            c = cl.mean(0); half = max(np.ptp(cl[:, 1]), np.ptp(cl[:, 2])) / 2
            n = en[side].mean(0); n /= np.linalg.norm(n)                     # this eye's outward direction
            up = np.array([0, 0, 1.0]) - n * n[2]; up /= np.linalg.norm(up)
            right = np.cross(up, n)
            # card a bit bigger than the eye, never reaching past the nose bridge
            sz = min(half * 1.35, abs(c[1]) * 0.95)
            front = (cl - c) @ n
            ctr = c + n * (front.max() + half * 0.35)                          # just in front of the eyeball
            near = ids[np.argmin(((cl - c)**2).sum(1))]
            base = used + len(halo_v)
            for dy_, dz_ in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                v = np.zeros(1, VERT_DT)[0]
                v['p'] = ctr + right * dy_ * sz + up * dz_ * sz; v['n'] = n
                v['w'] = V['w'][near]; v['b'] = V['b'][near]
                q, r = (dy_ + 1) / 2, (1 - dz_) / 2
                v['t0'] = v['t1'] = ((320 + q*128) / 512, (320 + r*128) / 512)
                halo_v.append(v)
            halo_t += [(base, base+1, base+2), (base, base+2, base+3)]
        V = np.concatenate([V, np.array(halo_v, VERT_DT)])
        halo = {'tris': halo_t}
        used = len(V)
        print('eyes: %d verts in atlas, %d halo cards' % (len(eye_verts), len(halo_t)//2))

    # ---- texture list rebuild: only textures referenced by surviving batches
    used_tex = []
    def tex_index(old):
        if old == 'extra':                         # island_extra: skin extra texture, type 8
            key = (8, 0)
            for k, u in enumerate(used_tex):
                if u['key'] == key: return k
            used_tex.append({'key': key, 'flags': 0, 'fdid': 0}); return len(used_tex)-1
        t = texs[old]
        if str(t[0]) in CFG.get('fixed_textures', {}):
            key = (0, 'fixed:%d' % t[0])           # replaceable in retail, baked to a fixed file here
        else:
            key = (tex_type_map().get(t[0], None), t[2] if t[0] == 0 else 0)
        if key[0] is None: raise ValueError('texture type %d unsupported' % t[0])
        for k, u in enumerate(used_tex):
            if u['key'] == key: return k
        used_tex.append({'key': key, 'flags': t[1], 'fdid': t[2]}); return len(used_tex)-1
    combos_out = []
    def combo_for(new_tex):
        for k, c in enumerate(combos_out):
            if c == new_tex: return k
        combos_out.append(new_tex); return len(combos_out)-1
    mats_out = []
    def mat_for(m):
        flags, blend = m
        blend = {7: 4}.get(blend, blend)
        if blend > 6: blend = 2
        mm = [flags & 0x1F, blend]
        if mm not in mats_out: mats_out.append(mm)
        return mats_out.index(mm)

    # ---- skins
    nbc, obc = arr(md, 120); bonecombos = np.frombuffer(md, '<u2', nbc, obc)
    lookup_out = []           # new bone lookup (palettes)
    skin_bytes = []
    for si, s in enumerate(skins):
        skin_bytes.append(build_skin(s, V, bonecombos, rule, lookup_out, max_bones,
                                     lambda b, sid=None: batch_fix(b, texs, texcombos, mats, tex_index,
                                                                   combo_for, mat_for, sid),
                                     halo, mat_for))
    # ---- write m2
    w = Writer(0x130)
    H = bytearray(0x130)
    H[0:4] = b'MD20'; struct.pack_into('<I', H, 4, 264)
    name = ((internal_name or stem_out) + '\0').encode()
    H[8:16] = w.array(name, len(name))
    struct.pack_into('<I', H, 16, 0x80)
    def raw_array(off, size):
        c, o = arr(md, off)
        return w.array(md[o:o+size*c], c)
    def raw_from(key, size):
        buf, off = SK[key]; c, o = arr(buf, off)
        return w.array(buf[o:o+size*c], c)
    H[20:28] = raw_from('loops', 4)                              # global loops
    # sequences
    sb = bytearray()
    for i in keep:
        s = seqs[i]
        vn = s.varnext
        while vn >= 0 and vn not in newidx: vn = seqs[vn].varnext
        al = newidx.get(s.alias, newidx[i]) if (s.flags & 0x40) else newidx[i]
        flags = (s.flags & 0xFF) | 0x20
        sb += struct.pack(SEQ_FMT, s.id, s.var, s.dur, s.speed, flags, s.freq, 0,
                          s.rmin, s.rmax, s.bin, 0, *s.bounds,
                          newidx[vn] if vn >= 0 else -1, al)
    H[28:36] = w.array(bytes(sb), len(keep))
    # sequence lookup (quadratic probing, as the client does)
    N = max(len(keep) * 2 + 1, 2)
    N = int(struct.unpack_from('<I', md, 36)[0]) if False else N
    table = [-1] * N; seen = set()
    for k, i in enumerate(keep):
        sid = seqs[i].id
        if sid in seen: continue
        seen.add(sid); h = sid % N; step = 1
        while table[h] != -1:
            h = (h + step*step) % N; step += 1
        table[h] = k
    H[36:44] = w.array(struct.pack('<%dh' % N, *table), N)
    # bones
    bmd, boff = SK['bones']
    nbn, obn = arr(bmd, boff)
    bones = bytearray()
    for i in range(nbn):
        o = obn + 88*i
        head = bmd[o:o+16]
        tr = read_track(bmd, o+16, 12, seqs, anims, part='bone')
        ro = read_track(bmd, o+36, 8, seqs, anims, part='bone')
        sc = read_track(bmd, o+56, 12, seqs, anims, part='bone')
        bones += head + w.track(tr, keep) + w.track(ro, keep) + w.track(sc, keep) + bmd[o+76:o+88]
    H[44:52] = w.array(bytes(bones), nbn)
    H[52:60] = raw_from('keybone', 2)                            # key bone lookup
    H[60:68] = w.array(V.tobytes(), used)
    struct.pack_into('<I', H, 68, len(skins))
    # colors
    ncl, ocl = arr(md, 72); cb = bytearray()
    for i in range(ncl):
        o = ocl + 40*i
        cb += w.track(read_track(md, o, 12, seqs, anims), keep) + w.track(read_track(md, o+20, 2, seqs, anims), keep)
    H[72:80] = w.array(bytes(cb), ncl)
    # textures written after skins built (used_tex filled)
    tb = bytearray()
    tex_names = []
    for u in used_tex:
        ttype, fdid = u['key']
        nm_ = b''
        if ttype == 0:
            fn = hardcoded_name(sex, fdid)
            tex_names.append((fdid, fn)); nm_ = (fn + '\0').encode('latin1')
        tb += struct.pack('<II', ttype, u['flags'] & 3) + w.array(nm_, len(nm_))
    H[80:88] = w.array(bytes(tb), len(used_tex))
    # transparency
    ntw, otw = arr(md, 88); twb = bytearray()
    for i in range(ntw): twb += w.track(read_track(md, otw+20*i, 2, seqs, anims), keep)
    H[88:96] = w.array(bytes(twb), ntw)
    # texture transforms
    ntt, ott = arr(md, 96); ttb = bytearray()
    for i in range(ntt):
        o = ott + 60*i
        ttb += (w.track(read_track(md, o, 12, seqs, anims), keep) +
                w.track(read_track(md, o+20, 16, seqs, anims), keep) +
                w.track(read_track(md, o+40, 12, seqs, anims), keep))
    H[96:104] = w.array(bytes(ttb), ntt)
    # replaceable texture lookup: type -> texture index
    rep = [-1] * 19
    for k, u in enumerate(used_tex):
        if u['key'][0] and rep[u['key'][0]] == -1: rep[u['key'][0]] = k
    H[104:112] = w.array(struct.pack('<19h', *rep), 19)
    H[112:120] = w.array(b''.join(struct.pack('<HH', *m) for m in mats_out), len(mats_out))
    H[120:128] = w.array(struct.pack('<%dH' % len(lookup_out), *lookup_out), len(lookup_out))
    H[128:136] = w.array(struct.pack('<%dH' % len(combos_out), *combos_out), len(combos_out))
    H[136:144] = w.array(struct.pack('<2h', 0, 1), 2)            # texture coord combos: UV0, UV1
    H[144:152] = raw_array(144, 2)                                 # transparency lookup
    H[152:160] = raw_array(152, 2)                                 # uv anim lookup
    H[160:216] = md[160:216]                                       # bounds + collision bounds
    H[216:224] = raw_array(216, 2)
    H[224:232] = raw_array(224, 12)
    H[232:240] = raw_array(232, 12)
    # attachments
    amd, aoff = SK['att']
    nat, oat = arr(amd, aoff); ab = bytearray()
    for i in range(nat):
        o = oat + 40*i
        ab += amd[o:o+20] + w.track(read_track(amd, o+20, 1, seqs, anims, part='att'), keep)
    H[240:248] = w.array(bytes(ab), nat)
    H[248:256] = raw_from('attlk', 2)
    # events
    nev, oev = arr(md, 256); eb = bytearray()
    for i in range(nev):
        o = oev + 36*i
        eb += md[o:o+24] + w.track(read_track(md, o+24, 0, seqs, anims, base=True), keep, base=True)
    H[256:264] = w.array(bytes(eb), nev)
    assert arr(md, 264)[0] == 0 and arr(md, 288)[0] == 0 and arr(md, 296)[0] == 0, 'lights/ribbons/particles not handled'
    # cameras 116 -> 100
    ncm, ocm = arr(md, 272); cmb = bytearray()
    for i in range(ncm):
        o = ocm + 116*i
        ctype, far, near = struct.unpack_from('<Iff', md, o)
        fovt = read_track(md, o+96, 12, seqs, anims)
        fov = 0.65
        for ts, vals in fovt.subs:
            if vals: fov = struct.unpack_from('<f', vals)[0]; break
        fov = 2*math.atan(math.tan(fov/2) * 5/3)     # vertical(modern) -> 3.3.5a convention (4:3 diagonal)
        cmb += struct.pack('<Ifff', ctype, fov, far/100.0, near)
        cmb += w.track(read_track(md, o+12, 36, seqs, anims), keep) + md[o+32:o+44]
        cmb += w.track(read_track(md, o+44, 36, seqs, anims), keep) + md[o+64:o+76]
        cmb += w.track(read_track(md, o+76, 12, seqs, anims), keep)
    H[272:280] = w.array(bytes(cmb), ncm)
    H[280:288] = raw_array(280, 2)
    w.b[0:0x130] = H
    os.makedirs(out_dir, exist_ok=True)
    open(os.path.join(out_dir, stem_out + '.m2'), 'wb').write(w.b)
    for k, sbts in enumerate(skin_bytes):
        open(os.path.join(out_dir, '%s%02d.skin' % (stem_out, k)), 'wb').write(sbts)
    layout['hardcoded_textures'] = tex_names
    print('wrote', stem_out, len(w.b), 'bytes,', len(skin_bytes), 'skins')
    return layout

def hardcoded_name(sex, fdid):
    """path written into the m2 for a fixed (type 0) texture; hdport copies the file there."""
    if CFG.get('hardcoded_resolver'): return CFG['hardcoded_resolver'](fdid)
    sx = CFG['sexes'][sex]
    if isinstance(fdid, str) and fdid.startswith('fixed:'):
        spec = CFG['fixed_textures'][fdid[6:]]
        return '%s\\%s\\HD\\%s%s.blp' % (CFG['out_folder'], sx['folder_cap'], sx['model_cap'], spec.get('name', 'Fixed' + fdid[6:]))
    named = CFG.get('hardcoded_names', {}).get(str(fdid))
    if named: return named
    sx = CFG['sexes'][sex]
    return '%s\\%s\\HD\\%s_%d.blp' % (CFG['out_folder'], sx['folder_cap'], sx['model_cap'], fdid)

EXTRA_GEOSETS = set()      # retail geosets whose body batches draw from the skin extra texture (island_extra)

def batch_fix(b, texs, texcombos, mats, tex_index, combo_for, mat_for, sid=None):
    """b: list of the 13 M2Batch fields. returns fixed list."""
    b = list(b)
    first = texcombos[b[9]]
    if texs[first][0] in {int(x) for x in CFG.get('drop_texture_types', [])}:
        return []                             # e.g. retail accessories with no 3.3.5a texture slot
    b[8] = 1                                  # one texture per batch
    b[9] = combo_for(tex_index('extra') if sid in EXTRA_GEOSETS and texs[first][0] == 1 else tex_index(first))
    b[2] = 0                                  # shader id: plain
    b[10] = 0                                 # texture coord combo -> UV0
    m = mats[b[6]]
    if texs[first][0] == 2 and m[1] == 0 and m[0] == 0:
        m = [0, 0]
    if texs[first][0] == CFG.get('eye_glow', {}).get('texture_type', -1):
        # eyeballs. The eye texture is an atlas (see build_assets.write_eye):
        #   UV0 -> left quarter: original eye art + original alpha (base pass, lit, alpha key as modern)
        #   UV1 -> glow map (black for natural eyes) drawn unlit + additive (One, One)
        b[6] = mat_for(m)
        g = list(b)
        g[6] = mat_for([0x1 | 0x10, 3])        # unlit, no depth write, blend NoAlphaAdd
        g[7] = 1                                # material layer 1
        g[10] = 1                               # texture coord combo 1 -> UV1
        return [b, g]
    b[6] = mat_for(m)
    return [b]

def build_skin(s, V, bonecombos, rule, lookup_out, max_bones, fixb, halo=None, mat_for=None):
    cvl, ovl = arr(s, 4); vl = np.frombuffer(s, '<u2', cvl, ovl)
    cil, oil = arr(s, 12); il = np.frombuffer(s, '<u2', cil, oil)
    nsm, osm = arr(s, 28); nb, ob = arr(s, 36)
    subs = [struct.unpack_from('<HHHHHHHHHH3f3ff', s, osm+48*i) for i in range(nsm)]
    batches = [list(struct.unpack_from('<BBHHHHHHHHHHH', s, ob+24*i)) for i in range(nb)]
    out_vl, out_il, out_props, out_subs, out_batches = [], [], [], [], []
    maxb = 0
    bw = V['w'].astype(np.int32); bi = V['b'].astype(np.int32)
    for si, sm in enumerate(subs):
        new_ids = rule(sm[0])
        if not new_ids: continue
        st = sm[4] + (sm[1] << 16)
        tri = vl[il[st:st+sm[5]]].reshape(-1, 3).astype(np.int64)   # global vertex ids
        if TRI_REMAP:                                                 # retail1024: corner triangles on copied vertices
            tri = np.array([TRI_REMAP.get(t, t) for t in map(tuple, tri.tolist())], np.int64).reshape(-1, 3)
        # greedy split by bone palette
        pieces = []; cur = []; pal = set()
        for t in tri:
            need = set()
            for v in t:
                for k in range(4):
                    if bw[v, k]: need.add(int(bi[v, k]))
            if len(pal | need) > max_bones and cur:
                pieces.append((cur, pal)); cur = []; pal = set()
            cur.append(t); pal |= need
        if cur: pieces.append((cur, pal))
        for nid in new_ids:
            for ptris, ppal in pieces:
                pal_l = sorted(ppal); pidx = {b_: k for k, b_ in enumerate(pal_l)}
                pal_off = find_or_add(lookup_out, pal_l)
                vmap = {}; vstart = len(out_vl); istart = len(out_il)
                for t in ptris:
                    for v in t:
                        v = int(v)
                        if v not in vmap:
                            vmap[v] = len(out_vl); out_vl.append(v)
                            out_props.append(bytes(pidx[int(bi[v, k])] if bw[v, k] else 0 for k in range(4)))
                        out_il.append(vmap[v])
                vcount = len(out_vl) - vstart; icount = len(out_il) - istart
                maxb = max(maxb, len(pal_l))
                # center bone: keep original if in palette
                newsub = (nid, istart >> 16, vstart, vcount, istart & 0xFFFF, icount,
                          len(pal_l), pal_off, sm[8], sm[9]) + tuple(sm[10:])
                if vstart > 0xFFFF: raise ValueError('skin vertex list exceeds 65535')
                out_subs.append(newsub)
                for b in batches:
                    if b[3] == si:
                        for nb_ in fixb(b, sm[0]):
                            nb_[3] = nb_[4] = len(out_subs) - 1
                            out_batches.append(nb_)
                            if sm[0] == CFG.get('eye_glow', {}).get('geoset', -1) and nb_[7] == 0: eye_tmpl = list(nb_)
    if halo:
        # soft glow cards in front of the eyes: unlit, two-sided, additive, UV1 into the atlas halo
        pal_l = sorted({int(bi[v, k]) for t in halo['tris'] for v in t for k in range(4) if bw[v, k]})
        pidx = {b_: k for k, b_ in enumerate(pal_l)}; pal_off = find_or_add(lookup_out, pal_l)
        vmap = {}; vstart = len(out_vl); istart = len(out_il)
        for t in halo['tris']:
            for v in t:
                if v not in vmap:
                    vmap[v] = len(out_vl); out_vl.append(v)
                    out_props.append(bytes(pidx[int(bi[v, k])] if bw[v, k] else 0 for k in range(4)))
                out_il.append(vmap[v])
        cpos = V['p'][list(vmap)].mean(0)
        out_subs.append((0, istart >> 16, vstart, len(out_vl) - vstart, istart & 0xFFFF, len(out_il) - istart,
                         len(pal_l), pal_off, 4, pal_l[0], *cpos, *cpos, 0.2))
        g = list(eye_tmpl); g[3] = g[4] = len(out_subs) - 1
        g[6] = mat_for([0x1 | 0x4 | 0x10, 3]); g[7] = 0; g[10] = 1; g[1] = 1
        out_batches.append(g)
    # build file
    hdr = 48
    body = bytearray()
    def put(data):
        nonlocal body
        o = hdr + len(body); body += data
        while len(body) % 4: body.append(0)
        return o
    vlb = struct.pack('<%dH' % len(out_vl), *out_vl)
    ilb = struct.pack('<%dH' % len(out_il), *out_il)
    pb = b''.join(out_props)
    smb = b''.join(struct.pack('<HHHHHHHHHH3f3ff', *x) for x in out_subs)
    bb = b''.join(struct.pack('<BBHHHHHHHHHHH', *x) for x in out_batches)
    offs = [put(vlb), put(ilb), put(pb), put(smb), put(bb)]
    h = b'SKIN' + struct.pack('<10I', len(out_vl), offs[0], len(out_il), offs[1], len(out_vl), offs[2],
                              len(out_subs), offs[3], len(out_batches), offs[4]) + struct.pack('<I', max_bones)
    print('  skin: verts %d idx %d subs %d batches %d maxbones %d  (needs level field: %s)' % (
        len(out_vl), len(out_il), len(out_subs), len(out_batches), maxb, len(out_il) > 0xFFFF))
    return h + bytes(body)

def find_or_add(lookup, pal):
    n = len(pal)
    for o in range(0, len(lookup) - n + 1):
        if lookup[o:o+n] == pal: return o
    o = len(lookup); lookup.extend(pal); return o

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('out'); ap.add_argument('stem')
    ap.add_argument('--sex', default='male'); ap.add_argument('--animdata', required=True)
    ap.add_argument('--skins', nargs='+', required=True)
    ap.add_argument('--max-bones', type=int, default=64)
    ap.add_argument('--layout-json')
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(__file__))
    from dbc import DBC
    ids = {r[0] for r in DBC(a.animdata).rows()}
    lay = convert(a.src, a.out, a.stem, ids, a.skins, a.max_bones, a.sex)
    if a.layout_json: json.dump(lay, open(a.layout_json, 'w'), indent=1)
