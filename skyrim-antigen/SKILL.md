---
name: skyrim-antigen
description: Build and run Antigen diagnostics for Skyrim plugins and MO2 profiles. Use to detect or explain record, reference, asset, worldspace, NPC, quest, dialogue, scene, package, and leveled-list issues.
---

# Skyrim Antigen

Use this skill directory as the working directory.

Antigen reports known record and load-order problems as `Suggestion`, `Warning`, `Error`, or `CTD` topics.

## Analyzer coverage

- References and assets: unresolved FormLinks; missing referenced models and textures.
- Cells and world: placement, ownership, persistence, doors, markers, duplicate references, landscape seams, and navmesh triangles.
- NPCs: appearance, race, voice, inventory, factions, packages, merchants, trainers, and unique placement.
- Lists and crafting: circular or oversized leveled lists, duplicate constructibles, tempering, and recipe consistency.
- Quests and scripting: aliases, stages, conditions, dialogue links and speakers, scene actions, package data, and VMAD fragments.
- General record lint: required fields, flags, keywords, slots, values, text, and cross-record consistency.

## Setup

```powershell
git clone --depth 1 https://github.com/Mutagen-Modding/Antigen.git tools/Antigen
dotnet publish tools/Antigen/Mutagen.Bethesda.Analyzers.Cli/Mutagen.Bethesda.Analyzers.Cli.csproj -c Release -o tools/cli /p:GeneratePackageOnBuild=false
```

## MO2 profile scan

```powershell
$cli = (Resolve-Path "tools/cli/Mutagen.Bethesda.Analyzers.Cli.exe").Path
& "<MO2 root>\ModOrganizer.exe" -p "<profile>" run `
  -a 'run-analyzers -g <SkyrimSE|SkyrimVR> -s <Suggestion|Warning|Error|CTD> --DataFolder "<MO2 game Data>" -o "<report.csv>"' `
  -c (Split-Path $cli) $cli
```

`-s` is the minimum reported severity. Add `--PrintTopics` to list enabled topics.

## Results

- MO2 may not relay child stdout; use CSV output for profile scans.
- CSV output is headerless and appended; use a new or cleared file.
- Exit code `0` means the scan completed, even when findings exist.
- Antigen scans every non-deleted record version, including losing overrides; a finding is not proof of an active runtime issue.
- Missing-asset analyzers check visible files and do not inspect BSA members.

To explain an `A###` topic, find its numeric ID under `tools/Antigen/Mutagen.Bethesda.Analyzers.Skyrim` and read the analyzer and test when present.
