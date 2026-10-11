"""Stretched spiral facing for 200 mm Y travel; rotate stock 90 degrees.

Each run contains one constant-depth pass. Two stock orientations at the SAME
work Z produce a complete 230 mm face. Arcs and straights join tangentially.
"""
from pathlib import Path
import hashlib
import json
import math
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from make_spiral_facing import require, arc_point, angle_on_arc, tangent

OUT = Path(__file__).resolve().parent
STEM = "face_230mm_Y200_stretched_spiral_one_pass"
RADIUS = 98.0
HALF_STRAIGHT = 16.0
DEPTH = 0.5
FEED = 600.0
SAFE_Z = 5.0


def generate():
    lines = [
        "(WM3020 - 200 MM Y RANGE - STRETCHED SPIRAL FACING)",
        "(Stock up to 230 mm diameter - 16 mm cutter - ONE depth per run)",
        "(Requires 254 mm X travel and 196 mm Y travel)",
        "(G54 XY0 stock centre - Z0 HIGHEST initial surface)",
        "(Set centre so X-114 to X140 and Y-98 to Y98 are reachable)",
        "(Cutter swept envelope X-122 to X148 and Y-106 to Y106)",
        "(Start above centre - ensure Z5 clears stock and fixtures)",
        "(START SPINDLE MANUALLY BEFORE RUNNING - speed set manually)",
        "(One Z-0.500 pass at F600 - no plunge into timber)",
        "(First orientation leaves small top and bottom edge areas)",
        "(To complete face ROTATE TIMBER 90 DEG - re-centre XY)",
        "(KEEP THE SAME Z HEIGHT for the second orientation)",
        "(Do not zero Z on partly faced timber before this second run)",
        "(Only reset Z0 for a NEW deeper layer or after flipping)",
        "G21 G17 G90 G91.1 G94",
        "G54 G40 G49",
        "G0 Z5.000",
        "G0 X140.000 Y66.000",
        "G1 Z-0.500 F120",
        "(Curved air entry then tangent straight through stock edge)",
        "G3 X108.000 Y98.000 I-32.000 J0.000 F600",
        "G1 X-16.000 Y98.000 F600",
        "(Outer stadium sweep - all joins tangent)",
        "G3 X-16.000 Y-98.000 I0.000 J-98.000",
        "G1 X16.000 Y-98.000 F600",
        "G3 X16.000 Y98.000 I0.000 J98.000",
        "G1 X-16.000 Y98.000 F600",
        "(Inward spiral - radius reduces 3 mm each half turn)",
    ]
    side, r, y = -1, RADIUS, RADIUS
    while r > 5:
        next_r = r - 3
        next_y = -math.copysign(next_r, y)
        lines.append(f"G3 X{side*HALF_STRAIGHT:.3f} Y{next_y:.3f} I0.000 J{(next_y-y)/2:.3f}")
        lines.append(f"G1 X{-side*HALF_STRAIGHT:.3f} Y{next_y:.3f} F600")
        side *= -1
        r, y = next_r, next_y
    lines += ["(Centre covered by final traverse)", "G0 Z5.000", "G0 X0.000 Y0.000",
              "(STOP SPINDLE MANUALLY - rotate 90 deg for edge completion)", "M2"]
    return "\n".join(lines) + "\n"


