"""REQUIRES VERIFIED 216 MM Y TRAVEL. Use make_y200_facing.py for 200 mm Y.

One constant-depth, edge-entry facing pass for the starburst timber.

The spiral uses tangent semicircles, reducing radius 3 mm each half turn.
There is no plunge into timber and no automatic second depth/pass.
Run with the bundled Python runtime; NumPy and Pillow validate and preview.
The saved G-code is parsed again before the coverage report is written.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont


OUT = Path(__file__).resolve().parent
STEM = "face_230mm_spiral_16mm_one_pass"
STOCK_DIAMETER = 230.0
TOOL_DIAMETER = 16.0
DEPTH = 0.5
FEED = 600.0
ENTRY_FEED = 120.0
SAFE_Z = 5.0
PITCH = 6.0
OUTER_RADIUS = 108.0
ENTRY_X = 140.0
FINAL_RADIUS = 6.0


def make_gcode() -> str:
    lines = [
        "(STAR BURST TIMBER - ONE SPIRAL FACING PASS)",
        "(REQUIRES 216 MM Y TRAVEL - DOES NOT FIT 200 MM Y RANGE)",
        "(Units mm - stock 220 to 230 diameter - cutter 16 diameter)",
        "(G54 X0 Y0 at timber centre - Z0 at highest top surface)",
        "(One cutting plane Z-0.500 - XY feed 600 mm/min)",
        "(6 mm pitch - tangent semicircles - clockwise inward sweep)",
        "(Start above centre - check Z5 clears timber and fixtures)",
        "(Check travel X-108 to X140 and Y-108 to Y108)",
        "(Cutter envelope X-116 to X148 and Y-116 to Y116)",
        "(Entry from positive X - cutter starts fully outside timber)",
        "(MANUALLY START SPINDLE BEFORE RUNNING)",
        "(No automatic spindle or speed control)",
        "(For another cut reset Z0 to newly faced top before rerunning)",
        "(After flipping re-clamp and reset centre XY and top Z)",
        "G21 G17 G90 G91.1 G94",
        "G54 G40 G49",
        f"G0 Z{SAFE_Z:.3f}",
        f"G0 X{ENTRY_X:.3f} Y0.000",
        f"G1 Z{-DEPTH:.3f} F{ENTRY_FEED:.0f}",
        "(Tangent entry semicircle - no plunge into wood)",
        f"G3 X{OUTER_RADIUS:.3f} Y0.000 I{(OUTER_RADIUS-ENTRY_X)/2:.3f} J0.000 F{FEED:.0f}",
        "(Complete outer cleanup circle)",
        f"G2 X{-OUTER_RADIUS:.3f} Y0.000 I{-OUTER_RADIUS:.3f} J0.000",
        f"G2 X{OUTER_RADIUS:.3f} Y0.000 I{OUTER_RADIUS:.3f} J0.000",
        "(Continuous inward spiral - one fixed depth)",
    ]
    x = OUTER_RADIUS
    half_step = PITCH / 2
    while abs(x) > FINAL_RADIUS + 1e-9:
        next_x = -math.copysign(abs(x) - half_step, x)
        lines.append(f"G2 X{next_x:.3f} Y0.000 I{(next_x-x)/2:.3f} J0.000")
        x = next_x
    assert abs(abs(x) - FINAL_RADIUS) < 1e-9
    lines += [
        "(16 mm cutter covers the centre from this final 6 mm radius)",
        f"G0 Z{SAFE_Z:.3f}",
        "G0 X0.000 Y0.000",
        "(MANUALLY STOP SPINDLE WHEN COMPLETE)",
        "M2",
    ]
    return "\n".join(lines) + "\n"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def angle_on_arc(angle: float, arc: dict) -> bool:
    direction = 1 if arc["g"] == 3 else -1
    travelled = (direction * (angle - arc["a0"])) % (2 * math.pi)
    return travelled <= abs(arc["sweep"]) + 1e-9


def arc_point(arc: dict, fraction: float) -> tuple[float, float]:
    a = arc["a0"] + fraction * arc["sweep"]
    cx, cy = arc["centre"]
    return cx + arc["radius"] * math.cos(a), cy + arc["radius"] * math.sin(a)


def tangent(arc: dict, at_end: bool) -> tuple[float, float]:
    a = arc["a0"] + (arc["sweep"] if at_end else 0)
    direction = 1 if arc["g"] == 3 else -1
    return -direction * math.sin(a), direction * math.cos(a)


def validate_saved_gcode(path: Path) -> tuple[dict, list[dict]]:
    """Decode saved G-code independently of the path construction."""
    # Setup specifies starting above the stock centre, already at clearance Z.
    xyz = [0.0, 0.0, SAFE_Z]
    feed = None
    arcs = []
    min_z = SAFE_Z
    descents = 0
    code_modes = set()
    allowed_g = {0, 1, 2, 3, 17, 21, 40, 49, 54, 90, 91.1, 94}
    seen_end = False
    for number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        code = re.sub(r"\([^)]*\)", "", line).strip()
        if not code:
            continue
        require(not seen_end, f"Motion/command after program end at line {number}")
        words = re.findall(r"([A-Z])([-+]?\d+(?:\.\d+)?)", code)
        require(re.sub(r"([A-Z])([-+]?\d+(?:\.\d+)?)", "", code).strip() == "", "Unparsed G-code")
        require(all(letter in "GMXYZIJF" for letter, _ in words), "Unexpected address")
        g_codes = [float(value) for letter, value in words if letter == "G"]
        require(all(g in allowed_g for g in g_codes), "Unsupported or unsafe G-code")
        code_modes.update(g_codes)
        values = {letter: float(value) for letter, value in words if letter != "G"}
        if "M" in values:
            require(values["M"] == 2 and len(words) == 1, "Unexpected M-code")
            seen_end = True
            continue
        if "F" in values:
            feed = values["F"]
        motion = [int(g) for g in g_codes if g in {0, 1, 2, 3}]
        if not motion:
            require(not any(axis in values for axis in "XYZIJ"), "Coordinates without motion")
            continue
        require(len(motion) == 1, "Ambiguous motion mode")
        g = motion[0]
        start = tuple(xyz)
        end = tuple(values.get(axis, xyz[i]) for i, axis in enumerate("XYZ"))
        min_z = min(min_z, end[2])
        if g == 0 and start[:2] != end[:2]:
            require(min(start[2], end[2]) >= SAFE_Z, "Rapid XY below clearance")
        if end[2] < start[2] - 1e-9:
            descents += 1
            require(g == 1 and start[:2] == end[:2], "Unexpected descent")
            require(math.hypot(*end[:2]) > STOCK_DIAMETER/2 + TOOL_DIAMETER/2, "Descent intersects timber")
            require(abs(end[2] + DEPTH) < 1e-9 and feed == ENTRY_FEED, "Wrong descent depth/feed")
        if g == 1:
            require(start[:2] == end[:2], "Unexpected linear cutting move")
        if g in {2, 3}:
            require({17, 21, 90, 91.1, 94, 54, 40, 49}.issubset(code_modes), "Incomplete modal setup")
            require(start[2] == end[2] == -DEPTH and feed == FEED, "Wrong cutting depth/feed")
            require("I" in values and "J" in values, "Missing incremental arc centre")
            centre = (start[0] + values["I"], start[1] + values["J"])
            radius = math.dist(start[:2], centre)
            require(abs(radius - math.dist(end[:2], centre)) < 0.001, "Arc radius mismatch")
            a0 = math.atan2(start[1]-centre[1], start[0]-centre[0])
            a1 = math.atan2(end[1]-centre[1], end[0]-centre[0])
            sweep = (a1-a0) % (2*math.pi) if g == 3 else -((a0-a1) % (2*math.pi))
            require(abs(abs(sweep)-math.pi) < 1e-9, "Expected semicircular arc")
            arcs.append(dict(line=number, g=g, start=start, end=end, centre=centre,
                             radius=radius, a0=a0, sweep=sweep))
        xyz = list(end)
    require(seen_end and descents == 1, "Expected exactly one depth/pass and a program end")
    require(min_z == -DEPTH and xyz == [0.0, 0.0, SAFE_Z], "Wrong final state/minimum Z")
    require(len(arcs) == 37, "Wrong number of entry, outer and spiral arcs")
    require(arcs[0]["g"] == 3 and all(a["g"] == 2 for a in arcs[1:]), "Wrong cutting direction")
    require(arcs[1]["centre"] == arcs[2]["centre"] == (0, 0), "Outer loop missing")
    require(arcs[1]["radius"] == arcs[2]["radius"] == OUTER_RADIUS, "Wrong outer radius")
    for previous, current in zip(arcs, arcs[1:]):
        require(previous["end"] == current["start"], "Disconnected toolpath")
        dot = sum(a*b for a, b in zip(tangent(previous, True), tangent(current, False)))
        require(dot > 1-1e-9, "Non-tangent join")

    # Exact distances to the decoded semicircles, including their endpoints.
    # Sample the full 230 mm disc on a 0.5 mm grid and the rim at 4096 angles.
    coordinates = np.arange(-STOCK_DIAMETER/2, STOCK_DIAMETER/2+0.25, 0.5)
    xx, yy = np.meshgrid(coordinates, coordinates)
    inside = xx*xx + yy*yy <= (STOCK_DIAMETER/2)**2
    rim = np.linspace(0, 2*math.pi, 4096, endpoint=False)
    x = np.concatenate((xx[inside], STOCK_DIAMETER/2*np.cos(rim), [0.0]))
    y = np.concatenate((yy[inside], STOCK_DIAMETER/2*np.sin(rim), [0.0]))
    distances = np.full(x.shape, np.inf)
    extrema = []
    for arc in arcs:
        cx, cy = arc["centre"]
        theta = np.arctan2(y-cy, x-cx)
        direction = 1 if arc["g"] == 3 else -1
        on_arc = (direction*(theta-arc["a0"])) % (2*math.pi) <= abs(arc["sweep"])+1e-9
        to_curve = np.abs(np.hypot(x-cx, y-cy)-arc["radius"])
        sx, sy = arc["start"][:2]
        ex, ey = arc["end"][:2]
        to_end = np.minimum(np.hypot(x-sx, y-sy), np.hypot(x-ex, y-ey))
        distances = np.minimum(distances, np.where(on_arc, to_curve, to_end))
        extrema += [arc["start"][:2], arc["end"][:2]]
        for angle in (0, math.pi/2, math.pi, 3*math.pi/2):
            if angle_on_arc(angle, arc):
                extrema.append((cx+arc["radius"]*math.cos(angle), cy+arc["radius"]*math.sin(angle)))
    worst = int(np.argmax(distances))
    require(float(distances[worst]) <= TOOL_DIAMETER/2 - 0.001, "Facing leaves uncovered stock")
    xmin, xmax = min(p[0] for p in extrema), max(p[0] for p in extrema)
    ymin, ymax = min(p[1] for p in extrema), max(p[1] for p in extrema)
    length = sum(abs(a["sweep"])*a["radius"] for a in arcs)
    minimum_radius = min(a["radius"] for a in arcs)
    return dict(
        status="PASS - geometry and saved G-code validated; machine execution not performed",
        gcode_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        stock_max_diameter_mm=STOCK_DIAMETER, cutter_diameter_mm=TOOL_DIAMETER,
        pass_count=1, depth_mm=DEPTH, cutting_z_mm=-DEPTH, clearance_z_mm=SAFE_Z,
        xy_feed_mm_per_min=FEED, air_descent_feed_mm_per_min=ENTRY_FEED,
        radial_pitch_per_revolution_mm=PITCH, spiral_half_turns=len(arcs)-3,
        arc_count=len(arcs), minimum_arc_radius_mm=minimum_radius,
        maximum_curve_acceleration_mm_per_s2=(FEED/60)**2/minimum_radius,
        tool_centre_bounds_mm=dict(x=[xmin, xmax], y=[ymin, ymax]),
        required_xy_travel_mm=[xmax-xmin, ymax-ymin],
        cutter_swept_envelope_mm=dict(x=[xmin-TOOL_DIAMETER/2, xmax+TOOL_DIAMETER/2],
                                     y=[ymin-TOOL_DIAMETER/2, ymax+TOOL_DIAMETER/2]),
        cutting_path_length_mm=length, ideal_cutting_time_minutes=length/FEED,
        coverage_sample_count=int(len(x)), coverage_grid_spacing_mm=0.5,
        coverage_rim_samples=len(rim), maximum_stock_distance_to_tool_centre_path_mm=float(distances[worst]),
        maximum_distance_sample_xy_mm=[float(x[worst]), float(y[worst])],
        centre_distance_to_path_mm=float(distances[-1]),
        all_cutting_arcs_tangent=True, all_cutting_arcs_same_z=True,
        vertical_descent_outside_stock=True, rapid_xy_at_clearance=True,
        spindle_control="manual - no spindle commands",
        repeat="Reset Z0 to newly faced top for another 0.5 mm cut before rerunning",
        machine_fit="Factory Y setting 200 mm; later profile 220 mm. Requires VERIFIED 216 mm usable Y. Use Y200 version otherwise.",
    ), arcs


def preview(arcs: list[dict], report: dict, path: Path) -> None:
    image = Image.new("RGB", (1400, 1160), "#f8fafc")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/segoeui.ttf")
    def font(size):
        return ImageFont.truetype(str(font_path), size) if font_path.exists() else ImageFont.load_default()
    title, body, small = font(34), font(23), font(20)
    draw.text((45, 25), "230 mm timber | one spiral facing pass", fill="#0f172a", font=title)
    draw.text((45, 78), "16 mm cutter   /   Z -0.5 mm   /   600 mm/min   /   6 mm pitch", fill="#475569", font=body)
    scale, origin = 3.5, (640, 570)
    def xy(point):
        return origin[0]+point[0]*scale, origin[1]-point[1]*scale
    def circle(centre, radius, **kwargs):
        x, y = xy(centre)
        draw.ellipse((x-radius*scale, y-radius*scale, x+radius*scale, y+radius*scale), **kwargs)
    circle((0, 0), STOCK_DIAMETER/2, fill="#e2e8f0", outline="#64748b", width=3)
    draw.line([xy((-120, 0)), xy((153, 0))], fill="#cbd5e1", width=1)
    draw.line([xy((0, -120)), xy((0, 120))], fill="#cbd5e1", width=1)
    for i, arc in enumerate(arcs):
        points = [xy(arc_point(arc, fraction)) for fraction in np.linspace(0, 1, 180)]
        colour = "#0284c7" if i == 0 else "#b45309" if i < 3 else "#1e40af"
        draw.line(points, fill=colour, width=3)
        if i in (0, 1, 5, 13, 23, 33):
            a, b = xy(arc_point(arc, 0.45)), xy(arc_point(arc, 0.48))
            dx, dy = b[0]-a[0], b[1]-a[1]
            norm = math.hypot(dx, dy)
            ux, uy = dx/norm, dy/norm
            draw.polygon([b, (b[0]-12*ux+5*uy, b[1]-12*uy-5*ux),
                          (b[0]-12*ux-5*uy, b[1]-12*uy+5*ux)], fill=colour)
    circle((ENTRY_X, 0), TOOL_DIAMETER/2, outline="#0284c7", width=2)
    circle(arcs[-1]["end"][:2], TOOL_DIAMETER/2, outline="#16a34a", width=2)
    sx, sy = xy((ENTRY_X, 0))
    draw.ellipse((sx-5, sy-5, sx+5, sy+5), fill="#0284c7")
    draw.text((sx-45, sy+45), "START", fill="#0284c7", font=body)
    draw.text((sx-67, sy+74), "X140 Y0", fill="#475569", font=small)
    draw.text((60, 154), "TOP VIEW", fill="#475569", font=small)
    draw.text((origin[0]-88, 995), "230 mm stock", fill="#475569", font=body)
    draw.text((45, 1043), "Tool centre: X -108 to +140, Y -108 to +108 (248 x 216 mm travel)", fill="#0f172a", font=body)
    draw.text((45, 1080), "Blue: inward spiral   |   Amber: outer sweep   |   Green: final cutter footprint", fill="#475569", font=small)
    draw.text((45, 1115), "Reset Z0 to the newly faced surface before another cutting run.", fill="#0f172a", font=small)
    image.save(path)


SETUP = """STAR BURST TIMBER: ONE SPIRAL FACING PASS

