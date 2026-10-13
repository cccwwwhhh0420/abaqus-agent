# -*- coding: utf-8 -*-
"""Parametric 3D Cantilever Beam Simulation Workflow for Abaqus/CAE.

Creates geometry, defines material, assigns section, instances assembly,
configures static step, applies encastre BC and pressure load, generates mesh,
creates job and optionally submits.
"""

from abaqus import *
from abaqusConstants import *
import part
import material
import section
import assembly
import step
import interaction
import load
import mesh
import job


def create_cantilever_beam(
    model_name="Model-1",
    length=200.0,
    width=20.0,
    height=20.0,
    youngs_modulus=210000.0,
    poissons_ratio=0.3,
    mesh_size=4.0,
    pressure_val=5.0
):
    """Build and mesh a 3D cantilever beam model."""
    # 1. Get or create model
    if model_name not in mdb.models:
        m = mdb.Model(name=model_name)
    else:
        m = mdb.models[model_name]

    # 2. Sketch and Extrude Part
    s = m.ConstrainedSketch(name='__profile__', sheetSize=200.0)
    s.rectangle(point1=(0.0, 0.0), point2=(length, height))
    p = m.Part(name='BeamPart', dimensionality=THREE_D, type=DEFORMABLE_BODY)
    p.BaseSolidExtrude(sketch=s, depth=width)
    del m.sketches['__profile__']

    # 3. Material & Section
    mat = m.Material(name='StructuralSteel')
    mat.Elastic(table=((youngs_modulus, poissons_ratio), ))
    mat.Density(table=((7.85e-09, ), ))

    m.HomogeneousSolidSection(name='SolidSec', material='StructuralSteel', thickness=None)
    cells = p.cells
    region = p.Set(cells=cells, name='AllCells')
    p.SectionAssignment(region=region, sectionName='SolidSec', offset=0.0)

    # 4. Assembly Instance
    a = m.rootAssembly
    a.DatumCsysByDefault(CARTESIAN)
    inst = a.Instance(name='BeamInst', part=p, dependent=ON)

    # 5. Step
    if 'StaticLoadStep' not in m.steps:
        m.StaticStep(name='StaticLoadStep', previous='Initial', timePeriod=1.0, nlgeom=OFF)

    # 6. Boundary Condition (Fixed at X=0)
    fixed_faces = inst.faces.findAt(((0.0, height / 2.0, width / 2.0), ))
    fixed_region = a.Set(faces=fixed_faces, name='FixedEnd')
    m.EncastreBC(name='FixedBase', createStepName='Initial', region=fixed_region)

    # 7. Pressure Load on Top Surface (Y = height)
    top_faces = inst.faces.findAt(((length / 2.0, height, width / 2.0), ))
    press_region = a.Surface(side1Faces=top_faces, name='TopPressureSurf')
    m.Pressure(name='UniformPressure', createStepName='StaticLoadStep', region=press_region, magnitude=pressure_val)

    # 8. Mesh Part
    p.seedPart(size=mesh_size, deviationFactor=0.1, minSizeFactor=0.1)
    elem_type = mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD)
    p.setElementType(regions=(p.cells, ), elemTypes=(elem_type, ))
    p.setMeshControls(regions=p.cells, technique=SWEEP)
    p.generateMesh()
    a.regenerate()

    # 9. Create Job
    job_name = 'Job-Cantilever'
    if job_name in mdb.jobs:
        del mdb.jobs[job_name]
    j = mdb.Job(name=job_name, model=model_name, description='Automated Cantilever Beam Analysis')
    print("[Workflow] Cantilever beam model created successfully: Job '%s' ready." % job_name)
    return j


if __name__ == "__main__":
    create_cantilever_beam()
