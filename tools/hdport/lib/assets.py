"""Textures + customization rows for one race/sex, driven by a race config (races/*.json).

The five 3.3.5a character-create slots and what each one can be fed from:
  skin        one option whose choices carry the base skin (+ optional 'extra' and underwear)
  face        one option, textures per skin choice (modern 'related' elements)
  hair_style  one option, geoset group 0
  hair_color  mode 'eyes': an eye colour option -> eyeball texture (type 6) + eye paint (scalp layers)
              mode 'hair': a hair colour option -> hair texture (type 6) + scalp layers, per hair style
  facial      cartesian product of N options, each feeding one or more of the facial geoset groups 1/2/3,
              optionally with a face-area texture per hair colour (CharSections section 2)
Textures the 3.3.5a model has no replaceable slot for (e.g. retail eyes, type 19, on a race whose hair
slot is taken by real hair) can be baked in as fixed textures: config 'fixed_textures'.
"""
import json, os, shutil
import numpy as np
from PIL import Image
from blp import save_blp, save_blp_pal

def islands(lay):
    """relocated body UV pieces: [{'src': bbox, 'scale': s, 'dst': [x, y]}] (one, or several when packed)"""
    if lay.get('islands'): return lay['islands']
    if lay.get('island_scale') and lay['island_src'][2] > lay['island_src'][0]:
        return [{'src': lay['island_src'], 'scale': lay['island_scale'], 'dst': lay['island_dst']}]
    return []

