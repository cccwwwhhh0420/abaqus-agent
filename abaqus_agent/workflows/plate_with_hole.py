# -*- coding: utf-8 -*-
"""Parametric 2D Plate with Central Hole Workflow for Abaqus/CAE.

Demonstrates 2D planar modeling, sketch with circle partition, shell section,
boundary conditions, tensile edge loading, and free/quad meshing.
"""

from abaqus import *
from abaqusConstants import *
import part
import material
import section
import assembly
import step
import load
import mesh
import job


def create_plate_with_hole(
    model_name="Model-1",
    plate_width=100.0,
    plate_height=100.0,
    hole_radius=15.0,
    thickness=2.0,
    tensile_pressure=100.0
):
    """Build a 2D planar plate with central circular hole."""
    if model_name not in mdb.models:
        m = mdb.Model(name=model_name)
    else:
        m = mdb.models[model_name]

    # 1. Sketch Plate and Hole
    s = m.ConstrainedSketch(name='__plate_sketch__', sheetSize=200.0)
    s.rectangle(point1=(-plate_width / 2.0, -plate_height / 2.0),
                point2=(plate_width / 2.0, plate_height / 2.0))
    s.CircleByCenterPerimeter(center=(0.0, 0.0), point1=(hole_radius, 0.0))

    # 2. 2D Planar Part
    p = m.Part(name='PlatePart', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
    p.BaseShell(sketch=s)
    del m.sketches['__plate_sketch__']

    # 3. Material & Shell Section
    mat = m.Material(name='Aluminum')
    mat.Elastic(table=((70000.0, 0.33), ))
    mat.Density(table=((2.7e-09, ), ))

    m.HomogeneousShellSection(name='PlateSec', material='Aluminum', thickness=thickness)
    faces = p.faces
    region = p.Set(faces=faces, name='AllFaces')
    p.SectionAssignment(region=region, sectionName='PlateSec')

    # 4. Assembly Instance
    a = m.rootAssembly
    a.DatumCsysByDefault(CARTESIAN)
    inst = a.Instance(name='PlateInst', part=p, dependent=ON)

    # 5. Static Step
    if 'TensionStep' not in m.steps:
        m.StaticStep(name='TensionStep', previous='Initial', timePeriod=1.0)

    # 6. BC & Load
    # Left edge fixed in X (symmetry or roller)
    left_edge = inst.edges.findAt(((-plate_width / 2.0, 0.0, 0.0), ))
    left_region = a.Set(edges=left_edge, name='LeftEdge')
    m.DisplacementBC(name='FixX_Left', createStepName='Initial', region=left_region, u1=0.0)

    # Bottom left vertex fixed in Y to prevent rigid translation
    bot_left_v = inst.vertices.findAt(((-plate_width / 2.0, -plate_height / 2.0, 0.0), ))
    bot_v_region = a.Set(vertices=bot_left_v, name='BotLeftV')
    m.DisplacementBC(name='FixY_Point', createStepName='Initial', region=bot_v_region, u2=0.0)

    # Right edge tensile traction (Pressure negative)
    right_edge = inst.edges.findAt(((plate_width / 2.0, 0.0, 0.0), ))
    right_surf = a.Surface(side1Edges=right_edge, name='RightTensionSurf')
    m.Pressure(name='TensilePull', createStepName='TensionStep', region=right_surf, magnitude=-tensile_pressure)

    # 7. Mesh
    p.seedPart(size=3.0, deviationFactor=0.1)
    elem_type = mesh.ElemType(elemCode=CPS4R, elemLibrary=STANDARD)
    p.setElementType(regions=(p.faces, ), elemTypes=(elem_type, ))
    p.generateMesh()
    a.regenerate()

    # 8. Job
    job_name = 'Job-PlateHole'
    if job_name in mdb.jobs:
        del mdb.jobs[job_name]
    j = mdb.Job(name=job_name, model=model_name, description='Plate with Central Hole Analysis')
    print("[Workflow] Plate with central hole created successfully: Job '%s' ready." % job_name)
    return j


if __name__ == "__main__":
    create_plate_with_hole()