def decode_and_validate(path):
    xyz, feed, descents, finished = (0.0, 0.0, SAFE_Z), 0, 0, False
    modes, segments = set(), []
    for number, raw in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        code = re.sub(r"\([^)]*\)", "", raw).strip()
        if not code:
            continue
        require(not finished, "Command after M2")
        words = re.findall(r"([A-Z])([-+]?\d+(?:\.\d+)?)", code)
        require(not re.sub(r"([A-Z])([-+]?\d+(?:\.\d+)?)", "", code).strip(), "Unparsed command")
        require(all(k in "GMXYZIJF" for k, v in words), "Unsupported address")
        gs = [float(v) for k, v in words if k == "G"]
        require(all(g in {0, 1, 3, 17, 21, 90, 91.1, 94, 54, 40, 49} for g in gs), "Unexpected G-code")
        modes.update(gs)
        values = {k: float(v) for k, v in words if k != "G"}
        if "M" in values:
            require(values["M"] == 2 and len(words) == 1, "Unexpected M-code")
            finished = True
            continue
        feed = values.get("F", feed)
        motions = [int(g) for g in gs if g in {0, 1, 3}]
        if not motions:
            require(not any(k in values for k in "XYZIJ"), "Coordinates without motion")
            continue
        require(len(motions) == 1, "Ambiguous motion")
        g, start = motions[0], xyz
        end = tuple(values.get(k, xyz[i]) for i, k in enumerate("XYZ"))
        require(end[2] >= -DEPTH, "Extra cutting depth")
        if g == 0 and start[:2] != end[:2]:
            require(start[2] == end[2] == SAFE_Z, "Rapid XY below clearance")
        if end[2] < start[2]:
            descents += 1
            require(g == 1 and start[:2] == end[:2] and feed == 120, "Unexpected descent")
            require(math.hypot(*end[:2]) > 115+8 and end[2] == -DEPTH, "Descent in timber")
        if g in {1, 3} and start[:2] != end[:2]:
            require({17, 21, 90, 91.1, 94, 54, 40, 49}.issubset(modes), "Missing setup")
            require(start[2] == end[2] == -DEPTH and feed == FEED, "Incorrect cut plane/feed")
            segment = dict(g=g, line=number, start=start, end=end)
            if g == 3:
                require("I" in values and "J" in values, "Missing arc centre")
                c = (start[0]+values["I"], start[1]+values["J"])
                radius = math.dist(start[:2], c)
                require(abs(radius-math.dist(end[:2], c)) < 0.001, "Arc radius mismatch")
                a0 = math.atan2(start[1]-c[1], start[0]-c[0])
                a1 = math.atan2(end[1]-c[1], end[0]-c[0])
                sweep = (a1-a0) % (2*math.pi)
                require(abs(sweep-math.pi) < 1e-9 or abs(sweep-math.pi/2) < 1e-9, "Unexpected arc sweep")
                segment.update(centre=c, radius=radius, a0=a0, sweep=sweep)
            segments.append(segment)
        xyz = end
    require(finished and descents == 1 and xyz == (0, 0, SAFE_Z), "Wrong pass count or end")

    def vector(segment, end):
        if segment["g"] == 3:
            return tangent(segment, end)
        dx = segment["end"][0]-segment["start"][0]
        dy = segment["end"][1]-segment["start"][1]
        length = math.hypot(dx, dy)
        return dx/length, dy/length
    for a, b in zip(segments, segments[1:]):
        require(a["end"] == b["start"], "Disconnected cut")
        require(sum(i*j for i, j in zip(vector(a, True), vector(b, False))) > 1-1e-9, "Non-tangent join")

    extrema, length = [], 0.0
    for s in segments:
        extrema += [s["start"][:2], s["end"][:2]]
        if s["g"] == 3:
            length += s["radius"]*s["sweep"]
            cx, cy = s["centre"]
            for angle in (0, math.pi/2, math.pi, 3*math.pi/2):
                if angle_on_arc(angle, s):
                    extrema.append((cx+s["radius"]*math.cos(angle), cy+s["radius"]*math.sin(angle)))
        else:
            length += math.dist(s["start"][:2], s["end"][:2])
    bounds = [[min(v[i] for v in extrema), max(v[i] for v in extrema)] for i in (0, 1)]
    require(bounds[0] == [-114, 140] and bounds[1] == [-98, 98], "Unexpected required travel")

    # Analytic distances to decoded arcs and straight segments.
    def distance(x, y):
        best = np.full(np.shape(x), np.inf)
        for s in segments:
            sx, sy = s["start"][:2]
            ex, ey = s["end"][:2]
            if s["g"] == 3:
                cx, cy = s["centre"]
                travelled = (np.arctan2(y-cy, x-cx)-s["a0"]) % (2*math.pi)
                on_arc = travelled <= s["sweep"]+1e-9
                to_curve = np.abs(np.hypot(x-cx, y-cy)-s["radius"])
                to_ends = np.minimum(np.hypot(x-sx, y-sy), np.hypot(x-ex, y-ey))
                d = np.where(on_arc, to_curve, to_ends)
            else:
                dx, dy = ex-sx, ey-sy
                t = np.clip(((x-sx)*dx+(y-sy)*dy)/(dx*dx+dy*dy), 0, 1)
                d = np.hypot(x-(sx+t*dx), y-(sy+t*dy))
            best = np.minimum(best, d)
        return best

    grid = np.arange(-115, 115.25, 0.5)
    xx, yy = np.meshgrid(grid, grid)
    inside = xx*xx+yy*yy <= 115**2
    angles = np.linspace(0, 2*math.pi, 8192, endpoint=False)
    x = np.concatenate((xx[inside], 115*np.cos(angles), [0]))
    y = np.concatenate((yy[inside], 115*np.sin(angles), [0]))
    first, second = distance(x, y), distance(-y, x)
    combined = np.minimum(first, second)
    require(float(np.max(combined)) <= 8-0.001, "Uncut stock after the 90 degree rotation")
    require(float(np.max(first)) > 8, "Expected incomplete one-orientation coverage")
    min_radius = min(s["radius"] for s in segments if s["g"] == 3)
    report = dict(
        status="PASS - saved G-code geometry and two-orientation coverage verified; not machine run",
        gcode_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        stock_max_diameter_mm=230, cutter_diameter_mm=16,
        conservative_machine_travel_mm=[290, 200], required_xy_travel_mm=[254, 196],
        tool_centre_bounds_mm=dict(x=bounds[0], y=bounds[1]),
        cutter_swept_envelope_mm=dict(x=[-122, 148], y=[-106, 106]),
        depth_per_run_mm=DEPTH, pass_count_per_run=1, cutting_z_mm=-DEPTH,
        radial_pitch_per_full_turn_mm=6, xy_feed_mm_per_min=FEED,
        air_descent_feed_mm_per_min=120, clearance_z_mm=SAFE_Z,
        minimum_arc_radius_mm=min_radius,
        maximum_curve_acceleration_mm_per_s2=(FEED/60)**2/min_radius,
        all_xy_cutting_joins_tangent=True, rapid_xy_at_clearance=True,
        descent_entirely_outside_stock=True, spindle_control="manual",
        cutting_path_length_per_run_mm=length, ideal_cutting_time_per_run_minutes=length/FEED,
        orientations_required_for_complete_face=2, second_orientation_rotation_deg=90,
        second_orientation_z="Keep original work Z0 and same absolute cutting plane; re-centre XY only",
        coverage_sample_count=len(x), coverage_grid_mm=0.5, coverage_rim_samples=len(angles),
        first_orientation_max_distance_to_path_mm=float(np.max(first)),
        both_orientations_max_distance_to_path_mm=float(np.max(combined)),
        both_orientations_uncovered_samples=int(np.sum(combined > 8)),
        measured_usable_machine_travel="Not verified; 200 mm Y chosen conservatively",
        factory_settings_reference="hardware/RAMPS/reference/wm3020-default-configuration/WM-3020.nc: Y200",
        last_saved_controller_readback="hardware/RAMPS/uno-cnc/qa/Uno-WM3020-Pi-CNCjs-readback-2026-10-10.json: Y220",
        live_readback="CNCjs API requires login; no machine commands or settings writes sent",
    )
    image_grid = np.arange(-115, 115.5, 1)
    px, py = np.meshgrid(image_grid, image_grid)
    pd1, pd2 = distance(px, py), distance(-py, px)
    return report, segments, (px, py, pd1, np.minimum(pd1, pd2))


