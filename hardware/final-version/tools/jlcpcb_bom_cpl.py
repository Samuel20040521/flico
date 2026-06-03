#!/usr/bin/env python3
"""
Generate JLCPCB-compatible BOM and CPL from SOC_Final.kicad_pcb.

CPL coordinate convention (must match Gerber):
  - X = same as KiCad absolute X
  - Y = negated KiCad Y (flip to Y-up, matching Gerber convention)
  - Layer: Top / Bottom

The Gerber exported by KiCad uses absolute coords with Y flipped to standard
Gerber Y-up. The CPL therefore must use absolute KiCad X and negated Y so
JLCPCB's renderer can match parts to the gerber board outline.

Do NOT set aux_axis_origin and do NOT use --use-drill-file-origin. Keep both
gerber and CPL in absolute KiCad coords — they will be in the same frame.
"""
import re
import csv
from pathlib import Path
from collections import defaultdict

PCB = Path('/Users/dengensheng/Developer/SOC_Final/SOC_Final/SOC_Final.kicad_pcb')
OUT_DIR = Path('/Users/dengensheng/Developer/SOC_Final/SOC_Final/Gerber')
BOM_CSV = OUT_DIR / 'SOC_Final_BOM.csv'
CPL_CSV = OUT_DIR / 'SOC_Final_CPL.csv'


def ref_sort_key(r):
    m = re.match(r'([A-Za-z]+)(\d+)', r)
    return (m.group(1), int(m.group(2))) if m else (r, 0)


def parse_components(txt):
    comps = []
    for m in re.finditer(r'\n\t\(footprint\s+"([^"]+)"', txt):
        start = m.start()
        end = txt.find('\n\t)\n', start)
        seg = txt[start:end if end > 0 else start + 8000]
        at = re.search(
            r'\n\t\t\(at\s+(-?\d+\.?\d*)\s+(-?\d+\.?\d*)(?:\s+(-?\d+\.?\d*))?\)',
            seg)
        side = re.search(r'^\t\t\(layer "(F|B)\.Cu"\)', seg, re.MULTILINE)
        ref = re.search(r'\(property "Reference"\s+"([^"]+)"', seg)
        val = re.search(r'\(property "Value"\s+"([^"]+)"', seg)
        if not (at and side and ref and val):
            continue
        comps.append(dict(
            ref=ref.group(1),
            value=val.group(1).strip(),
            footprint=m.group(1),
            x=float(at.group(1)),
            y=float(at.group(2)),
            rot=float(at.group(3)) if at.group(3) else 0.0,
            side=side.group(1),
        ))
    return comps


def describe(ref_prefix, value, footprint):
    """Build a description string to help JLCPCB's BOM matcher find LCSC parts."""
    # Extract package from footprint name (e.g. "0603" from "R_0603_...")
    pkg_m = re.search(r'_(\d{4})_', footprint)
    pkg = pkg_m.group(1) if pkg_m else ''
    if ref_prefix == 'R':
        return f'Resistor {value} {pkg}'.strip()
    if ref_prefix == 'C':
        return f'Capacitor {value} {pkg}'.strip()
    if ref_prefix == 'D':
        return f'Diode/Photodiode {value}'
    if ref_prefix == 'U':
        return f'IC {value}'
    if ref_prefix == 'J':
        # Strip KiCad's mangled name
        return 'Pin header/socket' if 'PinHeader' in footprint or 'PinSocket' in footprint else value
    return value


def write_bom(comps):
    groups = defaultdict(list)
    for c in comps:
        groups[(c['value'], c['footprint'])].append(c['ref'])
    rows = []
    for (value, fp), refs in groups.items():
        sorted_refs = sorted(refs, key=ref_sort_key)
        prefix = re.match(r'([A-Za-z]+)', sorted_refs[0]).group(1)
        rows.append(dict(
            Comment=value,
            Designator=','.join(sorted_refs),
            Footprint=fp,
            Quantity=str(len(sorted_refs)),
            Description=describe(prefix, value, fp),
            LCSC='',
        ))
    rows.sort(key=lambda r: ref_sort_key(r['Designator'].split(',')[0]))
    with BOM_CSV.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=[
            'Comment', 'Designator', 'Footprint', 'Quantity', 'Description', 'LCSC'])
        w.writeheader()
        w.writerows(rows)
    print(f"BOM: {BOM_CSV.name}  {len(rows)} lines, {len(comps)} parts")


def write_cpl(comps):
    rows = []
    for c in sorted(comps, key=lambda c: ref_sort_key(c['ref'])):
        # JLCPCB convention: same absolute coordinate system as the Gerber.
        # KiCad pcb stores Y in Y-down convention; Gerber/CPL use Y-up,
        # so negate Y. X stays unchanged.
        rows.append(dict(
            Designator=c['ref'],
            **{'Mid X': f"{c['x']:.4f}", 'Mid Y': f"{-c['y']:.4f}"},
            Layer='Top' if c['side'] == 'F' else 'Bottom',
            Rotation=f"{c['rot']:.1f}",
        ))
    with CPL_CSV.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['Designator', 'Mid X', 'Mid Y', 'Layer', 'Rotation'])
        w.writeheader()
        w.writerows(rows)
    print(f"CPL: {CPL_CSV.name}  {len(rows)} parts (absolute KiCad coords, Y negated)")


def main():
    txt = PCB.read_text()
    comps = parse_components(txt)
    write_bom(comps)
    write_cpl(comps)


if __name__ == '__main__':
    main()
