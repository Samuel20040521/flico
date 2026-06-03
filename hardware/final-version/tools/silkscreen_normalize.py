#!/usr/bin/env python3
"""
Normalize silkscreen text on SOC_Final.kicad_pcb per design rules:

  R*, C* (0603 passives):
    - Reference -> hidden
    - Value -> visible on *.SilkS layer, position (0,0,0), size 0.5x0.5, thickness 0.08
      (sits under part body, hidden after solder)

  U* (ICs):
    - Reference -> visible, size 1.0x1.0, thickness 0.15
    - Value -> hidden

  J* (connectors):
    - Reference -> visible, size 1.2x1.2, thickness 0.18
    - Value -> hidden

  D* (diodes/sensors):
    - Reference + Value both visible on *.SilkS, size 1.0x1.0, thickness 0.15

Run this whenever footprints change (e.g. after Update PCB from Schematic).
Idempotent. Edits the .kicad_pcb directly.
"""
import re
import sys
from pathlib import Path

PCB = Path('/Users/dengensheng/Developer/SOC_Final/SOC_Final/SOC_Final.kicad_pcb')

# (size_w, size_h, thickness) and visibility per category
RULES = {
    'R': dict(ref=dict(hide=True),
              val=dict(hide=False, layer='silk', size=(0.5, 0.5), thickness=0.15,
                       position=(0, 0, 0))),
    'C': dict(ref=dict(hide=True),
              val=dict(hide=False, layer='silk', size=(0.5, 0.5), thickness=0.15,
                       position=(0, 0, 0))),
    'U': dict(ref=dict(hide=False, size=(1.0, 1.0), thickness=0.15),
              val=dict(hide=True)),
    'J': dict(ref=dict(hide=False, size=(1.2, 1.2), thickness=0.18),
              val=dict(hide=True)),
    'D': dict(ref=dict(hide=False, size=(1.0, 1.0), thickness=0.15),
              val=dict(hide=False, layer='silk', size=(1.0, 1.0), thickness=0.15)),
}


def detect_side(fp_block: str) -> str:
    """Return 'F' or 'B' based on the footprint's primary layer."""
    m = re.search(r'^\t\t\(layer "(F|B)\.Cu"\)', fp_block, re.MULTILINE)
    return m.group(1) if m else 'F'


def resolve_layer(side: str, kind: str) -> str:
    """kind is 'silk' or 'fab'."""
    return f'{side}.{"SilkS" if kind == "silk" else "Fab"}'


def edit_property(prop_body: str, changes: dict, side: str) -> str:
    body = prop_body

    # Layer
    if 'layer' in changes:
        new_layer = resolve_layer(side, changes['layer'])
        body = re.sub(r'\(layer "[^"]+"\)', f'(layer "{new_layer}")', body, count=1)

    # Hide flag (insert after layer line, or remove existing)
    has_hide = re.search(r'\n\t\t\t\(hide yes\)', body) is not None
    if changes.get('hide') is True and not has_hide:
        body = re.sub(r'(\(layer "[^"]+"\))', r'\1\n\t\t\t(hide yes)', body, count=1)
    elif changes.get('hide') is False and has_hide:
        body = re.sub(r'\n\t\t\t\(hide yes\)', '', body, count=1)

    # Position (only if explicitly given)
    if 'position' in changes:
        x, y, rot = changes['position']
        body = re.sub(
            r'\(at\s+-?\d+\.?\d*\s+-?\d+\.?\d*(?:\s+-?\d+\.?\d*)?\s*\)',
            f'(at {x} {y} {rot})', body, count=1)

    # Size
    if 'size' in changes:
        w, h = changes['size']
        body = re.sub(r'\(size\s+-?\d+\.?\d*\s+-?\d+\.?\d*\)',
                      f'(size {w} {h})', body, count=1)

    # Thickness
    if 'thickness' in changes:
        body = re.sub(r'\(thickness\s+-?\d+\.?\d*\)',
                      f'(thickness {changes["thickness"]})', body, count=1)

    return body


def edit_footprint(fp_block: str) -> tuple[str, str]:
    """Return (modified_block, ref_designator)."""
    ref_m = re.search(r'\(property "Reference"\s+"([^"]+)"', fp_block)
    if not ref_m:
        return fp_block, ''
    ref = ref_m.group(1)
    category = ref[0]
    if category not in RULES:
        return fp_block, ref

    rules = RULES[category]
    side = detect_side(fp_block)

    for kind, prop_name in (('ref', 'Reference'), ('val', 'Value')):
        changes = rules.get(kind)
        if not changes:
            continue
        prop_re = re.compile(
            rf'(\(property "{prop_name}"\s+"[^"]*"\n)(.*?)(\n\t\t\))',
            re.DOTALL)
        m = prop_re.search(fp_block)
        if not m:
            continue
        header, body, footer = m.groups()
        new_body = edit_property(body, changes, side)
        fp_block = fp_block[:m.start()] + header + new_body + footer + fp_block[m.end():]

    return fp_block, ref


def main():
    txt = PCB.read_text()

    # Iterate footprint blocks at 1-tab indent
    out_chunks = []
    last_end = 0
    fp_starts = [m.start() for m in re.finditer(r'\n\t\(footprint\b', txt)]
    summary = []

    for start in fp_starts:
        # Find end of this footprint: the next "\n\t)\n" line
        end = txt.find('\n\t)\n', start)
        if end < 0:
            continue
        end += len('\n\t)\n')

        out_chunks.append(txt[last_end:start])
        block = txt[start:end]
        new_block, ref = edit_footprint(block)
        out_chunks.append(new_block)
        if ref:
            summary.append((ref, block != new_block))
        last_end = end

    out_chunks.append(txt[last_end:])
    new_txt = ''.join(out_chunks)

    # Parens sanity
    sym = re.sub(r'"(?:[^"\\]|\\.)*"', '""', new_txt)
    if sym.count('(') != sym.count(')'):
        sys.exit(f"ERR: paren imbalance after edit: opens={sym.count('(')} closes={sym.count(')')}")

    PCB.write_text(new_txt)
    changed = sum(1 for _, c in summary if c)
    print(f"Updated {changed}/{len(summary)} footprints")
    for ref, c in sorted(summary):
        marker = '*' if c else ' '
        print(f"  {marker} {ref}")
    delta = len(new_txt) - len(txt)
    print(f"Size delta: {delta:+d} bytes")


if __name__ == '__main__':
    main()
