#!/usr/bin/env python3
"""
rebake - rebuild the baked NPC textures (Textures\\BakedNpcTextures) for converted races.

Almost every humanoid NPC in 3.3.5a uses a baked texture: the finished character texture (skin, face,
facial hair, scalp, underwear and armour, composited) stored as one file and named in
CreatureDisplayInfoExtra.BakeName. The stock files were painted for the old models; on an HD model the
face and other regions land in the wrong places. This rebuilds them the way the client composites a
player character (rules read from Wow.exe 3.3.5a 12340, see LAYOUTS / ITEM_LAYERS below), from the
CharSections rows and textures hdport wrote plus the armour textures from the game data.

  python3 rebake.py --dbc <patch>/DBFilesClient <extracted client>/DBFilesClient \\
                    --data <patch> <extracted client data> --out <out> --race 1 [--race ...]
                    [--layout stock] [--only 48 ...] [--preview preview.png]

  --dbc     folders holding CharSections, ItemDisplayInfo and CreatureDisplayInfoExtra .dbc; each
            table is taken from the first folder that has it (put the hdport patch folder first).
  --data    folders the texture paths are looked up in, first match wins, case-insensitive: the hdport
            patch folder first, then an extraction of the client's MPQs (at least
            Item\\TextureComponents, plus Character\\ for races hdport didn't write).
  --out     baked files go to <out>/Textures/BakedNpcTextures/<BakeName>; copy into the patch MPQ folder.
  --race    3.3.5a race IDs to rebake (the converted ones).
  --retail-bakes / --layouts
            retail_bakes.csv (ExtraID;Race;Sex;FileDataID;RetailPath;BakeName) and the layouts folder hdport
            writes (<out>/layouts): NPCs with a retail bake (HD bake; goblin: its only bake) get that, converted
            to the 3.3.5a layout exactly as hdport converts skins; the rest are composited from parts.
            RetailPath is looked up in --data (add the TACTTool output folder). --retail-only skips the rest.
  --jobs    parallel processes, default all CPU cores (each holds its own texture cache: lower it if
            memory runs short).
  --size    pixel size of the files written (default: the layout's, 512); e.g. 256 for 1/4 the disk space.
  --layout  'stock' = the 512x512 layout of the unpatched client. 'retail1024' = the client patched with
            patch_component_layout.py: retail layout in the top half of 1024x1024 (hdport --layout retail1024
            too). Retail bakes keep their layout, with the body corner pieces hdport moved into the face
            square pasted there (--layouts is needed for both layouts).
"""
import argparse, os, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'lib'))
from PIL import Image
from dbcmerge import DBC
from blp import load_blp, save_blp_pal
import json, csv

# region rectangles (x, y, w, h): Wow.exe fills them at 0x9c8f30 for the 512 layout; regions 8/9 are the face
REGION_NAMES = ['ArmUpperTexture', 'ArmLowerTexture', 'HandTexture', 'TorsoUpperTexture', 'TorsoLowerTexture',
                'LegUpperTexture', 'LegLowerTexture', 'FootTexture']
STOCK = [(0, 0, 256, 128), (0, 128, 256, 128), (0, 256, 256, 64), (256, 0, 256, 128), (256, 128, 256, 64),
         (256, 192, 256, 128), (256, 320, 256, 128), (256, 448, 256, 64), (0, 320, 256, 64), (0, 384, 256, 128)]
LAYOUTS = {'stock': (512, STOCK),
           'retail1024': (1024, STOCK[:8] + [(512, 0, 512, 256), (512, 256, 512, 256)])}
FACE_UPPER, FACE_LOWER, TORSO_UPPER, LEG_UPPER = 8, 9, 3, 5

# layer of each equipment slot in each region (-1 = not drawn there): Wow.exe table at 0x9f6a00.
# Slots are CreatureDisplayInfoExtra's item order: head, shoulder, shirt, chest, waist, legs, feet,
# wrist, hands, tabard, back. Within a region, lower layers are drawn first.
_ = -1
ITEM_LAYERS = [
    [_, _, _, _, _, _, _, _],      # head      (model only)
    [_, _, _, _, _, _, _, _],      # shoulder  (model only)
    [0, 0, _, 0, 0, _, _, _],      # shirt
    [1, 1, _, 1, 1, 1, 1, _],      # chest
    [_, _, _, _, 5, 2, _, _],      # waist
    [_, _, _, _, _, 0, 0, _],      # legs
    [_, _, _, _, _, _, 2, 0],      # feet
    [_, 2, _, _, _, _, _, _],      # wrist
    [_, 3, 0, _, _, _, _, _],      # hands
    [_, _, _, 4, 4, _, _, _],      # tabard
    [_, _, _, _, _, _, _, _],      # back      (cape texture, not in the body texture)
]
SLOT_CHEST = 3

