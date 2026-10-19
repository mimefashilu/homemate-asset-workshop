"""Render simple clay three-view comparisons for candidate review."""
from __future__ import annotations

import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def read_payload(path):
    return json.loads(path.read_text(encoding="utf-8"))


def triangles(payload):
    for mesh_index, mesh in enumerate(payload.get("meshes", [])):
        points = mesh["positions"]
        indices = mesh["indices"]
        for offset in range(0, len(indices), 3):
            corners = []
            depths = []
            for index in indices[offset:offset + 3]:
                start = index * 3
                point = (points[start], points[start + 1], points[start + 2])
                corners.append(point)
                depths.append(point)
            yield mesh_index, corners, depths


def render(payload, view, size=420):
    image = Image.new("RGB", (size, size), (235, 242, 243))
    draw = ImageDraw.Draw(image, "RGBA")
    all_points = [point for _, corners, _ in triangles(payload) for point in corners]
    if not all_points:
        return image
    if view == "front":
        project = lambda point: (point[0], point[1], point[2])
    elif view == "side":
        project = lambda point: (point[2], point[1], point[0])
    else:
        project = lambda point: (-point[0], point[1], -point[2])
    projected = [project(point) for point in all_points]
    min_x = min(point[0] for point in projected); max_x = max(point[0] for point in projected)
    min_y = min(point[1] for point in projected); max_y = max(point[1] for point in projected)
    span = max(max_x - min_x, max_y - min_y, 1e-6)
    scale = size * 0.78 / span
    center_x = (min_x + max_x) / 2; center_y = (min_y + max_y) / 2

    def screen(point):
        x, y, depth = project(point)
        return ((x - center_x) * scale + size / 2, size / 2 - (y - center_y) * scale, depth)

    draw_items = []
    for mesh_index, corners, _ in triangles(payload):
        points = [screen(point) for point in corners]
        depth = sum(point[2] for point in points) / 3
        draw_items.append((depth, mesh_index, points))
    for _, mesh_index, points in sorted(draw_items, key=lambda item: item[0]):
        polygon = [(point[0], point[1]) for point in points]
        base = (61 + mesh_index * 19) % 130
        fill = (44, 112 + base // 3, 128 + base // 4, 155)
        draw.polygon(polygon, fill=fill)
        draw.line(polygon + [polygon[0]], fill=(26, 74, 82, 105), width=1)
    return image


def panel(original, variants, output):
    cell = 360
    canvas = Image.new("RGB", (cell * 3, cell * (len(variants) + 1) + 48), (216, 228, 230))
    draw = ImageDraw.Draw(canvas)
    labels = ["front", "side", "back"]
    for row, (label, payload) in enumerate([("original", original), *variants]):
        draw.text((12, row * cell + 8), label, fill=(35, 57, 61))
        for column, view in enumerate(labels):
            cell_image = render(payload, view, cell - 18)
            canvas.paste(cell_image, (column * cell + 9, row * cell + 30))
            draw.text((column * cell + 15, row * cell + 13), view, fill=(78, 102, 106))
    canvas.save(output)



def render_candidates(workshop, identifiers=None):
    if not identifiers:
        batch = json.loads((workshop.workspace / "recommended.json").read_text(encoding="utf-8"))
        identifiers = batch["candidate_ids"]
    outputs = []
    reports = workshop.workspace / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for identifier in identifiers:
        record, model, folder = workshop.candidate(identifier)
        original = read_payload(workshop.assets / f"{model['id']}.json")
        output = reports / f"{model['id']}-{identifier}.png"
        panel(original, [(record["operation"], read_payload(folder / "preview.json"))], output)
        outputs.append(str(output))
    return {"images": outputs}
