# Parametric fixture for Raspberry Pi Pico + SFH 2440 photodiode.
# Run inside Fusion 360: Scripts and Add-Ins -> Scripts -> + -> point at this file.

import adsk.core
import adsk.fusion
import traceback


HOUSING_L = 6.0     # 60 mm  (X)
HOUSING_W = 3.0     # 30 mm  (Y)
HOUSING_H = 2.0     # 20 mm  (Z)

PICO_L = 5.13       # 51.3 mm
PICO_W = 2.13       # 21.3 mm
PICO_H = 0.5        # 5 mm

USB_L = 0.95        # 9.5 mm  (along X, toward housing edge)
USB_W = 0.36        # 3.6 mm  (along Y)

SFH_L = 0.47        # 4.7 mm
SFH_W = 0.42        # 4.2 mm
SFH_D = 0.15        # 1.5 mm deep pocket

APERTURE_DIA = 0.25  # 2.5 mm through-hole


def _rect_body(root, plane, center_x, center_y, length_x, length_y, z_base, height, name):
    """Create an axis-aligned rectangular prism by extrusion."""
    sketch = root.sketches.add(plane)
    lines = sketch.sketchCurves.sketchLines
    p0 = adsk.core.Point3D.create(center_x - length_x / 2.0, center_y - length_y / 2.0, 0)
    p1 = adsk.core.Point3D.create(center_x + length_x / 2.0, center_y + length_y / 2.0, 0)
    lines.addTwoPointRectangle(p0, p1)

    prof = sketch.profiles.item(0)
    ext_input = root.features.extrudeFeatures.createInput(
        prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
    )
    distance = adsk.core.ValueInput.createByReal(height)
    ext_input.setOneSideExtent(
        adsk.fusion.DistanceExtentDefinition.create(distance),
        adsk.fusion.ExtentDirections.PositiveExtentDirection,
    )
    # Offset the sketch plane is awkward; instead translate the body afterwards.
    feat = root.features.extrudeFeatures.add(ext_input)
    body = feat.bodies.item(0)
    body.name = name

    if z_base != 0.0:
        tr = adsk.core.Matrix3D.create()
        tr.translation = adsk.core.Vector3D.create(0, 0, z_base)
        coll = adsk.core.ObjectCollection.create()
        coll.add(body)
        move_input = root.features.moveFeatures.createInput(coll, tr)
        root.features.moveFeatures.add(move_input)

    return body


def _cyl_body(root, plane, center_x, center_y, diameter, z_base, height, name):
    sketch = root.sketches.add(plane)
    circles = sketch.sketchCurves.sketchCircles
    circles.addByCenterRadius(
        adsk.core.Point3D.create(center_x, center_y, 0), diameter / 2.0
    )
    prof = sketch.profiles.item(0)
    ext_input = root.features.extrudeFeatures.createInput(
        prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
    )
    ext_input.setOneSideExtent(
        adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByReal(height)
        ),
        adsk.fusion.ExtentDirections.PositiveExtentDirection,
    )
    feat = root.features.extrudeFeatures.add(ext_input)
    body = feat.bodies.item(0)
    body.name = name

    if z_base != 0.0:
        tr = adsk.core.Matrix3D.create()
        tr.translation = adsk.core.Vector3D.create(0, 0, z_base)
        coll = adsk.core.ObjectCollection.create()
        coll.add(body)
        move_input = root.features.moveFeatures.createInput(coll, tr)
        root.features.moveFeatures.add(move_input)

    return body


def _combine_cut(root, target_body, tool_bodies):
    tools = adsk.core.ObjectCollection.create()
    for b in tool_bodies:
        tools.add(b)
    combine_input = root.features.combineFeatures.createInput(target_body, tools)
    combine_input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
    combine_input.isKeepToolBodies = False
    root.features.combineFeatures.add(combine_input)


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            ui.messageBox("No active Fusion design.")
            return
        root = design.rootComponent
        xy = root.xYConstructionPlane

        # Housing: centered on origin in XY, Z = 0 .. HOUSING_H
        housing = _rect_body(
            root, xy, 0.0, 0.0, HOUSING_L, HOUSING_W, 0.0, HOUSING_H, "Housing"
        )

        # Pico cavity: top-aligned, 5 mm deep from top face, centered in XY.
        pico = _rect_body(
            root, xy, 0.0, 0.0, PICO_L, PICO_W,
            HOUSING_H - PICO_H, PICO_H, "PicoCavity",
        )

        # USB Type-C slot on top face: aligned with one short end of the Pico,
        # extending from the housing edge inward so a cable can drop straight in.
        usb_center_x = HOUSING_L / 2.0 - USB_L / 2.0
        usb = _rect_body(
            root, xy, usb_center_x, 0.0, USB_L, USB_W,
            HOUSING_H - PICO_H, PICO_H, "USB_Slot",
        )

        # SFH 2440 pocket on the bottom face, centered in XY.
        sfh = _rect_body(
            root, xy, 0.0, 0.0, SFH_L, SFH_W, 0.0, SFH_D, "SFH2440_Pocket"
        )

        # Aperture: 2.5 mm through-hole at the centre of the pocket.
        aperture = _cyl_body(
            root, xy, 0.0, 0.0, APERTURE_DIA, 0.0, HOUSING_H, "Aperture"
        )

        # Single combine-cut subtracts all tool volumes from the housing.
        _combine_cut(root, housing, [pico, usb, sfh, aperture])

        ui.messageBox("Pico + SFH 2440 fixture created.")

    except Exception:
        if ui:
            ui.messageBox("Failed:\n{}".format(traceback.format_exc()))
