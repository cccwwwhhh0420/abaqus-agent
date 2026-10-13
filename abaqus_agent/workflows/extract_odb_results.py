# -*- coding: utf-8 -*-
"""Post-processing and ODB Result Extraction Script for Abaqus/CAE.

Opens an output database (.odb), inspects step and frame count,
extracts maximum von Mises stress, maximum displacement magnitude,
and reaction forces, then returns a summary report.
"""

import sys
import os

try:
    from visualization import openOdb
except ImportError:
    pass


def extract_odb_summary(odb_path):
    """Extract key FEA results from specified ODB file."""
    if not os.path.isfile(odb_path):
        return {"error": "ODB file not found: %s" % odb_path}

    try:
        odb = openOdb(odb_path)
    except Exception as e:
        return {"error": "Failed to open ODB (%s): %s" % (odb_path, str(e))}

    report = {
        "odb_name": os.path.basename(odb_path),
        "steps": list(odb.steps.keys()),
        "step_results": {}
    }

    try:
        for step_name, step_obj in odb.steps.items():
            num_frames = len(step_obj.frames)
            if num_frames == 0:
                continue

            last_frame = step_obj.frames[-1]
            frame_data = {
                "frame_id": last_frame.frameId,
                "description": last_frame.description,
                "available_fields": list(last_frame.fieldOutputs.keys())
            }

            # 1. Stress (S)
            if 'S' in last_frame.fieldOutputs:
                s_field = last_frame.fieldOutputs['S']
                mises_vals = [val.mises for val in s_field.values if getattr(val, 'mises', None) is not None]
                if mises_vals:
                    frame_data["max_von_mises"] = max(mises_vals)
                    frame_data["min_von_mises"] = min(mises_vals)

            # 2. Displacement (U)
            if 'U' in last_frame.fieldOutputs:
                u_field = last_frame.fieldOutputs['U']
                mag_vals = [val.magnitude for val in u_field.values if getattr(val, 'magnitude', None) is not None]
                if mag_vals:
                    frame_data["max_displacement_mag"] = max(mag_vals)

            # 3. Reaction Force (RF)
            if 'RF' in last_frame.fieldOutputs:
                rf_field = last_frame.fieldOutputs['RF']
                rf_mags = [val.magnitude for val in rf_field.values if getattr(val, 'magnitude', None) is not None]
                if rf_mags:
                    frame_data["max_reaction_force"] = max(rf_mags)

            report["step_results"][step_name] = frame_data

    finally:
        odb.close()

    return report


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_odb = sys.argv[1]
        res = extract_odb_summary(target_odb)
        print("[ODB Extraction Result]")
        import pprint
        pprint.pprint(res)
    else:
        print("Usage: abaqus python extract_odb_results.py <path_to_odb>")