REQUIRES VERIFIED 216 MM USABLE Y TRAVEL. DOES NOT FIT A 200 MM Y RANGE.
Use facing_Y200_setup.txt and the Y200 stretched-spiral program instead.

File: face_230mm_spiral_16mm_one_pass.gcode
Stock: centred circular timber, 220 to 230 mm diameter maximum.
Tool: 16 mm facing cutter; its non-cutting centre is not plunged into wood.
One cutting plane: 0.5 mm below the initial Z0. No additional depths/loops.
Feed: 600 mm/min XY; 120 mm/min vertical descent while outside the stock.
Spindle: use the cutter's suitable speed on the manual speed controller.
This file does not switch the spindle or set its speed.

SETUP
1. Secure the timber and clear clamps from the cutter's swept envelope.
   Tool centres: X -108 to +140, Y -108 to +108 relative to stock centre.
   Cutter envelope: X -116 to +148, Y -116 to +116 (includes edge entry).
   Required axis travel: 248 mm X and 216 mm Y. The advertised Y travel is
   only 220 mm; confirm actual travel and stock positioning before cutting.
2. Set G54 X0 Y0 at the timber centre; set Z0 to its HIGHEST top surface.
   Use normal work zero/probing; the program does not set or change offsets.
   Start above the centre at Z5 or higher. Z5 must clear stock and fixtures.
