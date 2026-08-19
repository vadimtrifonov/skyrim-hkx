---
name: skyrim-nif
description: Inspect, analyze, and render Skyrim NIF files with PyNifly and FO76Utils. Use for NIF block structure, meshes, textures, shaders, skinning, bones, partitions, transforms, collision, external asset references, archive-native lookup, OBJ export, and unattended PNG previews.
---

# Skyrim NIF Analysis and Preview

Use this skill directory as the working directory.

## Setup

```bash
mise trust mise.toml
mise install
```

Run commands through `mise exec --`.

## Structured report

Use the read-only PyNifly inspector for semantic analysis of a loose NIF.

```bash
mise exec -- python scripts/inspect_nif.py "<input.nif>"
mise exec -- python scripts/inspect_nif.py "<input.nif>" -o "<report.json>"
```

Report outputs must use `.json`; existing files require `--force`.

Optional detail:

```bash
mise exec -- python scripts/inspect_nif.py "<input.nif>" --geometry --bones --nodes --blocks -o "<report.json>"
```

- `--geometry` adds actual geometry counts and mesh bounds.
- `--bones` includes bone names rather than counts only.
- `--nodes` includes the scene-node hierarchy.
- `--blocks` includes the ordered NIF block list.
- `--data-root "<Data>"` checks whether referenced textures and other assets exist as loose files under a supplied game or mod Data directory. It does not search BSAs.

Inspect both `_0.nif` and `_1.nif` members of a weight-slider pair when relevant.

## Archive-native lookup

FO76Utils can locate NIFs in loose files and BSA/BA2 archives beneath a physical Data-like directory:

```bash
mise exec -- nif_info.exe -q "<Data-directory>" "meshes/path/model.nif"
mise exec -- nif_info.exe -m "<Data-directory>" "meshes/path/model.nif" > "<materials.txt>"
mise exec -- nif_info.exe -v "<Data-directory>" "meshes/path/model.nif" > "<verbose.txt>"
```

Use lowercase, full asset paths with direct `nif_info` calls. Its filters are case-sensitive substring matches after paths are normalized to lowercase, so a short pattern can select multiple files. Prefer `scripts/render_nif.py` for deterministic preview selection because it requires exactly one exact match.

Pass the containing Data directory rather than one BSA when textures may live in other archives. FO76Utils only sees the physical directory or archive supplied to it. Its flattened directory view does not reproduce plugin/BSA load order or MO2 priority, and the report cannot identify which archive supplied a winning path; use the actual game-visible Data view or inspect candidate archives separately when conflicts matter.

## Headless PNG preview

Render one exact loose or archived asset without extracting it or opening a viewer:

```bash
mise exec -- python scripts/render_nif.py \
  "<Data-directory-or-archive>" \
  "meshes/weapons/iron/longsword.nif" \
  --direction 2 \
  -o "<new-output-directory>/longsword.png" \
  --report "<new-output-directory>/longsword.json"
```

The asset must resolve to exactly one NIF. Write outputs outside the source/Data directory; existing outputs require `--force`.
Exit code `3` means the render has no non-zero-alpha pixels, not that its artifacts were lost: the PNG and JSON report are preserved.
If `--report` is omitted, the JSON report is written to stdout. Add `--keep-dds` only when the original DDS is useful.

Useful camera directions are:

- `0` to `3`: isometric from NW, SW, SE, and NE;
- `4`: top;
- `5` to `8`: south, east, north, and west;
- `10` to `13`: isometric from N, W, S, and E.

Generate several views when orientation or occlusion matters. Keep dimensions, direction, scale, rotation, lighting, and debug mode identical for before/after comparisons.

Diagnostic render modes:

```bash
mise exec -- python scripts/render_nif.py "<Data>" "<asset.nif>" --debug 3 -o "<normals.png>"
mise exec -- python scripts/render_nif.py "<Data>" "<asset.nif>" --debug 4 -o "<diffuse-only.png>"
```

`--debug 1` shows TriShape block colors, `2` depth, `3` normals, `4` diffuse texture only, and `5` lighting only.

## OBJ export

For a geometry interchange preview, emit matching OBJ and MTL files with the same exact asset path:

```bash
mise exec -- nif_info.exe -obj -o "<model.obj>" "<Data>" "meshes/path/model.nif"
mise exec -- nif_info.exe -mtl -o "<model.mtl>" "<Data>" "meshes/path/model.nif"
```

OBJ export is a convenience view of supported geometry, not a semantic NIF round trip.

## Interpretation limits

- FO76Utils previews are strongest for static meshes and supported Bethesda shaders.
- A blank skinned-mesh preview is inconclusive, not evidence that the NIF contains no geometry.
- Particles, controllers, animation state, and Havok collision are not meaningfully proven by the preview.
- Missing or wrong textures can reflect the supplied source directory rather than the NIF itself.
