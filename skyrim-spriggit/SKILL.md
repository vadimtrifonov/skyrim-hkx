---
name: skyrim-spriggit
description: Serialize and deserialize Skyrim plugins (ESP/ESL/ESM) with Spriggit JSON. Use to convert plugins to editable text trees or rebuild plugins.
---

# Skyrim Spriggit

Use this skill directory as the working directory.

## Setup

```bash
mise trust mise.toml
mise install http:spriggit-cli
```

Run commands through `mise exec --`.

## Serialize

Use a clean, dedicated output directory; `<ModName>.spriggit` is the preferred name.

```bash
mise exec -- Spriggit.CLI.exe serialize --InputPath "<plugin.esp>" --OutputPath "<ModName>.spriggit" --GameRelease <SkyrimSE|SkyrimVR> --PackageName Spriggit.Json --PackageVersion <version>
```

## Deserialize

Package and game metadata are read from the serialized tree.

```bash
mise exec -- Spriggit.CLI.exe deserialize --InputPath "<ModName>.spriggit" --OutputPath "<plugin.esp>"
```
