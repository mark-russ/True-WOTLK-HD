#!/usr/bin/env python3
"""Independent 3.3.5a-format checker + preview renderer for a converted race.
Reads ONLY the output files (m2/skin/DBC/BLP) the way the 3.3.5a client would lay them out.

  python3 lib/verify.py <build/patch> <race> <male|female> <skin,face,hair,haircolour,facial> <out.png>
  (race = a name from races/*.json; ZOOM=1 for a head close-up, LIGHT=<factor>)"""
import json, struct, sys, os, math
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
from dbcmerge import DBC
from render import render
from blp import load_blp

ROOT = sys.argv[1]; RACE = sys.argv[2]; SEX = sys.argv[3]
CHOICE = [int(x) for x in sys.argv[4].split(',')]  # skin,face,hair,haircol,facial
OUTPNG = sys.argv[5]
CFG = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'races', RACE + '.json')))
SX = CFG['sexes'][SEX]; RACE_ID = CFG['race_id']; sexid = SX['sex']
MODEL_DIR = CFG['out_folder'] + '\\' + SX['folder_cap']
def ci(p):
    cur = ROOT
    for x in p.replace('\\', '/').split('/'):
        cur = os.path.join(cur, next(e for e in os.listdir(cur) if e.lower() == x.lower()))
    return cur
md = open(ci('%s\\%s.m2' % (MODEL_DIR, SX['model_cap'])), 'rb').read()
assert md[:4] == b'MD20' and struct.unpack_from('<I', md, 4)[0] == 264
A = lambda o: struct.unpack_from('<II', md, o)
errors = []
def chk(cond, msg):
    if not cond: errors.append(msg)
for name, off, size in [('seq', 28, 64), ('seqlk', 36, 2), ('bones', 44, 88), ('keybone', 52, 2), ('verts', 60, 48),
                        ('tex', 80, 16), ('transp', 88, 20), ('texanim', 96, 60), ('texrep', 104, 2), ('mats', 112, 4),
                        ('bonelk', 120, 2), ('texlk', 128, 2), ('texunit', 136, 2), ('transplk', 144, 2), ('uvlk', 152, 2),
                        ('attach', 240, 40), ('attlk', 248, 2), ('events', 256, 36), ('cams', 272, 100), ('camlk', 280, 2)]:
    n, o = A(off); chk(n == 0 or o + n*size <= len(md), name + ' out of range')
nseq, oseq = A(28)
seqs = [struct.unpack_from('<HHIfIhHIIIfffffffhH', md, oseq+64*i) for i in range(nseq)]
nb, obn = A(44); nv, ov = A(60)
VDT = np.dtype([('p', '<3f4'), ('w', '4u1'), ('b', '4u1'), ('n', '<3f4'), ('t0', '<2f4'), ('t1', '<2f4')])
V = np.frombuffer(md, VDT, nv, ov)
chk(V['b'].max() < nb, 'vertex bone index >= bone count')
def track(off, vs, base=False):
    interp, gseq, tn, to = struct.unpack_from('<hhII', md, off)
    out = []
    vn, vo = (0, 0) if base else struct.unpack_from('<II', md, off+12)
    chk(base or tn == vn, 'track ts/value count mismatch @%x' % off)
    for i in range(tn):
        c, o = A(to + 8*i)
        ts = np.frombuffer(md, '<u4', c, o) if c else np.zeros(0, np.uint32)
        chk(c == 0 or o + 4*c <= len(md), 'ts oob'); chk(np.all(np.diff(ts.astype(np.int64)) >= 0), 'ts not sorted @%x' % off)
        vals = b''
        if not base:
            c2, o2 = A(vo + 8*i); chk(c2 == c, 'key count mismatch'); chk(c2 == 0 or o2 + vs*c2 <= len(md), 'val oob')
            vals = md[o2:o2+vs*c2]
        out.append((ts, vals))
    chk(gseq >= 0 or tn <= nseq, 'track has more sub-arrays than sequences')
    return interp, gseq, out
bones = []
for i in range(nb):
    o = obn + 88*i
    kb, fl, par, sub = struct.unpack_from('<iIhH', md, o)
    chk(par < i, 'bone parent order')
    bones.append(dict(par=par, t=track(o+16, 12), r=track(o+36, 8), s=track(o+56, 12),
                      pivot=np.array(struct.unpack_from('<3f', md, o+76))))
for i in range(A(240)[0]): track(A(240)[1] + 40*i + 20, 1)
for i in range(A(256)[0]): track(A(256)[1] + 36*i + 24, 0, True)
for i in range(A(272)[0]):
    o = A(272)[1] + 100*i; track(o+16, 36); track(o+48, 36); track(o+80, 12)
