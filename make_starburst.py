"""Circular radial pleats with adjustable fold angle and inward edge draft.

All dimensions are in millimetres. A 210 mm starburst stands on a 225 mm
diameter, 9 mm thick circular base with a flat back at Z=0. Pleat height
grows linearly from zero at the centre. Its 29 peaks and 29 troughs give
58 equally spaced positions to align with the surrounding frame. The default
included fold angle is 70 degrees. The starburst side leans inward 35 degrees
from vertical, with its 210 mm footprint measured at the top of the base.

Generate the model:
    python make_starburst.py --fold-angle 70 --edge-draft 35 --preview

STL generation and validation use the Python standard library only.
Add --preview to render a PNG (requires Pillow).
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import json
import math
from pathlib import Path
import struct


def subtract(a, b):
    return tuple(x - y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def normal(a, b, c):
    value = cross(subtract(b, a), subtract(c, a))
    length = math.sqrt(dot(value, value))
    if length <= 1e-12:
        raise ValueError("Degenerate triangle")
    return tuple(v / length for v in value)


def build_model(base_diameter, diameter, pleats, backing, samples, rotation_degrees,
                fold_angle, edge_draft):
    radius = diameter / 2
    base_radius = base_diameter / 2
    alpha = math.pi / pleats
    cot_half = 1 / math.tan(alpha / 2)
    # Adjacent face normals make an angle of 180 - fold_angle. Solving for
    # the alternating radial slopes keeps the actual dihedral angle exact.
    k = 1 / math.sqrt(cot_half ** 2 * math.tan(math.radians(fold_angle) / 2) ** 2 - 1)
    half_amplitude = radius * k
    centre_z = backing + half_amplitude
    count = 2 * pleats * samples
    draft = math.tan(math.radians(edge_draft))
    peak_height = 2 * half_amplitude / (1 + draft * k)
    rotation = math.radians(rotation_degrees)
    vertices = [(0.0, 0.0, centre_z), (0.0, 0.0, 0.0)]
    top_rim, ledge_inner, ledge_outer, bottom_outer = [], [], [], []
    def add_vertex(vertex):
        vertices.append(vertex)
        return len(vertices) - 1
    for i in range(count):
        sector, step = divmod(i, samples)
        local = alpha * step / samples
        theta = rotation + alpha * sector + local
        sign = 1 if sector % 2 == 0 else -1
        radial_slope = sign * k * (math.cos(local) - cot_half * math.sin(local))
        # Intersect each planar pleat with the tapered skirt:
        #   z - backing = half_amplitude + radial_slope * top_radius
        #   top_radius = radius - draft * (z - backing)
        # This clips the original planes rather than distorting their angles.
        top_radius = (radius - draft * half_amplitude) / (1 + draft * radial_slope)
        height = half_amplitude + radial_slope * top_radius
        # Merge the outer trough with the ledge: its height above the base
        # is exactly zero. This avoids coincident vertices and zero-area walls.
        if sector % 2 == 1 and step == 0:
            top_radius, height = radius, 0.0
        z = backing + height
        x, y = top_radius * math.cos(theta), top_radius * math.sin(theta)
        top_rim.append(add_vertex((x, y, z)))
        lx, ly = radius * math.cos(theta), radius * math.sin(theta)
        ledge_inner.append(top_rim[-1] if z == backing else add_vertex((lx, ly, backing)))
        bx, by = base_radius * math.cos(theta), base_radius * math.sin(theta)
        ledge_outer.append(add_vertex((bx, by, backing)))
        bottom_outer.append(add_vertex((bx, by, 0.0)))
    # Keep the top fan first so validation can identify every fold directly.
    faces = [(0, top_rim[i], top_rim[(i + 1) % count]) for i in range(count)]
    def add_face(a, b, c):
        if len({a, b, c}) == 3:
            faces.append((a, b, c))
    for i in range(count):
        j = (i + 1) % count
        t0, t1 = top_rim[i], top_rim[j]
        l0, l1 = ledge_inner[i], ledge_inner[j]
        o0, o1 = ledge_outer[i], ledge_outer[j]
        b0, b1 = bottom_outer[i], bottom_outer[j]
        add_face(t0, l0, l1)
        add_face(t0, l1, t1)
        add_face(l0, o0, o1)
        add_face(l0, o1, l1)
        add_face(o0, b0, b1)
        add_face(o0, b1, o1)
        add_face(1, b1, b0)
    rings = {"top_rim": top_rim, "ledge_inner": ledge_inner,
             "ledge_outer": ledge_outer, "bottom_outer": bottom_outer}
    return vertices, faces, {
        "units": "mm",
        "base_diameter_mm": base_diameter,
        "starburst_diameter_mm": diameter,
        "base_thickness_mm": backing,
        "radial_border_width_mm": (base_diameter - diameter) / 2,
        "pleats": pleats,
        "peaks": pleats,
        "troughs": pleats,
        "alternating_frame_alignment_positions": 2 * pleats,
        "angle_between_peak_and_next_trough_degrees": 180 / pleats,
        "angle_between_peaks_degrees": 360 / pleats,
        "first_peak_angle_degrees": rotation_degrees,
        "fold_angle_degrees": fold_angle,
        "fold_angle_definition": "Included acute opening at a tooth; not the face pitch from horizontal",
        "edge_inward_draft_degrees_from_vertical": edge_draft,
        "maximum_edge_inset_mm": draft * peak_height,
        "crest_envelope_diameter_mm": 2 * (radius - draft * peak_height),
        "minimum_backing_mm": backing,
        "outer_peak_to_valley_mm": peak_height,
        "maximum_height_above_base_mm": peak_height,
        "total_height_mm": backing + peak_height,
        "centre_top_z_mm": centre_z,
        "half_amplitude_per_radius": k,
        "perimeter_segments": count,
        "maximum_circle_chord_error_mm": base_radius * (1 - math.cos(math.pi / count)),
        "construction": "Inset planar radial pleats with inward tapered skirt on circular base with flat ledge and back at Z0",
        "height_rule": "Peak-to-valley amplitude = 2 * radius * half_amplitude_per_radius within the pleated surface; the outer edge is clipped by its draft",
        "frame_alignment": f"{pleats} peaks alternate with {pleats} troughs, for {2 * pleats} equally spaced rays; rotate to suit frame",
    }, rings


def write_stl(path, vertices, faces):
    header = b"Inset pleated starburst | mm | planar folds | inward edge draft | circular base"
    with path.open("wb") as stream:
        stream.write(header.ljust(80, b"\0"))
        stream.write(struct.pack("<I", len(faces)))
        for indices in faces:
            a, b, c = (vertices[i] for i in indices)
            stream.write(struct.pack("<12fH", *normal(a, b, c), *a, *b, *c, 0))


def validate_stl(path, pleats, samples, metadata):
    """Reload the actual binary STL; check topology, winding and fold angles."""
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    if len(data) != 84 + 50 * count:
        raise ValueError("Invalid STL length")
    vertices, vertex_ids, faces, normals, top_rim = [], {}, [], [], []
    edges, directed = Counter(), Counter()
    volume = 0.0
    min_area = float("inf")
    for i in range(count):
        record = struct.unpack_from("<12fH", data, 84 + i * 50)
        triangle = [tuple(record[j:j + 3]) for j in (3, 6, 9)]
        if i < 2 * pleats * samples:
            top_rim.append(triangle[1])
        face = []
        for vertex in triangle:
            if not all(math.isfinite(v) for v in vertex):
                raise ValueError("Non-finite coordinate")
            if vertex not in vertex_ids:
                vertex_ids[vertex] = len(vertices)
                vertices.append(vertex)
            face.append(vertex_ids[vertex])
        a, b, c = triangle
        computed_normal = normal(a, b, c)
        normals.append(computed_normal)
        if dot(computed_normal, record[:3]) < 0.99999:
            raise ValueError("STL normal and winding disagree")
        area_vector = cross(subtract(b, a), subtract(c, a))
        min_area = min(min_area, math.sqrt(dot(area_vector, area_vector)) / 2)
        volume += dot(a, cross(b, c)) / 6
        faces.append(tuple(face))
        for u, v in zip(face, face[1:] + face[:1]):
            edges[tuple(sorted((u, v)))] += 1
            directed[(u, v)] += 1
    if not all(value == 2 for value in edges.values()):
        raise ValueError("The mesh is not watertight")
    if not all(directed[(a, b)] == directed[(b, a)] == 1 for a, b in edges):
        raise ValueError("Inconsistent winding or duplicate faces")
    adjacency = defaultdict(set)
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    visited, queue = {0}, deque([0])
    while queue:
        for other in adjacency[queue.popleft()]:
            if other not in visited:
                visited.add(other)
                queue.append(other)
    if len(visited) != len(vertices) or volume <= 0:
        raise ValueError("Expected one connected solid with positive volume")
    euler = len(vertices) - len(edges) + len(faces)
    if euler != 2:
        raise ValueError("Expected a closed genus-zero solid")
    perimeter_count = 2 * pleats * samples
    fold_angles = []
    for i in range(0, perimeter_count, samples):
        # The first perimeter_count triangles are the top fan. Adjacent
        # normals across each crease test both peaks and valleys in the STL.
        n1 = normals[(i - 1) % perimeter_count]
        n2 = normals[i]
        angle = 180 - math.degrees(math.acos(max(-1.0, min(1.0, dot(n1, n2)))))
        fold_angles.append(angle)
    if max(abs(angle - metadata["fold_angle_degrees"]) for angle in fold_angles) > 0.01:
        raise ValueError("Fold angle exceeds the requested angle tolerance")
    draft = math.tan(math.radians(metadata["edge_inward_draft_degrees_from_vertical"]))
    radius = metadata["starburst_diameter_mm"] / 2
    backing = metadata["base_thickness_mm"]
    taper_error = max(abs(radius - math.hypot(x, y) - draft * (z - backing))
                      for x, y, z in top_rim)
    if taper_error > 0.0001:
        raise ValueError("The exported edge does not match the requested inward draft")
    bounds = [[min(v[j] for v in vertices) for j in range(3)],
              [max(v[j] for v in vertices) for j in range(3)]]
    return {
        "watertight": True,
        "consistent_outward_winding": True,
        "connected_components": 1,
        "vertices": len(vertices),
        "triangles": len(faces),
        "euler_characteristic": euler,
        "volume_mm3": volume,
        "bounds_mm": bounds,
        "smallest_triangle_area_mm2": min_area,
        "measured_fold_angle_min_degrees": min(fold_angles),
        "measured_fold_angle_max_degrees": max(fold_angles),
        "maximum_edge_draft_radius_error_mm": taper_error,
        "stl_size_bytes": len(data),
    }


def render_preview(path, vertices, metadata, pleats, samples, rings):
    """Orthographic shaded render of the model, with no external renderer."""
    from PIL import Image, ImageDraw, ImageFont

    supersample = 3
    width, height = 1500, 1160
    background = (244, 243, 239)
    image = Image.new("RGB", (width * supersample, height * supersample), background)
    draw = ImageDraw.Draw(image)
    slope = metadata["half_amplitude_per_radius"] / math.sin(math.pi / (2 * pleats))
    azimuth = math.radians(-68)
    elevation = math.radians(max(58, math.degrees(math.atan(slope)) + 5))
    right = (-math.sin(azimuth), math.cos(azimuth), 0)
    up = (-math.cos(azimuth) * math.sin(elevation),
          -math.sin(azimuth) * math.sin(elevation), math.cos(elevation))
    scale = 5.0 * 200 / metadata["base_diameter_mm"]
    def project(v):
        return ((width / 2 + scale * dot(v, right)) * supersample,
                (600 - scale * dot(v, up)) * supersample)
    def font(size):
        for candidate in ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"):
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size * supersample)
        return ImageFont.load_default()
    def text_at(x, y, value, size, fill=(45, 49, 51)):
        draw.text((x * supersample, y * supersample), value, font=font(size), fill=fill)

    count = 2 * pleats * samples
    top = [vertices[i] for i in rings["top_rim"]]
    inner = [vertices[i] for i in rings["ledge_inner"]]
    outer = [vertices[i] for i in rings["ledge_outer"]]
    bottom = [vertices[i] for i in rings["bottom_outer"]]
    # The top is a heightfield and this elevation is above its maximum slope,
    # so its projection does not self-occlude. Render the perimeter first.
    draw.polygon([project(v) for v in bottom], fill=(163, 154, 137))
    side_quads = []
    view_xy = (math.cos(azimuth), math.sin(azimuth), 0)
    for i in range(count):
        j = (i + 1) % count
        midpoint = tuple((outer[i][axis] + outer[j][axis]) / 2 for axis in range(3))
        side_quads.append((dot(midpoint, view_xy), i, j))
    for _, i, j in sorted(side_quads):
        n = normal(outer[i], bottom[i], bottom[j])
        brightness = 0.64 + 0.18 * dot(n, (-0.6, -0.4, 0.69282))
        colour = tuple(round(component * brightness) for component in (225, 216, 196))
        draw.polygon([project(v) for v in (outer[i], bottom[i], bottom[j], outer[j])], fill=colour)
    # Flat 7.5 mm ledge between the base perimeter and the raised starburst.
    for i in range(count):
        j = (i + 1) % count
        draw.polygon([project(v) for v in (inner[i], outer[i], outer[j], inner[j])],
                     fill=(229, 222, 207))
    # The skirt of the raised pleats meets the base at each outer trough.
    for _, i, j in sorted(side_quads):
        midpoint = tuple((inner[i][axis] + inner[j][axis]) / 2 for axis in range(3))
        length = math.hypot(midpoint[0], midpoint[1])
        draft_angle = math.radians(metadata["edge_inward_draft_degrees_from_vertical"])
        n = (midpoint[0] / length * math.cos(draft_angle),
             midpoint[1] / length * math.cos(draft_angle), math.sin(draft_angle))
        brightness = 0.64 + 0.18 * dot(n, (-0.6, -0.4, 0.69282))
        colour = tuple(round(component * brightness) for component in (225, 216, 196))
        draw.polygon([project(v) for v in (top[i], inner[i], inner[j], top[j])], fill=colour)
    light = (-0.4, -0.5, 0.768114575)
    for sector in range(2 * pleats):
        start = sector * samples
        indices = [(start + j) % count for j in range(samples + 1)]
        n = normal(vertices[0], top[indices[0]], top[indices[1]])
        brightness = 0.59 + 0.40 * max(0, dot(n, light))
        colour = tuple(round(component * brightness) for component in (242, 234, 217))
        draw.polygon([project(vertices[0])] + [project(top[i]) for i in indices], fill=colour)
    text_at(66, 36, "PLEATED STARBURST", 37)
    text_at(68, 95, f'{metadata["base_diameter_mm"]:g} mm base   /   '
            f'{metadata["starburst_diameter_mm"]:g} mm starburst   /   '
            f'{pleats} peaks + {pleats} troughs', 24, (99, 104, 106))
    text_at(68, 1040, f'{metadata["fold_angle_degrees"]:g}\N{DEGREE SIGN} folds   /   '
            f'{metadata["edge_inward_draft_degrees_from_vertical"]:g}\N{DEGREE SIGN} inward edge lean   /   '
            f'{2 * pleats} frame positions', 27)
    text_at(68, 1090, f'{metadata["base_thickness_mm"]:g} mm base thickness   /   '
            f'{metadata["maximum_height_above_base_mm"]:.2f} mm above base   /   '
            f'{metadata["radial_border_width_mm"]:g} mm border', 22, (99, 104, 106))
    image.resize((width, height), Image.Resampling.LANCZOS).save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-diameter", type=float, default=225)
    parser.add_argument("--diameter", type=float, default=210,
                        help="Starburst diameter, inset from the base perimeter")
    parser.add_argument("--pleats", type=int, default=29)
    parser.add_argument("--backing", type=float, default=9,
                        help="Base thickness, in mm")
    parser.add_argument("--rotation", type=float, default=0,
                        help="Angle of the first peak from +X, in degrees")
    parser.add_argument("--fold-angle", type=float, default=70,
                        help="Included angle at a tooth, in degrees")
    parser.add_argument("--edge-draft", type=float, default=35,
                        help="Inward starburst edge lean from vertical, in degrees")
    parser.add_argument("--samples", type=int, default=16,
                        help="Circular perimeter samples per half-pleat")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (args.base_diameter, args.diameter, args.backing)):
        parser.error("Diameters and backing must be positive finite numbers")
    if args.base_diameter <= args.diameter or not math.isfinite(args.rotation):
        parser.error("The base must be larger than the starburst; rotation must be finite")
    if args.pleats < 3 or args.samples < 2:
        parser.error("Use at least 3 pleats and at least 2 samples")
    if not math.isfinite(args.fold_angle) or not 180 / args.pleats < args.fold_angle < 180:
        parser.error("Fold angle must be greater than 180 / pleats and less than 180 degrees")
    if not math.isfinite(args.edge_draft) or not 0 <= args.edge_draft < 45:
        parser.error("Edge draft must be between 0 and 45 degrees")
    k = 1 / math.sqrt((1 / math.tan(math.pi / (2 * args.pleats))) ** 2
                      * math.tan(math.radians(args.fold_angle) / 2) ** 2 - 1)
    if math.tan(math.radians(args.edge_draft)) * k >= 1:
        parser.error("Edge draft is too large for this fold angle and pleat count")
    args.output.mkdir(parents=True, exist_ok=True)
    vertices, faces, metadata, rings = build_model(args.base_diameter, args.diameter,
                                                  args.pleats, args.backing, args.samples,
                                                  args.rotation, args.fold_angle, args.edge_draft)
    stl_path = args.output / "starburst.stl"
    write_stl(stl_path, vertices, faces)
    metadata["validation"] = validate_stl(stl_path, args.pleats, args.samples, metadata)
    (args.output / "starburst_dimensions.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    if args.preview:
        render_preview(args.output / "starburst_preview.png", vertices, metadata,
                       args.pleats, args.samples, rings)
    print(json.dumps({"stl": str(stl_path), **metadata}, indent=2))


if __name__ == "__main__":
    main()