class Builder:
    def __init__(self, cfg, tables, files, out_root, report):
        self.cfg, self.t, self.files, self.out, self.report = cfg, tables, files, out_root, report

    # ---------------------------------------------------------------- paths
    def wpath(self, p):
        """DBC/m2 path -> file on disk, mapping folder prefixes to the case used on disk."""
        for k, v in self.cfg.get('disk_folders', {}).items():
            if p.lower().startswith(k.lower() + '\\'):
                return os.path.join(self.out, *v.split('/'), *p[len(k)+1:].split('\\'))
        return os.path.join(self.out, *p.split('\\'))

    def save(self, img, p, alpha=False):
        """composited textures: palettized BLP2 (the only thing 3.3.5a's compositor reads)."""
        w = self.wpath(p)
        if os.environ.get('SKIP_EXISTING') and os.path.exists(w): return
        os.makedirs(os.path.dirname(w), exist_ok=True)
        save_blp_pal(img, w, alpha)

    def copy(self, src, p):
        w = self.wpath(p); os.makedirs(os.path.dirname(w), exist_ok=True); shutil.copyfile(src, w)

    def copy_tex(self, src, p):
        """direct (non-composited) texture: copied as-is when 3.3.5a can read it (BLP2, palette /
        DXT / ARGB8888), otherwise decoded and re-saved as DXT5."""
        with open(src, 'rb') as f: head = f.read(12)
        if head[:4] == b'BLP2' and head[8] in (1, 2, 3):
            return self.copy(src, p)
        w = self.wpath(p); os.makedirs(os.path.dirname(w), exist_ok=True)
        save_blp(Image.open(src).convert('RGBA'), w, True)
        self.report.append('%s: re-encoded (format %r not readable by 3.3.5a)' % (os.path.basename(src), head[8:9]))

    @staticmethod
    def overlay_area(src, lay):
        """modern face-section overlay (scalp, beard stubble, ...) -> 3.3.5a face area 256x192 RGBA.
        Takes the face half of a full-layout (2:1) texture. The rectangle the relocated body UV island
        occupies is cleared so the overlay can't paint over it."""
        im = Image.open(src).convert('RGBA')
        if im.size[0] == 2 * im.size[1]: im = im.crop((im.size[0] // 2, 0, im.size[0], im.size[1]))
        r1024 = lay.get('retail1024')
        if r1024: im = im.resize((512, 512), Image.LANCZOS) if im.size != (512, 512) else im
        else: im = im.resize((256, 192), Image.LANCZOS)
        ox, oy = (512, 0) if r1024 else (0, 320)          # where the face area sits in the layout
        if islands(lay):
            a = np.asarray(im).copy()
            for p in islands(lay):
                x0, y0, x1, y1 = p['src']; s = p['scale']; dx, dy = p['dst']
                a[max(int(dy) - oy - 2, 0):int(dy - oy + (y1 - y0) * s) + 3,
                  max(int(dx) - ox - 2, 0):int(dx - ox + (x1 - x0) * s) + 3, 3] = 0
            im = Image.fromarray(a, 'RGBA')
        return im

    def face_image(self, src, tints):
        """retail face texture, with any full-texture skin layers that also cover the face blended in
        (their face half: the right half of a 2:1 full-layout texture)"""
        im = Image.open(src)
        for p, mode in tints:
            ov = Image.open(p)
            if ov.size[0] == 2 * ov.size[1]: ov = ov.crop((ov.size[0] // 2, 0, ov.size[0], ov.size[1]))
            im = self.blend_layer(im, ov, mode)
        return im

    @staticmethod
    def blend_layer(base, layer, mode):
        """composite a full-texture retail layer onto base with its ChrModelTextureLayer blend mode:
        4 multiply, 6 overlay, 7 screen, anything else plain alpha. The result is mixed in by the
        layer's alpha; base alpha is kept. Overlay is the standard one (darken or lighten chosen by
        the BASE): wow.export's shader chooses by the layer (hard light), which turns light skins with
        coloured tints green (human female skins 14/15); on dark skins the two agree."""
        b = np.asarray(base.convert('RGBA'), np.float32) / 255
        t = np.asarray(layer.convert('RGBA').resize(base.size, Image.LANCZOS), np.float32) / 255
        bc, tc, a = b[..., :3], t[..., :3], t[..., 3:4]
        if mode == 4: rgb = bc * tc
        elif mode == 6: rgb = np.where(bc < 0.5, 2 * bc * tc, 1 - 2 * (1 - bc) * (1 - tc))
        elif mode == 7: rgb = 1 - (1 - bc) * (1 - tc)
        else: rgb = tc
        out = b.copy(); out[..., :3] = bc * (1 - a) + rgb * a
        im = Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), 'RGBA')
        return im if base.mode == 'RGBA' else im.convert(base.mode)

    @staticmethod
    def face_halves(img, lay):
        """face-area image -> (upper, lower): stock 256x192 -> 256x64 / 256x128; retail1024 512x512 -> 512x256 / 512x256"""
        if lay.get('retail1024'): return img.crop((0, 0, 512, 256)), img.crop((0, 256, 512, 512))
        return img.crop((0, 0, 256, 64)), img.crop((0, 64, 256, 192))

    def save_face_overlay(self, img, upper, lower):
        """face-area overlay -> the Upper / Lower pair, with alpha."""
        u, l = self.face_halves(img, self.lay)
        self.save(u, upper, True); self.save(l, lower, True)

    # ---------------------------------------------------------------- texture helpers
    @staticmethod
    def face_area(face, base, lay):
        """modern face section -> 3.3.5a face area (256x192, = FaceUpper + FaceLower at x2) with the
        relocated UV island pasted in. Works on any modern resolution with the 2:1 HD layout."""
        r1024 = lay.get('retail1024')
        if r1024:                        # patched client: the face square at full size
            fa = face.convert('RGBA').resize((512, 512), Image.LANCZOS) if face.size != (512, 512) else face.convert('RGBA')
            if not islands(lay): return fa
        base = base.convert('RGBA').resize((1024, 512), Image.LANCZOS) if base.size != (1024, 512) else base.convert('RGBA')
        if not r1024: fa = face.convert('RGBA').resize((256, 192), Image.LANCZOS)
        ox, oy = (512, 0) if r1024 else (0, 320)          # where the face area sits in the layout
        for p in islands(lay):
            x0, y0, x1, y1 = p['src']; s = p['scale']; dx, dy = p['dst']
            m = 3
            box = (int(max(x0 - m, 0)), int(max(y0 - m, 0)), int(min(x1 + m + 1, 512 if r1024 else 256)), int(min(y1 + m + 1, 512)))
            isl = base.crop(box)
            isl = isl.resize((max(1, round(isl.size[0]*s)), max(1, round(isl.size[1]*s))), Image.LANCZOS)
            fa.paste(isl, (round(dx - (x0 - box[0])*s) - ox, round(dy - (y0 - box[1])*s) - oy))
        return fa

    def write_eye(self, src, p, glow, surface, halo):
        """Eye atlas 512x512 (direct texture, DXT5); see m2conv eye_glow for the UV side.
             (0,0)-(256,128)      original eye art + original alpha     -> base pass (UV0)
             (0,256)-(256,384)    iris glow map, black if not glowing   -> unlit additive pass (UV1)
             (320,320)-(448,448)  soft radial halo, black if not glowing -> glow cards"""
        im = Image.open(src).convert('RGBA')
        if im.size != (256, 128): im = im.resize((256, 128), Image.LANCZOS)
        a = np.asarray(im).astype(np.float32)
        atlas = np.zeros((512, 512, 4), np.float32); atlas[..., 3] = 255
        atlas[0:128, 0:256] = a
        if glow:
            rgb = a[..., :3]; L = rgb.max(-1)
            sel = L[a[..., 3] > 250]
            lo, hi = np.percentile(sel, 50), np.percentile(sel, 99.5)
            m = np.clip((L - lo) / max(hi - lo, 1), 0, 1) ** 1.2
            atlas[256:384, 0:256, :3] = rgb * m[..., None] * surface
            top = rgb[L >= np.percentile(sel, 97)].mean(0)
            tint = top / max(top.max(), 1)
            yy, xx = np.mgrid[0:128, 0:128]
            r = np.hypot((xx + 0.5 - 64) / 60, (yy + 0.5 - 64) / 60)
            g = np.clip(1 - r, 0, 1) ** 2.4
            atlas[320:448, 320:448, :3] = tint * 255 * g[..., None] * halo
        w = self.wpath(p); os.makedirs(os.path.dirname(w), exist_ok=True)
        save_blp(Image.fromarray(np.clip(atlas, 0, 255).astype(np.uint8), 'RGBA'), w, True)

    def choices(self, o):
        """choices of option o in order; config choice_filter can drop non-player choices (retail
        requirement types, e.g. NPC-only = 2), move DK-only choices after the normal ones (dk_last)
        and NPC-only ones (npc_req_types) to the very end (npc_last)"""
        ch = self.t.choices(o)
        cf = self.cfg.get('choice_filter')
        if not cf: return ch
        drop = set(cf.get('drop_req_types', []))
        ch = [c for c in ch if self.t.req_type(c) not in drop]
        if cf.get('dk_last'):
            ch = [c for c in ch if not self.t.dk_only(c)] + [c for c in ch if self.t.dk_only(c)]
        if cf.get('npc_last'):
            # the 3.3.5a character screen does not skip unselectable entries: it counts the selectable
            # ones and steps through indices 0..count-1, so selectable choices must come first
            npc = set(cf.get('npc_req_types', []))
            ch = [c for c in ch if self.t.req_type(c) not in npc] + [c for c in ch if self.t.req_type(c) in npc]
        return ch

    # ---------------------------------------------------------------- one sex
    def build(self, sexname, lay):
        cfg, t = self.cfg, self.t
        sx = cfg['sexes'][sexname]; model = sx['chr_model']; slots = dict(cfg['slots'], **sx.get('slots', {}))
        self.lay = lay
        M = '%s\\%s\\HD\\%s' % (cfg['out_folder'], sx['folder_cap'], sx['model_cap'])
        rows = {'skin': [], 'face': [], 'hair': [], 'facial': [], 'underwear': [],
                'hairgeo': [], 'facialstyles': [], 'barber': []}
        el = lambda c: t.elements(c, self.files)
        def opt(name):
            o = t.option(model, name) if name else None
            if name and o is None: self.report.append('%s %s: no option named %r' % (cfg['name'], sexname, name))
            return o
        choices = self.choices
        if (slots['face'].get('sets') or slots['hair_color'].get('sets') or slots['facial'].get('respect_requirements')) \
                and not t.req_choices:
            raise SystemExit('%s: config uses sets / respect_requirements but ChrCustomizationReqChoice.csv is not in --tables'
                             % cfg['name'])
        # ---------------- skin
        sk = slots['skin']; furs = choices(opt(sk['option'])); fur_index = {}; base_imgs = {}
        face_tints = {}       # skin index -> [(layer file, blend)] also applied to that skin's faces
        for ci, c in enumerate(furs):
            fur_index[int(c['ID'])] = ci
            tx = {k: v for rel, k, v in el(c) if not rel}
            if not tx.get('tex%d' % sk['base']):
                raise SystemExit('%s %s: body texture of %s %r is not in the export. Without --listfile only files named\n'
                                 'by ID (<name>_<fileDataID>.blp) or listed in a wow.export manifest are found: pass --listfile,\n'
                                 'or check what is missing with: inspect_race.py --race %s --tables ... --export ... --extract-list list.txt'
                                 % (cfg['name'], sexname, sk['option'], c['Name_lang'] or '#' + c['ID'], cfg['name']))
            base = Image.open(tx['tex%d' % sk['base']])
            if base.size != (1024, 512): base = base.resize((1024, 512), Image.LANCZOS)
            for ov in sk.get('overlays', []):
                if isinstance(ov, dict) and set(ov) - {'target', 'blend', 'faces', '_comment'}:
                    raise SystemExit('%s: unknown key(s) %s in skin overlays' % (cfg['name'], sorted(set(ov) - {'target', 'blend', 'faces', '_comment'})))
                target, blend = (ov, 15) if isinstance(ov, int) else (ov['target'], ov.get('blend', 15))
                src = tx.get('tex%d' % target)
                if src:
                    base = self.blend_layer(base, Image.open(src), blend)
                    if isinstance(ov, dict) and ov.get('faces'): face_tints.setdefault(ci, []).append((src, blend))
                    self.report.append('%s %s: skin %d: layer %d blended (mode %d) into the body%s'
                                       % (cfg['name'], sexname, ci, target, blend,
                                          ' and its faces' if isinstance(ov, dict) and ov.get('faces') else ''))
            base_imgs[ci] = base
            if lay.get('retail1024'):
                # patched client: the retail layout in the top half of 1024x1024; the unused bottom half
                # repeats the last row so filtering at the face / body bottom edge sees no seam
                b2 = base.convert('RGBA'); wl = Image.new('RGBA', (1024, 1024))
                wl.paste(b2, (0, 0)); wl.paste(b2.crop((0, 511, 1024, 512)).resize((1024, 512)), (0, 512))
            else:
                wl = Image.new('RGBA', (512, 512))
                wl.paste(base.convert('RGBA').crop((0, 0, 512, 512)), (0, 0))
                wl.paste(self.face_area(base.crop((512, 0, 1024, 512)), base, lay), (0, 320))
            p = '%sSkin00_%02d.blp' % (M, ci); self.save(wl.convert('RGB'), p)
            pe = ''
            if sk.get('extra') and tx.get('tex%d' % sk['extra']):
                pe = '%sSkin00_%02d_Extra.blp' % (M, ci); self.copy(tx['tex%d' % sk['extra']], pe)
            if lay.get('island_extra'):
                # island_extra: the relocated body corner (tail etc.) at full size, as this skin's extra texture
                if pe: raise SystemExit('%s %s: island_extra needs the skin extra texture, but %s fills it' % (cfg['name'], sexname, sk['option']))
                ex = Image.new('RGBA', (256, 256), (0, 0, 0, 255))     # opaque: the retail body alpha is not skin opacity
                ex.paste(base.convert('RGB').convert('RGBA').crop(tuple(lay['island_extra'])), (0, 0))
                pe = '%sSkin00_%02d_Extra.blp' % (M, ci); w = self.wpath(pe)
                os.makedirs(os.path.dirname(w), exist_ok=True); save_blp(ex, w, True)
            uw = []
            for target, name in zip(sk.get('underwear', []), ('NakedPelvisSkin', 'NakedTorsoSkin')):
                src = tx.get('tex%d' % target)
                if src:
                    pp = '%s%s00_%02d.blp' % (M, name, ci); self.save(Image.open(src).convert('RGBA'), pp, True); uw.append(pp)
                else: uw.append('')
            flags = 5 if t.dk_only(c) else 17
            if t.req_type(c) in cfg.get('choice_filter', {}).get('npc_req_types', []): flags = 8   # NPC-only, as stock
            rows['skin'].append([0, ci, p, pe, '', flags])
            rows['underwear'].append([0, ci, uw[0] if uw else '', uw[1] if len(uw) > 1 else '', '', 17])
        nfur = len(furs)
        # ---------------- faces
        fc = slots['face']; faces = choices(opt(fc['option'])); art = []
        for f in faces:
            per = {}
            for rel, k, v in el(f):
                if k == 'tex%d' % fc['target'] and v:
                    if rel in fur_index: per[fur_index[rel]] = v
                    elif not rel: per.setdefault('any', v)
            if 'any' in per:
                for ci in range(nfur): per.setdefault(ci, per['any'])
                del per['any']
            art.append(per)
        nfb = nbare = 0
        nfaces = len(faces)
        if fc.get('sets'):
            # slider index i on skin s = the i-th face of the face list skin s is limited to
            groups, skin_set = [], {}
            for ci, c in enumerate(furs):
                a = t.allowed(c)
                lst = [fi for fi, f in enumerate(faces) if a and int(f['ID']) in a]
                if lst:
                    if lst not in groups: groups.append(lst)
                    skin_set[ci] = groups.index(lst)
            if not groups: groups = [list(range(len(faces)))]
            for ci in range(nfur):       # unrestricted skins (e.g. DK): the face list with most art for them
                if ci not in skin_set:
                    skin_set[ci] = max(range(len(groups)), key=lambda g: (sum(1 for fi in groups[g] if ci in art[fi]), -g))
            nfaces = max(len(g) for g in groups)
            for slot in range(nfaces):
                for ci in range(nfur):
                    g = groups[skin_set[ci]]; fi = g[min(slot, len(g) - 1)]
                    src = art[fi].get(ci)
                    if src is None:
                        cand = [fj for fj in g if ci in art[fj]] or [fj for fj in range(len(faces)) if ci in art[fj]]
                        src = art[min(cand, key=lambda j: abs(g.index(j) - slot) if j in g else 1000 + abs(j - fi))][ci] if cand \
                              else next(iter(art[fi].values()))
                        nfb += 1
                    fa = self.face_area(self.face_image(src, face_tints.get(ci, ())), base_imgs[ci], lay).convert('RGB')
                    pu = '%sFaceUpper%02d_%02d.blp' % (M, slot, ci); pl = '%sFaceLower%02d_%02d.blp' % (M, slot, ci)
                    fu, fl = self.face_halves(fa, lay); self.save(fu, pu); self.save(fl, pl)
                    rows['face'].append([slot, ci, pl, pu, '', 5 if t.dk_only(faces[fi]) else 1])
            self.report.append('%s %s: faces in %d per-skin lists (%s) -> %d face slots'
                               % (cfg['name'], sexname, len(groups), '/'.join(str(len(g)) for g in groups), nfaces))
        for fi, f in enumerate(faces if not fc.get('sets') else []):
            per = art[fi]; flags = 5 if t.dk_only(f) else 1
            for ci in range(nfur):
                src = per.get(ci)
                if src is None:
                    cand = [fj for fj in range(len(faces)) if ci in art[fj]]
                    if cand: src = art[min(cand, key=lambda j: abs(j - fi))][ci]; nfb += 1
                    else: nbare += 1     # no face art for this skin in retail: the body texture's own face half
                face = self.face_image(src, face_tints.get(ci, ())) if src else base_imgs[ci].crop((512, 0, 1024, 512))
                fa = self.face_area(face, base_imgs[ci], lay).convert('RGB')
                pu = '%sFaceUpper%02d_%02d.blp' % (M, fi, ci); pl = '%sFaceLower%02d_%02d.blp' % (M, fi, ci)
                fu, fl = self.face_halves(fa, lay); self.save(fu, pu); self.save(fl, pl)
                rows['face'].append([fi, ci, pl, pu, '', flags])
        if fc.get('dk_copies'):
            # stock 3.3.5a keeps a DK-only copy of every face after the normal ones (face n + count, flag 5);
            # existing DK characters point at those indices
            rows['face'] += [[r[0] + nfaces, r[1], r[2], r[3], r[4], 5] for r in rows['face'] if r[5] == 1]
        if nfb: self.report.append('%s %s: %d face/skin pairs have no retail art; borrowed the nearest face drawn for that skin' % (cfg['name'], sexname, nfb))
        if nbare: self.report.append('%s %s: %d face/skin pairs use the body texture\'s face half (retail has no face art for those skins)' % (cfg['name'], sexname, nbare))
        # ---------------- hair colour slot
        hc = slots['hair_color']; colors = []
        if hc['mode'] == 'eyes':
            paint = {}
            if hc.get('paint_option'):
                both = choices(opt(hc['paint_option']))[0]
                paint = {rel: v for rel, k, v in el(both) if k == 'tex%d' % hc['paint_target'] and v}
            eg = cfg.get('eye_glow', {}); glow_ids = set(eg.get('choices', []))
            skipped = 0
            for c in choices(opt(hc['option'])):
                tx = [v for rel, k, v in el(c) if k == 'tex%d' % hc['eye_target'] and not rel]
                if not tx or not tx[0]: skipped += 1; continue
                ei = len(colors)
                pe = '%s\\HD\\%sEyes%02d.blp' % (cfg['out_folder'], cfg['race_cap'], ei)
                if not os.path.exists(self.wpath(pe)) or os.environ.get('REBUILD_EYES'):
                    if eg:
                        self.write_eye(tx[0], pe, int(c['ID']) in glow_ids, eg.get('surface', 0.85), eg.get('halo', 0.55))
                    else:
                        self.copy(tx[0], pe)
                pu = pl = ''
                ov = paint.get(int(c['ID']))
                if ov:
                    o = self.overlay_area(ov, lay) if lay.get('retail1024') else Image.open(ov).convert('RGBA').resize((256, 192), Image.LANCZOS)
                    pu = '%sScalpUpperEyes%02d.blp' % (M, ei); pl = '%sScalpLowerEyes%02d.blp' % (M, ei)
                    ou, ol = self.face_halves(o, lay); self.save(ou, pu, True); self.save(ol, pl, True)
                colors.append((c, pe, pl, pu))
            if skipped: self.report.append('%s %s: %d eye colours skipped (no texture in the export)' % (cfg['name'], sexname, skipped))
        elif hc['mode'] == 'hair':
            all_colors = choices(opt(hc['option']))
            style_set = None
            if hc.get('sets'):
                # slider index i on style h = the i-th colour of the colour list style h is limited to
                groups, style_set = [], {}
                for h in choices(opt(slots['hair_style']['option'])):
                    lst = [c for c in all_colors if not t.allowed(c) or int(h['ID']) in t.allowed(c)]
                    if lst:
                        if lst not in groups: groups.append(lst)
                        style_set[int(h['ID'])] = groups.index(lst)
                if not groups: groups = [all_colors]
                style_set = {k: groups[v] for k, v in style_set.items()}
                style_set[None] = groups[0]
                n = max(len(g) for g in groups)
                all_colors = [groups[0][min(i, len(groups[0]) - 1)] for i in range(n)]
                self.report.append('%s %s: hair colours in %d per-style lists (%s) -> %d colour slots'
                                   % (cfg['name'], sexname, len(groups), '/'.join(str(len(g)) for g in groups), n))
            for c in all_colors:
                colors.append((c, None, None, None))
        else:
            raise SystemExit('hair_color mode %r not implemented' % hc['mode'])

        def pair_tex(a, b, target):
            """texture for target on choice a combined with choice b (retail puts the element on either
            side, related to the other one), falling back to an unrelated element on a, then b."""
            key = 'tex%d' % target
            for x, y in ((a, b), (b, a)):
                for rel, k, v in el(x):
                    if k == key and v and rel == int(y['ID']): return v
            for x in (a, b):
                for rel, k, v in el(x):
                    if k == key and v and not rel: return v
            return None
        written = {}
        def once(key, path, make):
            """write each distinct source (or source combination) once; later uses share the file"""
            if key not in written: make(path); written[key] = path
            return written[key]
        # ---------------- hair styles
        hs = slots['hair_style']; hairs = choices(opt(hs['option']))
        hair_missing = [0]
        def hair_pair(hi, h, ei, c):
            """mode 'hair': (hair texture, scalp lower, scalp upper) for style h in colour c"""
            if style_set is not None:
                g = style_set.get(int(h['ID']), style_set[None]); c = g[min(ei, len(g) - 1)]
            src = pair_tex(c, h, hc['hair_target'])
            if src is None:      # borrow hair and scalp together from the nearest colour drawn for this style
                near = sorted(range(len(colors)), key=lambda j: abs(j - ei))
                c = next((colors[j][0] for j in near if pair_tex(colors[j][0], h, hc['hair_target'])), c)
                src = pair_tex(c, h, hc['hair_target'])
                hair_missing[0] += 1
            pe = once(('hair', src), '%sHair%02d_%02d.blp' % (M, hi, ei), lambda p: self.copy_tex(src, p)) if src else ''
            pl = pu = ''
            # scalp layers from the style/colour pair, then fixed extra layers per colour (e.g. eyebrows)
            targets = hc.get('scalp_targets') or ([hc['scalp_target']] if hc.get('scalp_target') else [])
            srcs = [pair_tex(c, h, tg) for tg in targets]
            srcs += [pair_tex(lc, c, tg) for lc, tg in scalp_layers]
            srcs = [x for x in srcs if x]
            if srcs:
                def make(pp):
                    im = self.overlay_area(srcs[0], lay)
                    for x in srcs[1:]: im.alpha_composite(self.overlay_area(x, lay))
                    self.save_face_overlay(im, *pp)
                pu, pl = once(('scalp',) + tuple(srcs), ('%sScalpUpper%02d_%02d.blp' % (M, hi, ei), '%sScalpLower%02d_%02d.blp' % (M, hi, ei)), make)
            return pe, pl, pu
        scalp_layers = []
        for layer in hc.get('scalp_layers', []):
            ch = choices(opt(layer['option'])); pick = layer.get('choice', 0)
            lc = next((x for x in ch if x['Name_lang'] == pick), None) if isinstance(pick, str) else (ch[pick] if pick < len(ch) else None)
            if lc is None: self.report.append('%s %s: scalp layer %r choice %r not found' % (cfg['name'], sexname, layer['option'], pick)); continue
            scalp_layers.append((lc, layer['target']))
            if len(ch) > 1: self.report.append('%s %s: %s fixed to %r (%d choices not selectable in 3.3.5a)'
                                               % (cfg['name'], sexname, layer['option'], lc['Name_lang'] or pick, len(ch) - 1))
        for hi, h in enumerate(hairs):
            g = [v + hs.get('shift', 0) for rel, k, v in el(h) if k == 'geo%d' % hs.get('geoset_type', 0) and not rel]
            if g and g[0] > 0: val, scalp = cfg.get('hair_geoset_remap', {}).get(str(g[0]), g[0]), 0
            else: val, scalp = cfg.get('bald', {}).get('geoset', 1), cfg.get('bald', {}).get('showscalp', 0)
            rows['hairgeo'].append([hi, val, scalp])
            rows['barber'].append([0, hi, h['Name_lang'] or 'Style %d' % (hi+1), 1.0])
            for ei, (c, pe, pl, pu) in enumerate(colors):
                if hc['mode'] == 'hair':
                    pe, pl, pu = hair_pair(hi, h, ei, c)
                rows['hair'].append([hi, ei, pe, pl, pu, 5 if t.dk_only(c) else 17])
        if hc['mode'] == 'hair' and hair_missing[0]:
            self.report.append('%s %s: %d hair style/colour pairs have no retail hair texture; borrowed the nearest colour drawn for that style'
                               % (cfg['name'], sexname, hair_missing[0]))
        # ---------------- facial: product of options -> groups 1 / 3 / 2
        # each option spec either names one geoset ('geoset_type'/'group'/'shift') or several ('geosets': [...])
        def geo_specs(spec):
            return spec.get('geosets') or [{'type': spec['geoset_type'], 'group': spec['group'], 'shift': spec.get('shift', 0)}]
        parts = []
        for spec in slots['facial']['options']:
            o = t.option(model, spec['option'])
            if o is None:
                parts.append((spec, [('', [0] * len(geo_specs(spec)), None)])); continue
            parts.append((spec, [(c['Name_lang'],
                                  [([v for rel, k, v in el(c) if k == 'geo%d' % g['type'] and not rel] or [0])[0] for g in geo_specs(spec)],
                                  c) for c in choices(o)]))
        combos = [[]]
        for spec, vals in parts:
            combos = [cmb + [(spec, n, v, c)] for cmb in combos for n, v, c in vals]
        if slots['facial'].get('respect_requirements'):
            in_product = {int(c['ChrCustomizationOptionID']) for _, vals in parts for _, _, c in vals if c is not None}
            def allowed_combo(cmb):
                ids = {int(c['ID']) for *_, c in cmb if c is not None}
                for *_, c in cmb:
                    a = t.allowed(c) if c is not None else None
                    rel = {x for x in (a or ()) if x in t.cho and int(t.cho[x]['ChrCustomizationOptionID']) in in_product}
                    if rel and not rel & ids: return False
                return True
            n0 = len(combos); combos = [c for c in combos if allowed_combo(c)]
            if n0 != len(combos): self.report.append('%s %s: %d of %d facial combinations not allowed in retail, dropped'
                                                     % (cfg['name'], sexname, n0 - len(combos), n0))
        col = {1: 0, 3: 1, 2: 2}      # CharacterFacialHairStyles geoset columns are groups 1xx, 3xx, 2xx
        fmissing = 0
        for k, cmb in enumerate(combos):
            geo = [0, 0, 0, 0, 0]
            labels = []
            for spec, n, vs, c in cmb:
                for g, v in zip(geo_specs(spec), vs):
                    geo[col[g['group']]] = v + g.get('shift', 0)
                if n and n != spec.get('omit_label'): labels.append(spec.get('label', '{}').format(n))
            rows['facialstyles'].append([k] + geo)
            rows['barber'].append([2, k, ', '.join(labels) or slots['facial'].get('none_label') or 'Style %d' % (k+1), 0.75])
            for ei in range(len(colors)):
                srcs = []
                for spec, n, vs, c in cmb:
                    if spec.get('texture_target') and c is not None:
                        src = pair_tex(c, colors[ei][0], spec['texture_target'])
                        if src: srcs.append(src)
                        elif any(k_ == 'tex%d' % spec['texture_target'] for _, k_, _ in el(c)): fmissing += 1
                pl = pu = ''
                if srcs:
                    def make(pp, srcs=srcs):
                        im = self.overlay_area(srcs[0], lay)
                        for x in srcs[1:]: im.alpha_composite(self.overlay_area(x, lay))
                        self.save_face_overlay(im, *pp)
                    pu, pl = once(('facial',) + tuple(srcs), ('%sFacialUpper%02d_%02d.blp' % (M, k, ei),
                                                              '%sFacialLower%02d_%02d.blp' % (M, k, ei)), make)
                rows['facial'].append([k, ei, pl, pu, '', 17])
        if fmissing: self.report.append('%s %s: %d facial-hair/colour pairs have textures for other colours only; left bare'
                                        % (cfg['name'], sexname, fmissing))
        # ---------------- fixed textures the model references
        for fdid, p in lay.get('hardcoded_textures', []):
            if isinstance(fdid, str) and fdid.startswith('fixed:'):
                self.fixed_texture(sexname, model, fdid[6:], p); continue
            src = self.files.find(fdid)
            if src: self.copy(src, p)
            else: self.report.append('%s %s: fixed texture %d not in export' % (cfg['name'], sexname, fdid))
        info = dict(skins=nfur, faces=nfaces * (2 if fc.get('dk_copies') else 1), hair=len(hairs), hair_colors=len(colors), facial=len(combos))
        print('  %s: skins %d faces %d hair %d hair-colours %d facial %d' % (sexname, nfur, nfaces, len(hairs), len(colors), len(combos)))
        return rows, info

    def fixed_texture(self, sexname, model, ttype, p):
        """bake a retail replaceable texture (e.g. eyes) into a fixed one: config
        fixed_textures.<retail type> = {option, choice (index or name), target, name}"""
        spec = self.cfg['fixed_textures'][ttype]
        o = self.t.option(model, spec['option'])
        ch = self.choices(o) if o else []
        pick = spec.get('choice', 0)
        c = next((x for x in ch if x['Name_lang'] == pick), None) if isinstance(pick, str) else (ch[pick] if pick < len(ch) else None)
        src = next((v for rel, k, v in self.t.elements(c, self.files) if k == 'tex%d' % spec['target'] and v and not rel), None) if c else None
        if not src:
            self.report.append('%s %s: fixed texture for retail type %s: no texture (option %r, choice %r)'
                               % (self.cfg['name'], sexname, ttype, spec['option'], pick)); return
        self.copy_tex(src, p)
        # only a loss when the choices actually use different textures for this target
        distinct = {v for x in ch for rel, k, v in self.t.elements(x, self.files) if k == 'tex%d' % spec['target'] and v and not rel}
        if len(distinct) > 1:
            self.report.append('%s %s: %s texture fixed to %r (%d other textures not selectable in 3.3.5a)'
                               % (self.cfg['name'], sexname, spec['option'], c['Name_lang'] or '#' + c['ID'], len(distinct) - 1))
