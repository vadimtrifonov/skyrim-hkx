#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def native_path(path: Path) -> str:
    resolved = str(path.resolve(strict=True))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def vector_summary(values: list[list[float]]) -> dict[str, Any] | None:
    if not values:
        return None
    width = len(values[0])
    columns = [[row[i] for row in values] for i in range(width)]
    first = values[0]
    last = values[-1]
    return {
        "first": first,
        "last": last,
        "min": [min(column) for column in columns],
        "max": [max(column) for column in columns],
        "net": [last[i] - first[i] for i in range(width)],
    }


def quaternion_summary(values: list[list[float]]) -> dict[str, Any] | None:
    if not values:
        return None
    return {"first": values[0], "last": values[-1]}


def track_report(index: int, track: Any, name: str, bone_index: int | None, frames: bool) -> dict[str, Any]:
    report = {
        "index": index,
        "name": name,
        "bone_index": bone_index,
        "translation": vector_summary(track.translations),
        "rotation": quaternion_summary(track.rotations),
        "scale": vector_summary(track.scales),
    }
    if frames:
        report["frames"] = {
            "translations": track.translations,
            "rotations": track.rotations,
            "scales": track.scales,
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Skyrim LE/SE animation HKX with PyNifly.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--skeleton", type=Path)
    parser.add_argument("--tracks", action="store_true", help="Include per-track transform summaries.")
    parser.add_argument("--frames", action="store_true", help="Include every decompressed frame; implies --tracks.")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    pynifly_root = os.environ.get("PYNIFLY_ROOT")
    if not pynifly_root:
        parser.error("PYNIFLY_ROOT is unset; run through 'mise exec --'.")

    hkx_module_dir = Path(pynifly_root) / "hkx"
    if not (hkx_module_dir / "anim_skyrim.py").is_file():
        parser.error(f"PyNifly Skyrim parser not found under {hkx_module_dir}")
    sys.path.insert(0, str(hkx_module_dir))

    from anim_skyrim import load_skyrim_animation, load_skyrim_skeleton

    input_display = args.input.resolve(strict=True)
    input_native = native_path(args.input)
    animation = load_skyrim_animation(input_native)
    if animation is None:
        parser.error(f"No animation container found in {input_display}")

    skeleton = None
    skeleton_display = None
    if args.skeleton:
        skeleton_display = args.skeleton.resolve(strict=True)
        skeleton = load_skyrim_skeleton(native_path(args.skeleton))
        if skeleton is None:
            parser.error(f"No skeleton found in {skeleton_display}")

    with open(input_native, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()

    explicit_map = list(animation.track_to_bone_indices)
    if explicit_map:
        mapping_mode = "explicit"
        effective_map = explicit_map
    else:
        effective_map = list(range(animation.num_tracks))
        mapping_mode = "implicit-count-compatible" if skeleton and len(skeleton.bones) >= animation.num_tracks else "implicit-unvalidated"

    track_names = list(animation.bone_names)
    if skeleton:
        for index in range(animation.num_tracks):
            if index >= len(track_names) or not track_names[index]:
                bone_index = effective_map[index] if index < len(effective_map) else index
                name = skeleton.bones[bone_index] if bone_index < len(skeleton.bones) else ""
                if index >= len(track_names):
                    track_names.extend([""] * (index + 1 - len(track_names)))
                track_names[index] = name

    root_index = 0 if animation.tracks else None
    for index, name in enumerate(track_names[: animation.num_tracks]):
        if name.casefold() == "npc root [root]".casefold():
            root_index = index
            break

    fps = 1.0 / animation.frame_duration if animation.frame_duration > 0 else None
    report: dict[str, Any] = {
        "file": {
            "path": str(input_display),
            "size": os.stat(input_native).st_size,
            "sha256": digest,
        },
        "animation": {
            "duration": animation.duration,
            "num_frames": animation.num_frames,
            "frame_duration": animation.frame_duration,
            "fps": fps,
            "num_tracks": animation.num_tracks,
            "num_blocks": animation.num_blocks,
            "max_frames_per_block": animation.max_frames_per_block,
            "block_duration": animation.block_duration,
        },
        "binding": {
            "original_skeleton_name": animation.original_skeleton_name,
            "blend_hint": animation.blend_hint,
            "blend_hint_name": {0: "NORMAL", 1: "ADDITIVE"}.get(animation.blend_hint, "UNKNOWN"),
            "mapping_mode": mapping_mode,
            "transform_track_to_bone_indices": explicit_map,
        },
        "annotation_track_names": track_names,
        "annotations": [{"time": item.time, "text": item.text} for item in animation.annotations],
    }

    if skeleton:
        report["skeleton"] = {
            "path": str(skeleton_display),
            "name": skeleton.name,
            "num_bones": len(skeleton.bones),
            "bones": skeleton.bones,
            "parents": skeleton.parents,
        }

    if root_index is not None and root_index < len(animation.tracks):
        bone_index = effective_map[root_index] if root_index < len(effective_map) else None
        name = track_names[root_index] if root_index < len(track_names) else ""
        report["root_track"] = track_report(root_index, animation.tracks[root_index], name, bone_index, args.frames)

    if args.tracks or args.frames:
        tracks = []
        for index, track in enumerate(animation.tracks):
            bone_index = effective_map[index] if index < len(effective_map) else None
            name = track_names[index] if index < len(track_names) else ""
            tracks.append(track_report(index, track, name, bone_index, args.frames))
        report["tracks"] = tracks

    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
