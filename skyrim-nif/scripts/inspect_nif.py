#!/usr/bin/env python3

import argparse
import hashlib
import json
import logging
import os
import sys
from collections import Counter
from ctypes import create_string_buffer
from pathlib import Path
from typing import Any, Callable


MISSING = object()
ERRORS: list[dict[str, str]] = []
TEXTURES: set[str] = set()
MATERIALS: set[str] = set()
BEHAVIOR_GRAPHS: set[str] = set()
SEGMENT_FILES: set[str] = set()


class InspectError(RuntimeError):
    pass


def validated_output_path(output: Path, force: bool) -> Path:
    destination = output.resolve(strict=False)
    if destination.suffix.casefold() != ".json":
        raise InspectError("--output must have a .json extension")
    if destination.exists():
        if destination.is_dir():
            raise InspectError(f"output path is a directory: {destination}")
        if not force:
            raise InspectError(f"refusing to overwrite existing output: {destination}")
    return destination


def native_path(path: Path) -> str:
    resolved = str(path.resolve(strict=True))
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def capture(context: str, operation: Callable[[], Any], default: Any = None) -> Any:
    try:
        return operation()
    except Exception as error:
        ERRORS.append({"context": context, "error": str(error) or type(error).__name__})
        return default


def optional_attr(value: Any, name: str) -> Any:
    try:
        return getattr(value, name)
    except Exception:
        return MISSING


def scalar(value: Any) -> Any:
    if value is MISSING:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [scalar(item) for item in value]
    if not isinstance(value, (str, bytes, dict)):
        try:
            return [scalar(value[index]) for index in range(len(value))]
        except (IndexError, TypeError):
            pass
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)


def vector(value: Any, width: int) -> list[float] | None:
    if value is None:
        return None
    try:
        return [float(value[index]) for index in range(width)]
    except (IndexError, TypeError, ValueError):
        return None