class Files:
    """case-insensitive lookup of game paths across several folders, first folder wins"""
    def __init__(self, roots):
        self.index = {}
        for root in roots:
            for d, _, fs in os.walk(root):
                for f in fs:
                    rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, '/').lower()
                    self.index.setdefault(rel, os.path.join(d, f))
        self.cache = {}
    def get(self, path, cache=True):
        """cache=False for files used once (retail bakes, up to 8 MB each in memory)"""
        if not path: return None
        k = path.replace('\\', '/').lower()
        if k in self.cache: return self.cache[k]
        p = self.index.get(k)
        im = load_blp(p).convert('RGBA') if p else None
        if cache: self.cache[k] = im
        return im

def find_dbc(dirs, name):
    for d in dirs:
        for f in os.listdir(d):
            if f.lower() == name.lower() + '.dbc': return DBC(os.path.join(d, f))
    raise SystemExit('%s.dbc not found in %s' % (name, ', '.join(dirs)))

def paste(canvas, img, rect, alpha=True):
    x, y, w, h = rect
    im = img.resize((w, h), Image.LANCZOS) if img.size != (w, h) else img
    if alpha: canvas.alpha_composite(im, (x, y))
    else: canvas.paste(im, (x, y))

def bake(ex, ctx):
    """one CreatureDisplayInfoExtra row -> (image, notes)"""
    cs, items, files, (size, R) = ctx['cs'], ctx['items'], ctx['files'], ctx['layout']
    race, sex, skin, face, hstyle, hcolor, facial = ex[1:8]
    notes = []
    def section(t, var, col):
        r = cs.get((race, sex, t, var, col))
        if r is None and (t, var, col) != (t, 0, 0):
            r = cs.get((race, sex, t, 0, col)) or cs.get((race, sex, t, 0, 0))
            if r: notes.append('section %d %d/%d missing, used %d/%d' % (t, var, col, r[0], r[1]))
        return r[2] if r else None
    def tex(path, what):
        im = files.get(path)
        if path and im is None: notes.append('%s not found: %s' % (what, path))
        return im
    sk = section(0, 0, skin)
    base = tex(sk and sk[0], 'skin')
    if base is None: return None, notes + ['no skin texture: skipped']
    # 1. every region starts as the skin's own region (the client copies the skin region by region)
    canvas = base.resize((size, size), Image.LANCZOS)      # the skin is authored in the same layout
    # 2. face, facial hair, scalp: upper and lower face regions, in that order
    fc, fh, hr = section(1, face, skin), section(2, facial, hcolor), section(3, hstyle, hcolor)
    for sec, lo, up, what in ((fc, 0, 1, 'face'), (fh, 0, 1, 'facial hair'), (hr, 1, 2, 'scalp')):
        if not sec: continue
        for k, reg in ((lo, FACE_LOWER), (up, FACE_UPPER)):
            im = tex(sec[k], what)
            if im: paste(canvas, im, R[reg], alpha=(what != 'face'))
    # 3. armour layers per region; underwear under them unless covered
    rows = {}
    for slot, did in enumerate(ex[8:19]):
        if did and did in items: rows[slot] = items[did]
    # chest with ItemDisplayInfo flag 4: the client then skips the pelvis underwear (Wow.exe 0x4f27a5 / 0x4f0d00)
    no_pelvis = SLOT_CHEST in rows and rows[SLOT_CHEST][0] & 4
    uw = section(4, 0, skin)
    for reg in range(8):
        layers = [(ITEM_LAYERS[slot][reg], slot, texs[reg]) for slot, (flags, texs) in rows.items()
                  if ITEM_LAYERS[slot][reg] >= 0 and texs[reg]]
        present = {l for l, _, _ in layers}
        if reg == TORSO_UPPER and uw and not present & {0, 1, 2}:
            im = tex(uw[1], 'underwear'); im and paste(canvas, im, R[reg])
        if reg == LEG_UPPER and uw and not no_pelvis and not present & {0, 1}:
            im = tex(uw[0], 'underwear'); im and paste(canvas, im, R[reg])
        for _, slot, name in sorted(layers):
            folder = 'Item\\TextureComponents\\%s\\%s' % (REGION_NAMES[reg], name)
            im = files.get(folder + '_U.blp') or files.get(folder + ('_F.blp' if sex else '_M.blp'))
            if im is None: notes.append('armour texture not found: %s_U/_%s' % (folder, 'F' if sex else 'M')); continue
            paste(canvas, im, R[reg])
    if size == 1024: canvas = pad_bottom(canvas.crop((0, 0, 1024, 512)))
    return canvas, notes

