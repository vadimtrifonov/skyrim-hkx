#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path
from typing import Any


VIEW_NAMES = {
    0: "isometric from NW",
    1: "isometric from SW",
    2: "isometric from SE",
    3: "isometric from NE",
    4: "top (up = N)",
    5: "south",
    6: "east",
    7: "north",
    8: "west",
    9: "bottom (up = S)",
    10: "isometric from N",
    11: "isometric from W",
    12: "isometric from S",
    13: "isometric from E",
    14: "top (up = NE)",
    15: "southwest",
    16: "southeast",
    17: "northeast",
    18: "northwest",
    19: "bottom (up = SW)",
}

DEBUG_NAMES = {
    0: "normal",
    1: "TriShape block IDs",
    2: "depth",
    3: "normals",
    4: "diffuse texture only",
    5: "light only",
}


class RenderError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalize_asset(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if (
        not normalized
        or any(part in ("", ".", "..") for part in parts)
        or any(character in normalized for character in "*?[]\t\r\n")
        or ":" in normalized
    ):
        raise RenderError("asset must be an exact, relative archive path without wildcards")
    if not normalized.casefold().endswith(".nif"):
        raise RenderError("asset must have a .nif extension")
    # FO76Utils normalizes archive and loose-file paths to lowercase before
    # applying its case-sensitive substring filter.
    return "/".join(parts).lower()


def locate_nif_info() -> Path:
    root = os.environ.get("FO76UTILS_ROOT")
    candidates = [Path(root) / "nif_info.exe"] if root else []
    found = shutil.which("nif_info.exe") or shutil.which("nif_info")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve(strict=True)
    raise RenderError("nif_info.exe was not found; run this script through 'mise exec --'")


def run_tool(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RenderError(f"nif_info timed out after {timeout:g} seconds") from error
    except OSError as error:
        raise RenderError(f"could not run nif_info: {error}") from error


def parse_query_output(output: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        try:
            size = int(fields[-1])
        except ValueError:
            continue
        matches.append(
            {
                "author": "\t".join(fields[:-2]),
                "path": fields[-2].replace("\\", "/").strip("/"),
                "size": size,
            }
        )
    return matches


def require_exact_asset(
    executable: Path, source: Path, requested_asset: str, timeout: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    command = [str(executable), "-q", "--", str(source), requested_asset]
    process = run_tool(command, timeout)
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "unknown error"
        raise RenderError(f"nif_info asset query failed ({process.returncode}): {detail}")

    matches = parse_query_output(process.stdout)
    exact = [item for item in matches if item["path"].casefold() == requested_asset.casefold()]
    if len(matches) != 1 or len(exact) != 1:
        candidates = ", ".join(item["path"] for item in matches[:8]) or "none"
        raise RenderError(
            "asset did not resolve to exactly one exact NIF; "
            f"requested '{requested_asset}', candidates: {candidates}"
        )
    query = {
        "return_code": process.returncode,
        "stderr": process.stderr.splitlines(),
    }
    return exact[0], query


def channel(value: int, mask: int, default: int) -> int:
    if not mask:
        return default
    shift = (mask & -mask).bit_length() - 1
    maximum = mask >> shift
    component = (value & mask) >> shift
    return (component * 255 + maximum // 2) // maximum


def decode_dds(path: Path, expected_width: int, expected_height: int) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if len(data) < 128 or data[:4] != b"DDS ":
        raise RenderError("nif_info did not produce a valid DDS file")
    header_size = struct.unpack_from("<I", data, 4)[0]
    height, width = struct.unpack_from("<II", data, 12)
    pitch = struct.unpack_from("<I", data, 20)[0]
    pixel_format_size = struct.unpack_from("<I", data, 76)[0]
    pixel_format_flags = struct.unpack_from("<I", data, 80)[0]
    four_cc = data[84:88]
    bits_per_pixel = struct.unpack_from("<I", data, 88)[0]
    masks = struct.unpack_from("<IIII", data, 92)

    if header_size != 124 or pixel_format_size != 32:
        raise RenderError("unsupported DDS header produced by nif_info")
    if (width, height) != (expected_width, expected_height):
        raise RenderError(
            f"nif_info produced unexpected DDS dimensions {width}x{height}; "
            f"expected {expected_width}x{expected_height}"
        )
    if bits_per_pixel != 32 or four_cc != b"\0\0\0\0" or not (pixel_format_flags & 0x40):
        raise RenderError("only the uncompressed 32-bit DDS output from nif_info is supported")
    if not all(masks[:3]):
        raise RenderError("DDS output is missing RGB channel masks")

    minimum_stride = width * 4
    row_stride = pitch if pitch >= minimum_stride else minimum_stride
    required_size = 128 + (row_stride * height)
    if len(data) < required_size:
        raise RenderError("DDS pixel data is truncated")

    rgba = bytearray(width * height * 4)
    standard_bgra = masks == (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    for y in range(height):
        source_row = data[128 + y * row_stride : 128 + y * row_stride + minimum_stride]
        destination_start = y * minimum_stride
        destination_end = destination_start + minimum_stride
        if standard_bgra:
            destination = rgba[destination_start:destination_end]
            destination[0::4] = source_row[2::4]
            destination[1::4] = source_row[1::4]
            destination[2::4] = source_row[0::4]
            destination[3::4] = source_row[3::4]
            rgba[destination_start:destination_end] = destination
            continue
        for x in range(width):
            value = struct.unpack_from("<I", source_row, x * 4)[0]
            offset = destination_start + x * 4
            rgba[offset : offset + 4] = bytes(
                (
                    channel(value, masks[0], 0),
                    channel(value, masks[1], 0),
                    channel(value, masks[2], 0),
                    channel(value, masks[3], 255),
                )
            )
    return width, height, bytes(rgba)


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    row_size = width * 4
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        scanlines.extend(rgba[y * row_size : (y + 1) * row_size])
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(scanlines), 9))
        + png_chunk(b"IEND", b"")
    )


def image_statistics(width: int, height: int, rgba: bytes) -> dict[str, Any]:
    visible_pixels = 0
    minimum_x = width
    minimum_y = height
    maximum_x = -1
    maximum_y = -1

    # FO76Utils initializes untouched render pixels to transparent black. Use
    # alpha rather than a sampled corner color so uniform, full-frame debug
    # renders are not mistaken for blank output.
    for y in range(height):
        row_start = y * width * 4
        for x in range(width):
            offset = row_start + x * 4
            if not rgba[offset + 3]:
                continue
            visible_pixels += 1
            minimum_x = min(minimum_x, x)
            minimum_y = min(minimum_y, y)
            maximum_x = max(maximum_x, x)
            maximum_y = max(maximum_y, y)

    pixel_count = width * height
    bounds = None
    if visible_pixels:
        bounds = {
            "min": [minimum_x, minimum_y],
            "max": [maximum_x, maximum_y],
            "width": maximum_x - minimum_x + 1,
            "height": maximum_y - minimum_y + 1,
        }
    ratio = visible_pixels / pixel_count
    return {
        "background_rgba": [0, 0, 0, 0],
        # Preserve the original field names for report compatibility while
        # defining content by visible (non-zero-alpha) pixels.
        "non_background_pixels": visible_pixels,
        "non_background_ratio": ratio,
        "nonzero_alpha_pixels": visible_pixels,
        "content_bounds": bounds,
        "blank": visible_pixels == 0,
        "likely_blank": ratio < 0.001,
    }


def float_argument(value: float) -> str:
    return format(value, ".9g")


def path_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render one exact NIF asset to PNG with FO76Utils nif_info."
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Data-like directory or BSA/BA2 archive searched by nif_info.",
    )
    parser.add_argument(
        "asset",
        help="Exact relative NIF path, for example meshes/weapons/iron/longsword.nif.",
    )
    parser.add_argument("-o", "--output", required=True, type=Path, help="New PNG output path.")
    parser.add_argument("--report", type=Path, help="Write the JSON report here instead of stdout.")
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=512)
    parser.add_argument("--direction", type=int, choices=range(20), default=0)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--rotate-x", type=float, default=0.0)
    parser.add_argument("--rotate-y", type=float, default=0.0)
    parser.add_argument("--rotate-z", type=float, default=0.0)
    parser.add_argument("--light-scale", type=float, default=1.0)
    parser.add_argument("--light-y", type=float, default=56.25)
    parser.add_argument("--light-z", type=float, default=-135.0)
    parser.add_argument("--debug", type=int, choices=range(6), default=0)
    parser.add_argument("--enable-markers", action="store_true")
    parser.add_argument("--keep-dds", action="store_true", help="Keep the intermediate DDS beside the PNG.")
    parser.add_argument("--force", action="store_true", help="Replace existing output files.")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-command timeout in seconds.")
    args = parser.parse_args()

    try:
        source = args.source.resolve(strict=True)
        if not source.is_dir() and not source.is_file():
            raise RenderError(f"source is not a directory or file: {source}")
        if source.is_file() and source.suffix.casefold() not in (".bsa", ".ba2"):
            raise RenderError("a file source must be a .bsa or .ba2 archive")
        asset = normalize_asset(args.asset)
        output = args.output.resolve(strict=False)
        report_path = args.report.resolve(strict=False) if args.report else None
        dds_output = output.with_suffix(".dds") if args.keep_dds else None

        if output.suffix.casefold() != ".png":
            raise RenderError("--output must have a .png extension")
        if report_path and report_path.suffix.casefold() != ".json":
            raise RenderError("--report must have a .json extension")
        if not 16 <= args.width <= 4096 or not 16 <= args.height <= 4096:
            raise RenderError("--width and --height must be between 16 and 4096")
        numeric_values = {
            "--scale": args.scale,
            "--rotate-x": args.rotate_x,
            "--rotate-y": args.rotate_y,
            "--rotate-z": args.rotate_z,
            "--light-scale": args.light_scale,
            "--light-y": args.light_y,
            "--light-z": args.light_z,
            "--timeout": args.timeout,
        }
        if any(not math.isfinite(value) for value in numeric_values.values()):
            raise RenderError("numeric options must be finite")
        if not (1.0 / 512.0) <= args.scale <= 16.0:
            raise RenderError("--scale must be between 1/512 and 16")
        if not 0.125 <= args.light_scale <= 4.0:
            raise RenderError("--light-scale must be between 0.125 and 4")
        if any(
            not -360.0 <= value <= 360.0
            for value in (args.rotate_x, args.rotate_y, args.rotate_z, args.light_y, args.light_z)
        ):
            raise RenderError("rotation options must be between -360 and 360 degrees")
        if args.timeout <= 0:
            raise RenderError("--timeout must be greater than zero")
        if source.is_dir() and path_within(output, source):
            raise RenderError("write previews outside the source/Data directory")
        if report_path and source.is_dir() and path_within(report_path, source):
            raise RenderError("write reports outside the source/Data directory")
        if report_path and report_path == output:
            raise RenderError("--report and --output must be different paths")

        destinations = [output]
        if report_path:
            destinations.append(report_path)
        if dds_output:
            destinations.append(dds_output)
        existing = [path for path in destinations if path.exists()]
        if existing and not args.force:
            raise RenderError(
                "refusing to overwrite existing output: " + ", ".join(str(path) for path in existing)
            )
        if any(path.is_dir() for path in existing):
            raise RenderError("an output path names an existing directory")

        output.parent.mkdir(parents=True, exist_ok=True)
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
        executable = locate_nif_info()
        match, query = require_exact_asset(executable, source, asset, args.timeout)
        resolved_asset = match["path"]

        with tempfile.TemporaryDirectory(prefix=".nif-preview-", dir=output.parent) as temporary_name:
            temporary_directory = Path(temporary_name)
            temporary_dds = temporary_directory / "render.dds"
            command = [
                str(executable),
                f"-render{args.width}x{args.height}",
                str(temporary_dds),
                "-cam",
                float_argument(args.scale),
                str(args.direction),
                float_argument(args.rotate_x),
                float_argument(args.rotate_y),
                float_argument(args.rotate_z),
                "-light",
                float_argument(args.light_scale),
                float_argument(args.light_y),
                float_argument(args.light_z),
                "-debug",
                str(args.debug),
            ]
            if args.enable_markers:
                command.append("-enable-markers")
            command.extend(("--", str(source), resolved_asset))
            process = run_tool(command, args.timeout)
            if process.returncode != 0:
                detail = process.stderr.strip() or process.stdout.strip() or "unknown error"
                raise RenderError(f"nif_info render failed ({process.returncode}): {detail}")
            if not temporary_dds.is_file() or temporary_dds.stat().st_size == 0:
                raise RenderError("nif_info returned success but did not produce a DDS image")

            width, height, rgba = decode_dds(temporary_dds, args.width, args.height)
            statistics = image_statistics(width, height, rgba)
            png = encode_png(width, height, rgba)
            png_sha256 = hashlib.sha256(png).hexdigest()
            dds_sha256 = sha256_file(temporary_dds)
            dds_size = temporary_dds.stat().st_size
            dds_data = temporary_dds.read_bytes() if dds_output else None

        loose_asset = None
        if source.is_dir():
            candidate = source.joinpath(*resolved_asset.split("/"))
            if candidate.is_file():
                loose_asset = {
                    "path": str(candidate.resolve(strict=True)),
                    "size": candidate.stat().st_size,
                    "sha256": sha256_file(candidate),
                }

        warnings: list[str] = []
        if statistics["blank"]:
            warnings.append("The render is completely blank; FO76Utils may not support this mesh.")
        elif statistics["likely_blank"]:
            warnings.append("The render contains very few visible pixels; inspect it manually.")
        if source.is_file():
            warnings.append(
                "A single archive source may not contain referenced textures from other archives; "
                "prefer the containing Data directory."
            )

        report: dict[str, Any] = {
            "tool": {
                "name": "FO76Utils nif_info",
                "release": os.environ.get("FO76UTILS_VERSION"),
                "license": "MIT",
                "executable": str(executable),
                "sha256": sha256_file(executable),
            },
            "source": {
                "path": str(source),
                "kind": "directory" if source.is_dir() else "archive",
                "requested_asset": asset,
                "resolved_asset": resolved_asset,
                "asset_size": match["size"],
                "author": match["author"] or None,
                "loose_asset": loose_asset,
            },
            "render": {
                "width": width,
                "height": height,
                "camera": {
                    "scale": args.scale,
                    "direction": args.direction,
                    "direction_name": VIEW_NAMES[args.direction],
                    "model_rotation": [args.rotate_x, args.rotate_y, args.rotate_z],
                },
                "light": {
                    "scale": args.light_scale,
                    "rotation_y": args.light_y,
                    "rotation_z": args.light_z,
                },
                "debug_mode": args.debug,
                "debug_mode_name": DEBUG_NAMES[args.debug],
                "markers_enabled": args.enable_markers,
            },
            "output": {
                "png": {
                    "path": str(output),
                    "size": len(png),
                    "sha256": png_sha256,
                },
                "dds": {
                    "path": str(dds_output) if dds_output else None,
                    "kept": bool(dds_output),
                    "size": dds_size,
                    "sha256": dds_sha256,
                },
                "statistics": statistics,
            },
            "process": {
                "query": query,
                "render": {
                    "return_code": process.returncode,
                    "stdout": process.stdout.splitlines(),
                    "stderr": process.stderr.splitlines(),
                },
            },
            "warnings": warnings,
        }

        serialized = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        writes = [(output, png)]
        if dds_output and dds_data is not None:
            writes.append((dds_output, dds_data))
        if report_path:
            writes.append((report_path, serialized))
        for destination, data in writes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if args.force:
                destination.unlink(missing_ok=True)
            with destination.open("xb") as stream:
                stream.write(data)
        if not report_path:
            sys.stdout.buffer.write(serialized)

        if statistics["blank"]:
            print("render_nif: render is blank; see the JSON report", file=sys.stderr)
            return 3
        return 0
    except (OSError, RenderError, struct.error, zlib.error) as error:
        print(f"render_nif: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