3. Preview in the sender and perform an air run clear of timber first.
   For an air run, temporarily raise work Z0 enough that the Z-0.5 path
   clears the highest stock and fixtures. Restore actual surface Z0 after.
4. Start the spindle manually before running the cutting program.
5. The tool moves out to X140 at clearance, lowers entirely in air,
   curves into the edge, completes a full outer circle, then spirals in.
   All 37 arcs join tangentially. Radius shrinks 3 mm each spiral half turn.
   The 16 mm cutter covers the centre at the final 6 mm centre radius.
   It retracts to Z5 and returns to the timber centre. Stop spindle manually.

REUSING THE SAME FILE
To take another 0.5 mm off, reset Z0 to the newly faced top before rerunning.
Running again with the same Z0 returns to the same cutting plane; it does
not automatically lower the next pass. Reprobe/re-zero after flipping,
and reset XY centre if the timber shifts. Use the same file for either side.
Uneven areas more than 0.5 mm below the initial highest point can remain
untouched after the first run. A single constant-depth pass is intentional.

ESTIMATE AND VALIDATION
About 11.4 minutes of arc motion at the programmed feed; allow extra time
for approach/retract and acceleration. Feed/depth are initial choices from
the project's existing feed, not a measured setup for this specific cutter.
The adjacent JSON records validation of the saved G-code: arc radii and
tangency, one constant depth, no rapid XY below clearance, descent fully
outside timber, and sampled coverage of the full 230 mm disc and its rim.
It is a geometric/software check, not a machine or fixture clearance test.

REGENERATING
Edit the constants in make_spiral_facing.py and run with Python containing
NumPy and Pillow. The supplied validation is intentionally specific to the
230 mm / 16 mm geometry; recheck machine travel for any geometry changes.
"""


def main() -> None:
    path = OUT / f"{STEM}.gcode"
    path.write_text(make_gcode(), encoding="ascii")
    report, arcs = validate_saved_gcode(path)
    (OUT / f"{STEM}_validation.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    preview(arcs, report, OUT / f"{STEM}_preview.png")
    (OUT / "facing_setup.txt").write_text(SETUP, encoding="ascii")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