def pad_bottom(top):
    """retail1024: 1024x512 top half -> 1024x1024, the unused bottom half repeating the last row (as hdport's skins)"""
    out = Image.new('RGBA', (1024, 1024)); out.paste(top, (0, 0))
    out.paste(top.crop((0, 511, 1024, 512)).resize((1024, 512)), (0, 512)); return out

def convert_retail(path, lay, files, size):
    """a retail NPC bake (2:1 HD layout: body left half, face right half) -> the stock 512 layout, the way
    hdport builds skins (assets.Builder): body half copied, face half into the face area with any body
    pieces hdport moved there pasted back"""
    from assets import Builder, islands
    im = files.get(path, cache=False)
    if im is None: return None, 'retail bake not found: %s' % path
    if im.size[0] != 2 * im.size[1]: return None, 'retail bake %s is %dx%d, not 2:1' % (path, im.size[0], im.size[1])
    base = im.resize((1024, 512), Image.LANCZOS) if im.size != (1024, 512) else im
    if size == 1024:                       # retail1024: the bake is already in this layout (top half)
        if islands(lay):                   # ... except body corner pieces hdport moved into the face square
            base = base.convert('RGBA')
            base.paste(Builder.face_area(base.crop((512, 0, 1024, 512)), base, lay), (512, 0))
        return pad_bottom(base), None
    out = Image.new('RGBA', (512, 512))
    out.paste(base.crop((0, 0, 512, 512)), (0, 0))
    out.paste(Builder.face_area(base.crop((512, 0, 1024, 512)), base, lay), (0, 320))
    return out, None

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dbc', nargs='+', required=True); ap.add_argument('--data', nargs='+', required=True)
    ap.add_argument('--out', required=True); ap.add_argument('--race', type=int, nargs='+', required=True)
    ap.add_argument('--layout', choices=sorted(LAYOUTS), default='stock')
    ap.add_argument('--only', type=int, nargs='*', help='CreatureDisplayInfoExtra IDs to bake (testing)')
    ap.add_argument('--preview', help='also save the first baked texture as a PNG')
    ap.add_argument('--retail-bakes', help='retail_bakes.csv'); ap.add_argument('--layouts', help="hdport's <out>/layouts folder")
    ap.add_argument('--retail-only', action='store_true', help='only write NPCs that have a retail bake')
    ap.add_argument('--size', type=int, help='size of the written files (default: the layout size)')
    ap.add_argument('--jobs', type=int, help='parallel processes (default: all CPU cores; 1 = no parallelism)')
    a = ap.parse_args()
    csd = find_dbc(a.dbc, 'CharSections'); idi = find_dbc(a.dbc, 'ItemDisplayInfo'); exd = find_dbc(a.dbc, 'CreatureDisplayInfoExtra')
    cs = {}
    for r in csd.rows:     # ID, race, sex, section, tex[3], flags, variation, colour
        cs.setdefault((r[1], r[2], r[3], r[8], r[9]), (r[8], r[9], [csd.str(x) for x in r[4:7]]))
    items = {r[0]: (r[10], [idi.str(x) for x in r[15:23]]) for r in idi.rows}
    print('indexing data folders ...')
    files = Files(a.data)
    ctx = dict(cs=cs, items=items, files=files, layout=LAYOUTS[a.layout])
    retail = {}
    if a.retail_bakes:
        if not a.layouts:
            raise SystemExit('--retail-bakes needs --layouts (the layouts folder hdport writes): body pieces hdport moved are pasted into the bakes with it')
        for x in csv.DictReader(open(a.retail_bakes, encoding='utf-8-sig'), delimiter=';'):
            retail[int(x['ExtraID'])] = x['RetailPath']
    lays = {}
    def layout_for(race, sex):
        k = (race, sex)
        if k not in lays:
            p = os.path.join(a.layouts or '', '%d_%s.json' % (race, 'female' if sex else 'male'))
            lays[k] = json.load(open(p)) if a.layouts and os.path.exists(p) else None
        return lays[k]
    rows = [r for r in exd.rows if r[1] in a.race and exd.str(r[20]) and (not a.only or r[0] in a.only)]
    if a.retail_only: rows = [r for r in rows if r[0] in retail]
    print('%d displays to bake (%s layout)' % (len(rows), a.layout))
    outdir = os.path.join(a.out, 'Textures', 'BakedNpcTextures'); os.makedirs(outdir, exist_ok=True)
    global JOB
    JOB = dict(a=a, ctx=ctx, retail=retail, layout_for=layout_for, files=files, outdir=outdir, exd=exd,
               preview_id=rows[0][0] if a.preview and rows else None)
    problems = collections.Counter(); done = skipped = 0; report = []; used = collections.Counter()
    jobs = max(1, a.jobs or os.cpu_count() or 1)
    if jobs > 1 and len(rows) > 1:
        import multiprocessing
        pool = multiprocessing.get_context('fork').Pool(jobs)      # workers inherit the loaded tables
        results = pool.imap_unordered(bake_one, rows, chunksize=4)
    else:
        pool = None; results = map(bake_one, rows)
    print('baking with %d process%s' % (jobs if pool else 1, 'es' if pool else ''))
    for n, (rid, name, notes, kind) in enumerate(results):
        for x in notes: problems[x.split(':')[0]] += 1; report.append('%d %s: %s' % (rid, name, x))
        if kind is None: skipped += 1
        else: used[kind] += 1; done += 1
        if (n + 1) % 250 == 0: print('  %d / %d' % (n + 1, len(rows)), flush=True)
    if pool: pool.close(); pool.join()
    with open(os.path.join(a.out, 'rebake_report.txt'), 'w') as f:
        f.write('baked %d (%s), skipped %d\n\n' % (done, ', '.join('%s %d' % kv for kv in used.items()), skipped) + ''.join('%6d x %s\n' % (v, k) for k, v in problems.most_common()) + '\n' + '\n'.join(sorted(report)) + '\n')
    print('baked %d (%s), skipped %d; %d notes in rebake_report.txt' % (done, ', '.join('%s %d' % kv for kv in used.items()), skipped, len(report)))

