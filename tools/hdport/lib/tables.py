"""Readers for the retail tables (CSV exports from wow.export) and the export folder."""
import csv, collections, json, os, re

def read_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        head = f.readline(); f.seek(0)
        delim = ';' if head.count(';') > head.count(',') else ','
        return list(csv.DictReader(f, delimiter=delim))

class Tables:
    NEEDED = ['ChrCustomizationOption', 'ChrCustomizationChoice', 'ChrCustomizationElement',
              'ChrCustomizationMaterial', 'ChrCustomizationGeoset', 'ChrCustomizationReq', 'TextureFileData']

    def __init__(self, folder):
        self.folder = folder
        files = {f.lower(): f for f in os.listdir(folder)}
        missing = [n for n in self.NEEDED if n.lower() + '.csv' not in files]
        if missing:
            raise SystemExit('missing table CSVs in %s: %s' % (folder, ', '.join(missing)))
        def load(n): return read_csv(os.path.join(folder, files[n.lower() + '.csv']))
        self.opt = {int(r['ID']): r for r in load('ChrCustomizationOption')}
        self.cho = {int(r['ID']): r for r in load('ChrCustomizationChoice')}
        self.mat = {int(r['ID']): r for r in load('ChrCustomizationMaterial')}
        self.geo = {int(r['ID']): r for r in load('ChrCustomizationGeoset')}
        self.req = {int(r['ID']): r for r in load('ChrCustomizationReq')}
        self.els = collections.defaultdict(list)
        for e in load('ChrCustomizationElement'):
            self.els[int(e['ChrCustomizationChoiceID'])].append(e)
        self.tfd = collections.defaultdict(list)
        for r in load('TextureFileData'):
            self.tfd[int(r['MaterialResourcesID'])].append(int(r['FileDataID']))
        # optional: which choices a requirement allows (retail ties e.g. hair colours to hair styles)
        self.req_choices = collections.defaultdict(set)
        if 'chrcustomizationreqchoice.csv' in files:
            for r in load('ChrCustomizationReqChoice'):
                self.req_choices[int(r['ChrCustomizationReqID'])].add(int(r['ChrCustomizationChoiceID']))
        self.by_opt = collections.defaultdict(list)
        for c in self.cho.values():
            self.by_opt[int(c['ChrCustomizationOptionID'])].append(c)

    def option(self, chr_model, name):
        hits = [o for o in self.opt.values() if int(o['ChrModelID']) == chr_model and o['Name_lang'] == name]
        return int(hits[0]['ID']) if hits else None

    def choices(self, opt_id):
        return sorted(self.by_opt.get(opt_id, []), key=lambda c: int(c['OrderIndex']))

    def dk_only(self, choice):
        r = self.req.get(int(choice['ChrCustomizationReqID']))
        return bool(r) and int(r.get('ClassMask', 0) or 0) == 32

    def req_type(self, choice):
        r = self.req.get(int(choice['ChrCustomizationReqID']))
        return int(r.get('ReqType', 0) or 0) if r else 0

    def allowed(self, choice):
        """choice IDs (of other options) this choice is limited to, or None if unrestricted"""
        return self.req_choices.get(int(choice['ChrCustomizationReqID'])) or None

    def elements(self, choice, files):
        """-> list of (related choice id, 'tex<target>' | 'geo<type>', path | value)"""
        out = []
        for e in self.els[int(choice['ID'])]:
            rel = int(e['RelatedChrCustomizationChoiceID'])
            m = int(e['ChrCustomizationMaterialID'])
            if m and m in self.mat:
                fd = self.tfd.get(int(self.mat[m]['MaterialResourcesID']), [])
                out.append((rel, 'tex%s' % self.mat[m]['ChrModelTextureTargetID'],
                            next((files.find(x) for x in fd if files.find(x)), None)))
            g = int(e['ChrCustomizationGeosetID'])
            if g and g in self.geo:
                out.append((rel, 'geo%s' % self.geo[g]['GeosetType'], int(self.geo[g]['GeosetID'])))
        return out

class ExportFiles:
    """Resolves FileDataIDs to files inside the wow.export root (case-insensitive), using the
    listfile first and wow.export's '<name>_<fdid>.ext' naming for unnamed files as a fallback."""
    def __init__(self, root, listfile=None, prefixes=('character/',)):
        self.root = root
        self.index = {}
        self.by_fdid = {}
        for d, _, fs in os.walk(root):
            for f in fs:
                full = os.path.join(d, f)
                rel = os.path.relpath(full, root).replace(os.sep, '/').lower()
                self.index[rel] = full
                m = re.search(r'_(\d+)\.(blp|m2|skin|anim)$', f.lower())
                if m: self.by_fdid.setdefault(int(m.group(1)), full)
        # wow.export writes <model>.manifest.json next to each model: file data IDs of its textures etc.
        for rel, full in list(self.index.items()):
            if rel.endswith('.manifest.json'):
                try: man = json.load(open(full, encoding='utf-8'))
                except (OSError, ValueError): continue
                for lst in man.values():
                    for e in (lst if isinstance(lst, list) else [lst]):
                        if isinstance(e, dict) and e.get('fileDataID') and e.get('file'):
                            p = os.path.join(os.path.dirname(full), e['file'])
                            if os.path.exists(p): self.by_fdid.setdefault(int(e['fileDataID']), p)
        self.names = {}
        if listfile:
            with open(listfile, encoding='utf-8', errors='replace') as f:
                for line in f:
                    a, _, b = line.strip().partition(';')
                    b = b.lower()
                    if a.isdigit() and b.startswith(prefixes): self.names[int(a)] = b

    def find(self, fdid):
        n = self.names.get(fdid)
        if n and n in self.index: return self.index[n]
        return self.by_fdid.get(fdid)

    def path(self, rel):
        return self.index.get(rel.replace('\\', '/').lower())