def preview(segments, coverage):
    image = Image.new("RGB", (1500, 1030), "#f8fafc")
    d = ImageDraw.Draw(image)
    def font(size):
        return ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", size)
    d.text((40, 22), "Facing a 230 mm disc within 200 mm Y travel", font=font(34), fill="#0f172a")
    d.text((40, 77), "16 mm cutter | one 0.5 mm pass per run | 600 mm/min | 254 x 196 mm travel", font=font(24), fill="#475569")
    scale = 2.45
    origins = [(365, 495), (1130, 495)]
    xx, yy, first, combined = coverage
    for index, origin in enumerate(origins):
        def xy(point):
            return origin[0]+point[0]*scale, origin[1]-point[1]*scale
        d.text((origin[0]-220, 145), "First stock orientation" if index == 0 else "After rotating stock 90 degrees", font=font(27), fill="#0f172a")
        mask = xx*xx+yy*yy <= 115**2
        distances = first if index == 0 else combined
        for x, y, dist in zip(xx[mask], yy[mask], distances[mask]):
            px, py = xy((x, y))
            colour = "#fecaca" if dist > 8 else "#dcfce7"
            d.rectangle((px-1.5, py-1.5, px+1.5, py+1.5), fill=colour)
        radius = 115*scale
        d.ellipse((origin[0]-radius, origin[1]-radius, origin[0]+radius, origin[1]+radius), outline="#64748b", width=2)
        if index == 0:
            for s in segments:
                points = [s["start"][:2], s["end"][:2]] if s["g"] == 1 else [arc_point(s, t) for t in np.linspace(0, 1, 160)]
                d.line([xy(p) for p in points], fill="#1e40af", width=2)
            d.text((origin[0]-230, 810), "Red: edges still needing machining", font=font(23), fill="#991b1b")
        else:
            d.text((origin[0]-247, 810), "Green: complete face after both runs", font=font(23), fill="#166534")
    d.text((40, 884), "Keep the SAME Z height for the second orientation; re-centre XY after rotating.", font=font(27), fill="#0f172a")
    d.text((40, 932), "Only reset Z0 when taking a new deeper layer or facing the opposite side.", font=font(23), fill="#475569")
    d.text((40, 973), "Work centre must allow X -114 to +140 and Y -98 to +98. Check clamps and air-run first.", font=font(22), fill="#475569")
    image.save(OUT / f"{STEM}_preview.png")