nt, ot = A(80); textypes = []; texnames = {}
for i in range(nt):
    t, f, nn, no = struct.unpack_from('<IIII', md, ot+16*i); textypes.append(t)
    if t == 0:
        nm = md[no:no+nn-1].decode(); texnames[i] = nm
        try: ci(nm)
        except StopIteration: errors.append('missing ' + nm)
ntl, otl = A(128); texlk = struct.unpack_from('<%dH' % ntl, md, otl)
nbl, obl = A(120); bonelk = struct.unpack_from('<%dH' % nbl, md, obl)
chk(max(bonelk) < nb, 'bone lookup oob')
# ---------------------------------------------------------------- skins
skins = []
for k in range(struct.unpack_from('<I', md, 68)[0]):
    s = open(ci('%s\\%s%02d.skin' % (MODEL_DIR, SX['model_cap'], k)), 'rb').read()
    SA = lambda o: struct.unpack_from('<II', s, o)
    c, o = SA(4); vl = np.frombuffer(s, '<u2', c, o); chk(vl.max() < nv, 'skin vertex oob')
    c, o = SA(12); il = np.frombuffer(s, '<u2', c, o); chk(il.max() < len(vl), 'skin index oob')
    c, o = SA(20); props = np.frombuffer(s, 'u1', c*4, o).reshape(-1, 4)
    nsm, osm = SA(28); nbt, obt = SA(36)
    subs = [struct.unpack_from('<HHHHHHHHHH', s, osm+48*i) for i in range(nsm)]
    for sm in subs:
        st = sm[4] + (sm[1] << 16)
        chk(st + sm[5] <= len(il), 'submesh idx range')
        idx = il[st:st+sm[5]]
        chk(idx.min() >= sm[2] and idx.max() < sm[2] + sm[3], 'submesh indices outside vertex range')
        for v in range(sm[2], sm[2]+sm[3]):
            g = V['b'][vl[v]]; w = V['w'][vl[v]]
            for j in range(4):
                if w[j]: chk(bonelk[int(sm[7]) + int(props[v][j])] == g[j], 'palette mismatch')
    bt = [struct.unpack_from('<BBHHHHHHHHHHH', s, obt+24*i) for i in range(nbt)]
    for b in bt: chk(b[3] < nsm and b[9] < ntl and b[6] < A(112)[0], 'batch refs')
    skins.append((vl, il, subs, bt))
print('sequences', nseq, 'bones', nb, 'verts', nv, 'textures', textypes)
print('CHECKS:', 'OK' if not errors else '%d problems' % len(errors), errors[:10])

# ---------------------------------------------------------------- pose (Stand, t=0) and skin
def q16(v): return np.array([(x + 32768) / 32767.0 - 1.0 if x >= 0 else (x + 32767) / 32767.0 for x in v])
def sample(tr, si, t, vs, kind):
    interp, gseq, subs = tr
    sub = subs[0] if gseq >= 0 and subs else (subs[si] if si < len(subs) else None)
    if not sub or not len(sub[0]): return None
    ts, vals = sub; k = max(0, np.searchsorted(ts, t, 'right') - 1)
    if kind == 'q':
        x, y, z, w = struct.unpack_from('<4h', vals, 8*k)
        q = np.array([((c + 32768) if c < 0 else (c - 32767)) / 32767.0 for c in (x, y, z, w)]); return q/np.linalg.norm(q)
    return np.array(struct.unpack_from('<3f', vals, 12*k))