JOB = {}

def bake_one(r):
    """bake one CreatureDisplayInfoExtra row and write it -> (id, bake name, notes, 'retail'/'composited'/None)"""
    a, ctx, retail, layout_for, files = JOB['a'], JOB['ctx'], JOB['retail'], JOB['layout_for'], JOB['files']
    name = JOB['exd'].str(r[20])
    if True:
        img = None; notes = []; kind = None
        if r[0] in retail:
            lay = layout_for(r[1], r[2])
            if lay is None: notes.append('no hdport layout for race %d sex %d: composited instead' % (r[1], r[2]))
            elif lay and lay.get('island_extra'): notes.append('retail bake: body corner drawn from the skin extra texture (skin colour, no armour)')
            if lay is not None:
                img, why = convert_retail(retail[r[0]], lay, files, LAYOUTS[a.layout][0])
                if why: notes.append(why + ': composited instead')
            if img is not None: kind = 'retail'
        if img is None:
            img, more = bake(r, ctx); notes += more
            if img is not None: kind = 'composited'
        if img is None: return r[0], name, notes, None
        if a.size and img.size[0] != a.size: img = img.resize((a.size, a.size), Image.LANCZOS)
        save_blp_pal(img.convert('RGB'), os.path.join(JOB['outdir'], name))
        if r[0] == JOB['preview_id']: img.save(a.preview)
        return r[0], name, notes, kind

if __name__ == '__main__':
    main()