SETUP = """WM3020: 200 MM Y TRAVEL - STRETCHED SPIRAL FACING

USE face_230mm_Y200_stretched_spiral_one_pass.gcode.
The older circular-spiral file needs 216 mm Y travel and does not fit 200 mm.
Each run is ONE 0.5 mm pass. A complete face needs TWO stock orientations.
The 16 mm cutter cannot reach a full 220-230 mm disc from 200 mm Y travel.
The stretched spiral reaches sideways and leaves small upper/lower edge
areas. Rotating the timber 90 degrees brings these into the reachable area.

TOUCH OFF AND POSITION
Secure the timber. Use G54 XY0 at its centre and Z0 at the highest initial
top surface. Begin above centre at Z5 or higher; Z5 must clear the stock
and fixtures. Set spindle speed suitably for your cutter and start manually.
The non-cutting tool centre is not plunged into wood: descent is in air.

Required centre coordinates: X -114 to +140, Y -98 to +98.
Required axis travel: 254 mm X, 196 mm Y (4 mm total inside 200 mm Y).
Cutter swept envelope: X -122 to +148, Y -106 to +106, including entry.
Check actual positioning, reach and clamps, and perform an air run first.
The 200 mm Y assumption still requires at least 196 mm actual usable travel.

COMPLETE ONE FACE
1. Run the file once. It lowers to Z-0.5 in air, curves into a tangent edge
   approach, sweeps the outer stadium and spirals inward, then retracts.
2. Stop spindle manually. Rotate stock 90 degrees and re-clamp flat to the
   SAME reference/support plane. Restore XY centre if it shifted.
3. KEEP THE SAME ORIGINAL WORK Z0. Run the same file again at the same
   absolute cutting height to finish the missing edge areas. Do not zero
   Z on the newly cut middle: that would cut another 0.5 mm instead.
4. If re-clamping changes the timber's height, restore the same cutting
   plane using a fixed reference. Do not assume height survived a re-clamp.

ANOTHER LAYER OR OPPOSITE SIDE
For another layer, reset Z0 to the fully faced top, then repeat the two
orientations. After flipping to face the other side, set XY centre and Z0
to that side's highest surface, then perform the same two orientations.
This file has no automated extra depths or automatic spindle commands.
Feed 600 mm/min; 120 mm/min air descent. About 11.5 minutes per run plus
positioning, acceleration and the manual rotation. Low areas can remain
uncut after the first 0.5 mm layer if the original surface is very uneven.

MACHINE LIMIT EVIDENCE
FoxAlien factory WM-3020.nc: X300, Y200, Z60 settings.
Latest saved Uno/CNCjs profile: X290, Y220, Z70 settings (10 Oct 2026).
The advertised working range was recorded as X290, Y220, Z70.
These values differ and none proves physical usable travel. This program
conservatively uses the smaller Y value. Controller limits were not changed.
The live CNCjs API was reachable but required login, so no fresh settings
or machine motions were requested. Physical travel has not been measured.

VALIDATION
The adjacent JSON checks the actual saved G-code: one constant cutting
plane, tangent arc/straight joins, exact axis bounds, air-only descent and
rapid XY at clearance. Analytic distances on a 0.5 mm stock grid and 8192
rim points confirm coverage after rotating 90 degrees at the same Z.
Geometry validation does not verify clamping, actual axis reach or run it.
"""


def main():
    path = OUT / f"{STEM}.gcode"
    path.write_text(generate(), encoding="ascii")
    report, segments, coverage = decode_and_validate(path)
    (OUT / f"{STEM}_validation.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    preview(segments, coverage)
    (OUT / "facing_Y200_setup.txt").write_text(SETUP, encoding="ascii")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
