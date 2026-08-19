---
name: skyrim-bsa
description: Inspect metadata, list contents, and extract BSA archives with BSArch64; also supports BA2 archives.
---

# Skyrim BSA Archives

Use this skill directory as the working directory.

## Setup

```bash
mise trust mise.toml
mise install http:bsarch
```

Run BSArch through `mise exec --`.

## Inspect

Show archive metadata before extracting:

```bash
mise exec -- BSArch64.exe "<archive.bsa>"
```

List paths or produce an extended dump:

```bash
mise exec -- BSArch64.exe "<archive.bsa>" -list
mise exec -- BSArch64.exe "<archive.bsa>" -dump
```

## Extract

Treat the archive as read-only. Use a new, empty destination outside the game `Data` directory and installed MO2 mods unless the user explicitly requests otherwise.

BSArch requires the destination directory to exist:

```bash
mkdir -p "<empty-output-directory>"
mise exec -- BSArch64.exe unpack "<archive.bsa>" "<empty-output-directory>" -mt
```

Assume matching files in an existing destination can be overwritten. Never omit the destination argument, because BSArch otherwise extracts beside the input archive.