def qmat(q):
    x, y, z, w = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)], [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)], [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
stand = [i for i, s in enumerate(seqs) if s[0] == 0][0]
Ms = []
for b in bones:
    T = np.eye(4); p = b['pivot']
    tr = sample(b['t'], stand, 300, 12, 'v'); ro = sample(b['r'], stand, 300, 8, 'q'); sc = sample(b['s'], stand, 300, 12, 'v')
    L = np.eye(4)
    if tr is not None: L[:3, 3] = tr
    R = np.eye(4)
    if ro is not None: R[:3, :3] = qmat(ro)
    S_ = np.eye(4)
    if sc is not None: S_[:3, :3] = np.diag(sc)
    P = np.eye(4); P[:3, 3] = p; Pi = np.eye(4); Pi[:3, 3] = -p
    M = P @ L @ R @ S_ @ Pi
    if b['par'] >= 0: M = Ms[b['par']] @ M
    Ms.append(M)
Ms = np.array(Ms)
P4 = np.concatenate([V['p'], np.ones((nv, 1), np.float32)], 1)
W = V['w'].astype(np.float64) / 255.0
pos = np.zeros((nv, 3))
for j in range(4):
    pos += W[:, j:j+1] * np.einsum('nij,nj->ni', Ms[V['b'][:, j]], P4)[:, :3]
N = V['n'].astype(np.float64)

# ---------------------------------------------------------------- customization via DBC
P = os.path.join(ROOT, 'DBFilesClient')
cs = DBC(P + '/CharSections.dbc'); hg = DBC(P + '/CharHairGeosets.dbc'); fh = DBC(P + '/CharacterFacialHairStyles.dbc')
skin, face, hair, hcol, facial = CHOICE
def sec(t, var, col):
    for r in cs.rows:
        if r[1] == RACE_ID and r[2] == sexid and r[3] == t and r[8] == var and r[9] == col:
            return [cs.str(r[i]) for i in (4, 5, 6)]
    raise SystemExit('no CharSections row %s' % ((t, var, col),))
def img(p, size=None):
    im = load_blp(ci(p))
    return im.resize(size, Image.LANCZOS) if size and im.size != size else im
sk = sec(0, 0, skin); fc = sec(1, face, skin); hr = sec(3, hair, hcol); uw = sec(4, 0, skin); fh_ = sec(2, facial, hcol)
comp = img(sk[0]).copy()
if comp.size[0] == 1024:   # retail1024: the client composites only inside its regions; the rest stays black
    comp.paste((0, 0, 0, 255), (0, 320, 256, 512)); comp.paste((0, 0, 0, 255), (0, 512, 1024, 1024))
# face regions: stock 512 layout, or retail1024 (patch_component_layout.py) when the skin is 1024x1024
FL, FU = ((512, 256, 512, 256), (512, 0, 512, 256)) if comp.size[0] == 1024 else ((0, 384, 256, 128), (0, 320, 256, 64))
for p, rect in [(fc[0], FL), (fc[1], FU),
                (fh_[0], FL), (fh_[1], FU), (uw[0], (256, 192, 256, 128)),
                (uw[1], (256, 0, 256, 128)), (hr[1], FL), (hr[2], FU)]:
    if p: comp.alpha_composite(img(p, rect[2:]), rect[:2])
comp.save(OUTPNG.replace('.png', '_tex.png'))
tex = {1: np.asarray(comp, np.float32)}
if sk[1]: tex[8] = np.asarray(img(sk[1]), np.float32)
if hr[0]: tex[6] = np.asarray(img(hr[0]), np.float32)
def tex_for(ti):
    """texture for texture index ti: replaceable by type, fixed (type 0) by its file name"""
    if textypes[ti] == 0:
        if ti not in tex: tex[ti] = np.asarray(img(texnames[ti]), np.float32)
        return tex[ti]
    return tex.get(textypes[ti])
hgeo = [r[4] for r in hg.rows if r[1] == RACE_ID and r[2] == sexid and r[3] == hair][0]
frow = [r for r in fh.rows if r[0] == RACE_ID and r[1] == sexid and r[2] == facial][0]
show = {0, hgeo, 100 + frow[3], 300 + frow[4], 200 + frow[5], 401, 501, 701, 901, 1301, 1801}
vl, il, subs, bt = skins[0]
nmat, omat = A(112); mats = [struct.unpack_from('<HH', md, omat+4*i) for i in range(nmat)]
ntu, otu = A(136); texunit = struct.unpack_from('<%dh' % ntu, md, otu)
groups = {}
for b in bt:
    sm = subs[b[3]]
    if sm[0] not in show: continue
    tt = texlk[b[9]]
    if tex_for(tt) is None: continue
    flags, blend = mats[b[6]]
    mode = 'add' if blend in (3, 4) else 'lit'
    uvset = texunit[b[10]]
    st = sm[4] + (sm[1] << 16)
    groups.setdefault((tt, uvset, mode), []).append(vl[il[st:st+sm[5]]].reshape(-1, 3))
print('draw groups:', {k: sum(len(x) for x in v) for k, v in groups.items()})
ims = []
allp = pos[vl]
from render import render_layers
ZOOM = os.environ.get('ZOOM')
for view in (('front',) if ZOOM else ('front', 'side', 'back')):
    if view == 'side': bb = (allp[:, 0].min(), allp[:, 0].max(), allp[:, 2].min(), allp[:, 2].max())
    else: bb = (-allp[:, 1].max(), -allp[:, 1].min(), allp[:, 2].min(), allp[:, 2].max())
    if ZOOM:   # head close-up
        hz = allp[:, 2].max(); bb = (-0.45, 0.45, hz - 0.75, hz + 0.05)
    lays = [dict(tex=tex_for(k[0]), tris=np.concatenate(v), uv=(V['t1'] if k[1] == 1 else V['t0']), mode=k[2]) for k, v in groups.items()]
    ims.append(render_layers(pos, N, lays, 420, 640 if not ZOOM else 420, view, bb, float(os.environ.get('LIGHT', 1))))
out = Image.new('RGB', (420*len(ims), ims[0].size[1])); [out.paste(im, (420*i, 0)) for i, im in enumerate(ims)]
out.save(OUTPNG)
print('rendered', OUTPNG, 'geosets', sorted(show))
