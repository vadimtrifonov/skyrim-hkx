---
name: skyrim-hkx
description: Convert and inspect Skyrim Havok 2010.2 HKX animation, skeleton, and behavior files with serde-hkx and PyNifly. Use for XML conversion, animation metadata, annotations, bindings, skeleton mappings, and decompressed transform tracks.
---

# Skyrim HKX Analysis

Use this skill directory as the working directory.

## Setup

```bash
mise trust mise.toml
mise install python github:SARDONYX-sard/serde-hkx github:BadDogSkyrim/PyNifly
```

Run commands through `mise exec --`.

## HKX to XML

```bash
mise exec -- hkxc convert -i "<input.hkx>" -o "<output.xml>" -v xml
```

## Animation report

```bash
mise exec -- python scripts/inspect_animation.py "<input.hkx>"
mise exec -- python scripts/inspect_animation.py "<input.hkx>" --skeleton "<skeleton.hkx>" --tracks -o "<report.json>"
```

- serde-hkx reads animation, skeleton, and behavior structure.
- PyNifly decompresses animation transforms; it does not parse behavior graphs.
- Animation annotations and `hkbClipTriggerArray` entries are independent event sources.

See [references/fields.md](references/fields.md) for the relevant Havok fields.