def transform_report(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    translation = vector(optional_attr(value, "translation"), 3)
    rotation_value = optional_attr(value, "rotation")
    rotation = None
    if rotation_value is not MISSING:
        try:
            rotation = [vector(rotation_value[row], 3) for row in range(3)]
        except (IndexError, TypeError):
            rotation = None
    scale_value = optional_attr(value, "scale")
    try:
        scale = float(scale_value) if scale_value is not MISSING else None
    except (TypeError, ValueError):
        scale = None
    return {"translation": translation, "rotation": rotation, "scale": scale}


def object_ref(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    result: dict[str, Any] = {}
    for field, attribute in (("id", "id"), ("type", "blockname"), ("name", "name")):
        item = optional_attr(value, attribute)
        if item is not MISSING and item is not None:
            result[field] = scalar(item)
    return result


def block_inventory(nif: Any, nifly: Any) -> list[dict[str, Any]]:
    result = []
    buffer_size = max(128, nif.max_string_len)
    for block_id in range(1_000_000):
        buffer = create_string_buffer(buffer_size)
        length = nifly.getBlockname(nif._handle, block_id, buffer, buffer_size)
        if length <= 0 or not buffer.value:
            break
        result.append({"id": block_id, "type": buffer.value.decode("utf-8", errors="replace")})
    else:
        ERRORS.append({"context": "blocks", "error": "Block enumeration exceeded the safety limit"})
    return result


def bounds_report(vertices: list[Any]) -> dict[str, list[float]] | None:
    if not vertices:
        return None
    columns = [[float(vertex[axis]) for vertex in vertices] for axis in range(3)]
    return {
        "min": [min(column) for column in columns],
        "max": [max(column) for column in columns],
    }


def extra_data_report(owner: Any, context: str) -> list[dict[str, Any]]:
    method = optional_attr(owner, "extra_data")
    if method is MISSING or not callable(method):
        return []
    entries = capture(f"{context}.extra_data", lambda: list(method()), [])
    result = []
    for index, item in enumerate(entry for entry in entries if entry is not None):
        entry = object_ref(item) or {}
        flags = optional_attr(item, "flags")
        if flags is not MISSING:
            entry["flags"] = scalar(flags)
        else:
            properties = optional_attr(item, "properties")
            if properties is not MISSING:
                flags = optional_attr(properties, "flags")
                if flags is not MISSING:
                    entry["flags"] = scalar(flags)

        for output_name, attribute in (
            ("integer", "integer_data"),
            ("string", "string_data"),
            ("behavior_graph", "behavior_graph_file"),
            ("controls_base_skeleton", "controls_base_skeleton"),
            ("center", "center"),
            ("half_extents", "half_extents"),
            ("keys", "keys"),
        ):
            value = optional_attr(item, attribute)
            if value is not MISSING:
                entry[output_name] = scalar(value)

        behavior_path = entry.get("behavior_graph")
        if isinstance(behavior_path, str) and behavior_path:
            BEHAVIOR_GRAPHS.add(behavior_path)
        result.append(entry)
    return result


def partition_report(shape: Any, context: str) -> dict[str, Any]:
    partitions = capture(f"{context}.partitions", lambda: list(shape.partitions), [])
    triangle_map = capture(f"{context}.partition_triangles", lambda: list(shape.partition_tris), [])
    triangle_counts = Counter(int(index) for index in triangle_map)
    entries = []
    for index, partition in enumerate(partitions):
        entry = {
            "index": index,
            "id": scalar(optional_attr(partition, "id")),
            "name": scalar(optional_attr(partition, "name")),
            "triangle_count": triangle_counts.get(index, 0),
        }
        for output_name, attribute in (("flags", "flags"), ("user_slot", "user_slot"), ("material", "material")):
            value = optional_attr(partition, attribute)
            if value is not MISSING:
                entry[output_name] = scalar(value)
        subsegments = optional_attr(partition, "subsegments")
        if subsegments is not MISSING and subsegments:
            entry["subsegments"] = [
                {
                    "id": scalar(optional_attr(item, "id")),
                    "name": scalar(optional_attr(item, "name")),
                    "user_slot": scalar(optional_attr(item, "user_slot")),
                    "material": scalar(optional_attr(item, "material")),
                }
                for item in subsegments
            ]
        entries.append(entry)

    segment_file = capture(f"{context}.segment_file", lambda: shape.segment_file, "")
    if segment_file:
        SEGMENT_FILES.add(segment_file)
    return {
        "segment_file": segment_file or None,
        "triangle_assignment_count": len(triangle_map),
        "entries": entries,
    }


def shader_report(shape: Any, context: str) -> dict[str, Any] | None:
    shader = capture(f"{context}.shader", lambda: shape.shader, None)
    if shader is None:
        return None
    result = object_ref(shader) or {}
    properties = capture(f"{context}.shader.properties", lambda: shader.properties, None)
    if properties is not None:
        for output_name, attribute in (
            ("flags_1", "Shader_Flags_1"),
            ("flags_2", "Shader_Flags_2"),
            ("shader_type", "Shader_Type"),
        ):
            value = optional_attr(properties, attribute)
            if value is not MISSING:
                result[output_name] = scalar(value)

    textures = capture(f"{context}.textures", lambda: dict(shape.textures), {})
    clean_textures = {}
    for slot, path in textures.items():
        path_text = str(path) if path is not None else ""
        clean_textures[str(slot)] = path_text
        if not path_text:
            continue
        if str(slot) == "RootMaterialPath":
            MATERIALS.add(path_text)
        else:
            TEXTURES.add(path_text)
    result["textures"] = clean_textures
    return result


def alpha_report(shape: Any, context: str) -> dict[str, Any] | None:
    alpha = capture(f"{context}.alpha", lambda: shape.alpha_property, None)
    if alpha is None:
        return None
    result = object_ref(alpha) or {}
    properties = capture(f"{context}.alpha.properties", lambda: alpha.properties, None)
    if properties is not None:
        for name in ("flags", "threshold"):
            value = optional_attr(properties, name)
            if value is not MISSING:
                result[name] = scalar(value)
    return result


def shape_report(shape: Any, index: int, include_geometry: bool, include_bones: bool) -> dict[str, Any]:
    context = f"shapes[{index}]"
    properties = capture(f"{context}.properties", lambda: shape.properties, None)
    result = object_ref(shape) or {"index": index}
    result["index"] = index
    result["parent"] = capture(f"{context}.parent", lambda: object_ref(shape.parent), None)
    result["local_transform"] = capture(
        f"{context}.local_transform", lambda: transform_report(shape.transform), None
    )
    result["global_transform"] = capture(
        f"{context}.global_transform", lambda: transform_report(shape.global_transform), None
    )

    vertex_count = scalar(optional_attr(properties, "vertexCount")) if properties is not None else None
    triangle_count = scalar(optional_attr(properties, "triangleCount")) if properties is not None else None
    vertex_colors = optional_attr(properties, "hasVertexColors") if properties is not None else MISSING
    result["geometry"] = {
        "vertex_count": vertex_count,
        "triangle_count": triangle_count,
        "has_vertex_colors": bool(vertex_colors) if vertex_colors is not MISSING else None,
    }
    if include_geometry:
        vertices = capture(f"{context}.vertices", lambda: list(shape.verts or []), [])
        triangles = capture(f"{context}.triangles", lambda: list(shape.tris or []), [])
        uvs = capture(f"{context}.uvs", lambda: list(shape.uvs or []), [])
        normals = capture(f"{context}.normals", lambda: list(shape.normals or []), [])
        colors = capture(f"{context}.colors", lambda: list(shape.colors or []), [])
        result["geometry"].update(
            {
                "actual_vertex_count": len(vertices),
                "actual_triangle_count": len(triangles),
                "uv_count": len(uvs),
                "normal_count": len(normals),
                "color_count": len(colors),
                "bounds": bounds_report(vertices),
            }
        )

    result["shader"] = shader_report(shape, context)
    result["alpha"] = alpha_report(shape, context)

    has_skin = bool(capture(f"{context}.has_skin", lambda: shape.has_skin_instance, False))
    skin = {
        "present": has_skin,
        "instance_type": capture(f"{context}.skin_instance", lambda: shape.skin_instance_name, "") or None,
        "has_global_to_skin": bool(capture(f"{context}.global_to_skin", lambda: shape.has_global_to_skin, False)),
    }
    if has_skin:
        bone_names = capture(f"{context}.bone_names", lambda: list(shape.bone_names), [])
        used_bones = capture(f"{context}.used_bones", lambda: list(shape.get_used_bones()), [])
        skin["bone_count"] = len(bone_names)
        skin["used_bone_count"] = len(used_bones)
        if include_bones:
            skin["bones"] = bone_names
            skin["used_bones"] = sorted(str(item) for item in used_bones)
    result["skinning"] = skin
    result["partitions"] = partition_report(shape, context)
    result["extra_data"] = extra_data_report(shape, context)
    return result


def collision_shape_report(value: Any, include_geometry: bool, seen: set[int] | None = None) -> dict[str, Any] | None:
    if value is None:
        return None
    seen = seen or set()
    block_id = optional_attr(value, "id")
    identity = int(block_id) if block_id is not MISSING else id(value)
    if identity in seen:
        return {"id": scalar(block_id), "cycle": True}
    seen.add(identity)

    result = object_ref(value) or {}
    properties = optional_attr(value, "properties")
    if properties is not MISSING:
        details = {}
        for name in (
            "material",
            "radius",
            "radius1",
            "radius2",
            "dimensions",
            "point1",
            "point2",
            "buildType",
        ):
            item = optional_attr(properties, name)
            if item is not MISSING:
                details[name] = scalar(item)
        if details:
            result["properties"] = details

    if include_geometry:
        vertices = optional_attr(value, "vertices")
        triangles = optional_attr(value, "triangles")
        if vertices is not MISSING:
            vertices = list(vertices or [])
            result["vertex_count"] = len(vertices)
            result["bounds"] = bounds_report(vertices)
        if triangles is not MISSING:
            result["triangle_count"] = len(list(triangles or []))

    child = optional_attr(value, "child")
    if child is not MISSING and child is not None:
        result["child"] = collision_shape_report(child, include_geometry, seen)
    children = optional_attr(value, "children")
    if children is not MISSING and children:
        result["children"] = [
            collision_shape_report(item, include_geometry, seen) for item in children
        ]
    return result


def collision_reports(nif: Any, include_geometry: bool) -> list[dict[str, Any]]:
    capture("nodes.load", lambda: nif.nodes, {})
    result = []
    seen_collisions: set[int] = set()
    for node_id, node in sorted(nif.node_ids.items()):
        collision = optional_attr(node, "collision_object")
        if collision is MISSING or collision is None:
            continue
        collision_id = optional_attr(collision, "id")
        identity = int(collision_id) if collision_id is not MISSING else id(collision)
        if identity in seen_collisions:
            continue
        seen_collisions.add(identity)
        context = f"collision[{identity}]"
        entry: dict[str, Any] = {
            "target": object_ref(node),
            "object": object_ref(collision),
        }
        flags = optional_attr(collision, "flags")
        if flags is not MISSING:
            entry["object"]["flags"] = scalar(flags)
        body = capture(f"{context}.body", lambda: collision.body, None)
        if body is not None:
            body_report = object_ref(body) or {}
            properties = capture(f"{context}.body.properties", lambda: body.properties, None)
            if properties is not None:
                body_properties = {}
                for name in (
                    "collisionFilter_layer",
                    "broadPhaseType",
                    "collisionResponse",
                    "motionSystem",
                    "qualityType",
                    "mass",
                    "friction",
                    "restitution",
                ):
                    item = optional_attr(properties, name)
                    if item is not MISSING:
                        body_properties[name] = scalar(item)
                body_report["properties"] = body_properties
            body_shape = capture(f"{context}.shape", lambda: body.shape, None)
            body_report["shape"] = collision_shape_report(body_shape, include_geometry)
            entry["body"] = body_report
        result.append(entry)
    return result


def node_reports(nif: Any) -> list[dict[str, Any]]:
    capture("nodes.load", lambda: nif.nodes, {})
    result = []
    for node_id, node in sorted(nif.node_ids.items()):
        transform = optional_attr(node, "transform")
        name = optional_attr(node, "name")
        if transform is MISSING or name is MISSING:
            continue
        context = f"nodes[{node_id}]"
        entry = object_ref(node) or {"id": node_id}
        entry["parent"] = capture(f"{context}.parent", lambda: object_ref(node.parent), None)
        entry["transform"] = capture(
            f"{context}.transform", lambda: transform_report(node.transform), None
        )
        flags = optional_attr(node, "flags")
        if flags is not MISSING:
            entry["flags"] = scalar(flags)
        entry["extra_data"] = extra_data_report(node, context)
        result.append(entry)
    return result


def resolve_asset(path_text: str, data_root: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"path": path_text}
    if data_root is None:
        return result

    def invalid(reason: str) -> dict[str, Any]:
        result.update(
            {
                "loose_path": None,
                "exists_loose": False,
                "invalid_reason": reason,
            }
        )
        return result

    normalized = path_text.replace("\\", "/")
    if not normalized:
        return invalid("empty asset path")
    if normalized.startswith("/"):
        return invalid("absolute asset path")
    if normalized.casefold().startswith("data/"):
        normalized = normalized[5:]

    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return invalid("asset path contains an empty, dot, or parent component")
    if any(":" in part for part in parts):
        return invalid("asset path contains a drive or alternate-stream separator")
    if any(ord(character) < 32 for character in normalized):
        return invalid("asset path contains a control character")

    try:
        candidate = data_root.joinpath(*parts).resolve(strict=False)
        candidate.relative_to(data_root)
    except (OSError, RuntimeError, ValueError):
        return invalid("asset path cannot be resolved beneath the Data root")
    result["loose_path"] = str(candidate)
    result["exists_loose"] = candidate.is_file()
    return result


def dependency_report(data_root: Path | None) -> dict[str, Any]:
    return {
        "textures": [resolve_asset(path, data_root) for path in sorted(TEXTURES, key=str.casefold)],
        "materials": [resolve_asset(path, data_root) for path in sorted(MATERIALS, key=str.casefold)],
        "behavior_graphs": [resolve_asset(path, data_root) for path in sorted(BEHAVIOR_GRAPHS, key=str.casefold)],
        "segment_files": [resolve_asset(path, data_root) for path in sorted(SEGMENT_FILES, key=str.casefold)],
        "data_root": str(data_root) if data_root else None,
        "note": "Loose-file checks do not search BSA archives." if data_root else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Bethesda NIF with PyNifly and emit JSON.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--force", action="store_true", help="Replace an existing JSON output file.")
    parser.add_argument("--geometry", action="store_true", help="Load geometry arrays and include counts and bounds.")
    parser.add_argument("--bones", action="store_true", help="Include skin bone names.")
    parser.add_argument("--nodes", action="store_true", help="Include scene nodes and their transforms.")
    parser.add_argument("--blocks", action="store_true", help="Include the ordered NIF block list.")
    parser.add_argument("--data-root", type=Path, help="Check referenced assets as loose files beneath this Data directory.")
    args = parser.parse_args()

    if args.input.suffix.casefold() != ".nif":
        parser.error("input must have a .nif extension")
    input_display = args.input.resolve(strict=True)
    if not input_display.is_file():
        parser.error(f"input is not a file: {input_display}")
    if args.force and args.output is None:
        parser.error("--force requires --output")
    output_path = None
    if args.output:
        try:
            output_path = validated_output_path(args.output, args.force)
        except InspectError as error:
            parser.error(str(error))
    data_root = args.data_root.resolve(strict=True) if args.data_root else None
    if data_root is not None and not data_root.is_dir():
        parser.error(f"--data-root is not a directory: {data_root}")

    pynifly_root = os.environ.get("PYNIFLY_ROOT")
    if not pynifly_root:
        parser.error("PYNIFLY_ROOT is unset; run through 'mise exec --'.")
    pynifly_path = Path(pynifly_root)
    if not (pynifly_path / "NiflyDLL.dll").is_file():
        parser.error(f"NiflyDLL.dll not found under {pynifly_path}")
    sys.path.insert(0, str(pynifly_path))

    from pyn.pynifly import NifFile
    from pyn.niflydll import nifly

    logging.getLogger("pynifly").setLevel(logging.ERROR)
    input_native = native_path(args.input)
    NifFile.clear_log()
    nif = capture("file.load", lambda: NifFile(input_native), None)
    if nif is None:
        message = NifFile.message_log().strip()
        parser.error(message or f"could not load NIF: {input_display}")

    with open(input_native, "rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        stream.seek(0)
        header = stream.readline(512).rstrip(b"\r\n").decode("ascii", errors="replace")

    blocks = block_inventory(nif, nifly)
    type_counts = Counter(item["type"] for item in blocks)
    shapes = [
        shape_report(shape, index, args.geometry, args.bones)
        for index, shape in enumerate(nif.shapes)
    ]
    collisions = collision_reports(nif, args.geometry)
    root = capture("root", lambda: nif.root, None)
    root_report = object_ref(root)
    if root_report is not None:
        root_report["flags"] = scalar(optional_attr(root, "flags"))
        root_report["transform"] = capture("root.transform", lambda: transform_report(root.transform), None)
        root_report["extra_data"] = extra_data_report(root, "root")

    nodes = node_reports(nif) if args.nodes else None
    report: dict[str, Any] = {
        "file": {
            "path": str(input_display),
            "size": os.stat(input_native).st_size,
            "sha256": digest,
            "header": header,
        },
        "format": {
            "game": capture("game", lambda: nif.game, None),
            "block_count": len(blocks),
            "block_type_counts": dict(sorted(type_counts.items())),
        },
        "root": root_report,
        "summary": {
            "shape_count": len(shapes),
            "vertex_count": sum(int(item["geometry"].get("vertex_count") or 0) for item in shapes),
            "triangle_count": sum(int(item["geometry"].get("triangle_count") or 0) for item in shapes),
            "skinned_shape_count": sum(bool(item["skinning"]["present"]) for item in shapes),
            "collision_object_count": len(collisions),
            "node_count": capture("node_count", lambda: int(nifly.getNodeCount(nif._handle)), None),
        },
        "shapes": shapes,
        "collisions": collisions,
        "dependencies": dependency_report(data_root),
    }
    if nodes is not None:
        report["nodes"] = nodes
    if args.blocks:
        report["blocks"] = blocks

    native_messages = NifFile.message_log().strip()
    if native_messages:
        report["native_messages"] = native_messages.splitlines()
    if ERRORS:
        report["errors"] = ERRORS

    serialized = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if args.force:
                output_path.unlink(missing_ok=True)
            with output_path.open("xb") as stream:
                stream.write(serialized)
        else:
            sys.stdout.buffer.write(serialized)
    except OSError as error:
        print(f"inspect_nif: could not write report: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
