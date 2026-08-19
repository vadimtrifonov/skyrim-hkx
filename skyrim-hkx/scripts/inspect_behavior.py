#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any


REFERENCE = re.compile(r"#\d+")
INTEGER = re.compile(r"[+-]?\d+")


def native_path(path: Path) -> str:
    resolved = str(path.resolve(strict=True))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def param(obj: ET.Element, name: str) -> ET.Element | None:
    return obj.find(f"./hkparam[@name='{name}']")


def scalar(text: str) -> Any:
    if text == "null":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if INTEGER.fullmatch(text):
        return int(text)
    try:
        return float(text)
    except ValueError:
        return text


def value(item: ET.Element | None) -> Any:
    if item is None:
        return None
    strings = item.findall("./hkcstring")
    if strings:
        return [(entry.text or "") for entry in strings]
    if item.find("./hkobject") is not None:
        return None
    text = (item.text or "").strip()
    if item.get("numelements") is not None:
        return [scalar(token) for token in text.split()] if text else []
    return scalar(text)


def parameters(obj: ET.Element, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    result = {}
    for item in obj.findall("./hkparam"):
        name = item.get("name")
        if not name or name in excluded or item.find("./hkobject") is not None:
            continue
        result[name] = value(item)
    return result


def object_references(obj: ET.Element) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for item in obj.findall("./hkparam"):
        name = item.get("name") or ""
        for target in REFERENCE.findall("".join(item.itertext())):
            result[target].add(name)
    return result


def parse_trigger_array(obj: ET.Element, event_name: Any) -> list[dict[str, Any]]:
    result = []
    container = param(obj, "triggers")
    if container is None:
        return result
    for trigger in container.findall("./hkobject"):
        event = param(trigger, "event")
        event_obj = event.find("./hkobject") if event is not None else None
        event_id = value(param(event_obj, "id")) if event_obj is not None else None
        entry = {
            "local_time": value(param(trigger, "localTime")),
            "event_id": event_id,
            "relative_to_end": value(param(trigger, "relativeToEndOfClip")),
            "acyclic": value(param(trigger, "acyclic")),
            "is_annotation": value(param(trigger, "isAnnotation")),
        }
        name = event_name(event_id)
        if name is not None:
            entry["event_name"] = name
        payload = value(param(event_obj, "payload")) if event_obj is not None else None
        if payload is not None:
            entry["payload"] = payload
        result.append(entry)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Skyrim behavior HKX or XML.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--match", help="Regex matched against clip name and animation name.")
    parser.add_argument("--context-depth", type=int, default=2, help="Incoming-reference depth. Default: 2.")
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    if args.context_depth < 0:
        parser.error("--context-depth must be non-negative")
    try:
        matcher = re.compile(args.match) if args.match else None
    except re.error as error:
        parser.error(f"invalid --match regex: {error}")

    input_display = args.input.resolve(strict=True)
    input_native = native_path(args.input)
    temporary = None
    xml_path = input_native
    if args.input.suffix.casefold() != ".xml":
        temporary = tempfile.TemporaryDirectory()
        xml_path = str(Path(temporary.name) / "behavior.xml")
        try:
            completed = subprocess.run(
                ["hkxc", "convert", "-i", input_native, "-o", xml_path, "-v", "xml"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            parser.error("hkxc not found; run through 'mise exec --'")
        if completed.returncode:
            message = (completed.stderr or completed.stdout).strip()
            parser.error(message or f"hkxc exited with code {completed.returncode}")

    try:
        root = ET.parse(xml_path).getroot()
    finally:
        if temporary:
            temporary.cleanup()

    with open(input_native, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()

    objects = {
        obj.get("name"): obj
        for obj in root.findall(".//hkobject[@name]")
        if obj.get("name")
    }
    edges: dict[str, dict[str, set[str]]] = {}
    incoming: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for object_id, obj in objects.items():
        edges[object_id] = object_references(obj)
        for target, names in edges[object_id].items():
            incoming[target][object_id].update(names)

    event_tables = {}
    for object_id, obj in objects.items():
        if obj.get("class") == "hkbBehaviorGraphStringData":
            names = value(param(obj, "eventNames"))
            event_tables[object_id] = names if isinstance(names, list) else []

    def event_name(event_id: Any) -> str | None:
        if not isinstance(event_id, int) or event_id < 0:
            return None
        names = {
            table[event_id]
            for table in event_tables.values()
            if event_id < len(table) and table[event_id]
        }
        return next(iter(names)) if len(names) == 1 else None

    clips = []
    context_ids = set()
    trigger_ids = set()
    for object_id, obj in objects.items():
        if obj.get("class") != "hkbClipGenerator":
            continue
        name = value(param(obj, "name")) or ""
        animation = value(param(obj, "animationName")) or ""
        if matcher and not (matcher.search(name) or matcher.search(animation)):
            continue

        trigger_id = value(param(obj, "triggers"))
        if isinstance(trigger_id, str) and trigger_id in objects:
            trigger_ids.add(trigger_id)
        else:
            trigger_id = None

        context = []
        seen = {object_id}
        frontier = {object_id}
        for depth in range(1, args.context_depth + 1):
            found: dict[str, set[str]] = defaultdict(set)
            for child in sorted(frontier):
                for parent, names in sorted(incoming.get(child, {}).items()):
                    if parent not in seen:
                        found[parent].update(names)
            if not found:
                break
            for parent in sorted(found):
                context.append({"id": parent, "depth": depth, "via": sorted(found[parent])})
            frontier = set(found)
            seen.update(frontier)
            context_ids.update(frontier)

        clips.append(
            {
                "id": object_id,
                "name": name,
                "animation_name": animation,
                "parameters": parameters(obj, {"name", "animationName", "triggers"}),
                "trigger_array": trigger_id,
                "context": context,
            }
        )

    trigger_arrays = {
        object_id: parse_trigger_array(objects[object_id], event_name)
        for object_id in sorted(trigger_ids)
    }

    weight_ids = set()
    for object_id in context_ids | {clip["id"] for clip in clips}:
        for target in edges.get(object_id, {}):
            obj = objects.get(target)
            if obj is not None and obj.get("class") == "hkbBoneWeightArray":
                weight_ids.add(target)

    bone_weights = {}
    for object_id in sorted(weight_ids):
        weights = value(param(objects[object_id], "boneWeights"))
        values = weights if isinstance(weights, list) else []
        bone_weights[object_id] = {"count": len(values), "values": values}

    context_objects = {}
    for object_id in sorted(context_ids - trigger_ids - weight_ids):
        obj = objects[object_id]
        context_objects[object_id] = {
            "class": obj.get("class"),
            "name": value(param(obj, "name")),
            "parameters": parameters(obj, {"name"}),
        }

    report = {
        "file": {
            "path": str(input_display),
            "size": os.stat(input_native).st_size,
            "sha256": digest,
        },
        "behavior": {
            "class_version": root.get("classversion"),
            "contents_version": root.get("contentsversion"),
            "top_level_object": root.get("toplevelobject"),
        },
        "clips": clips,
        "trigger_arrays": trigger_arrays,
        "context_objects": context_objects,
        "bone_weight_arrays": bone_weights,
    }

    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
