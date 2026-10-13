# Abaqus Python API Command Reference

Legacy, unversioned examples retained for reference. The Agent does not retrieve from this file. Its active source is `abaqus2025_knowledge.json`; use that versioned reference and its official source links.

---

## ConstrainedSketch

### Description
Creates a 2D sketch used for extruding, revolving, or sweeping geometry.

### Syntax
```python
s = mdb.models['Model-1'].ConstrainedSketch(name='__profile__', sheetSize=200.0)
s.rectangle(point1=(0.0, 0.0), point2=(100.0, 20.0))
s.CircleByCenterPerimeter(center=(50.0, 10.0), point1=(55.0, 10.0))
s.Line(point1=(0.0, 0.0), point2=(50.0, 50.0))
```

### Parameters
- `name`: String name of sketch.
- `sheetSize`: Float canvas dimension.

---

## Part

### Description
Creates a geometric part in the model database.

### Syntax
```python
p = mdb.models['Model-1'].Part(name='Beam', dimensionality=THREE_D, type=DEFORMABLE_BODY)
p.BaseSolidExtrude(sketch=s, depth=20.0)
# Or for 2D Planar:
p2d = mdb.models['Model-1'].Part(name='Plate', dimensionality=TWO_D_PLANAR, type=DEFORMABLE_BODY)
p2d.BaseShell(sketch=s)
```

### Parameters
- `dimensionality`: `THREE_D`, `TWO_D_PLANAR`, or `AXISYMMETRIC`.
- `type`: `DEFORMABLE_BODY`, `DISCRETE_RIGID`, or `ANALYTICAL_RIGID`.

---

## Material

### Description
Defines constitutive mechanical, thermal, or physical material models.

### Syntax
```python
mat = mdb.models['Model-1'].Material(name='Steel')
mat.Elastic(table=((210000.0, 0.3), ))
mat.Density(table=((7.85e-09, ), ))
mat.Plastic(table=((350.0, 0.0), (450.0, 0.05), (550.0, 0.15)))
mat.Expansion(table=((1.2e-05, ), ))
```

### Parameters
- `Elastic`: `table=((YoungsModulus, PoissonsRatio), )`.
- `Density`: `table=((DensityValue, ), )`.
- `Plastic`: `table=((YieldStress, PlasticStrain), ...)`.

---

## HomogeneousSolidSection

### Description
Creates a 3D solid continuum homogeneous section.

### Syntax
```python
mdb.models['Model-1'].HomogeneousSolidSection(name='SolidSec', material='Steel', thickness=None)
```

---

## HomogeneousShellSection

### Description
Creates a 2D shell section with uniform thickness.

### Syntax
```python
mdb.models['Model-1'].HomogeneousShellSection(
    name='ShellSec',
    material='Steel',
    thickness=2.0,
    numIntPts=5
)
```

---

## SectionAssignment

### Description
Assigns a defined section to geometric cells or faces of a part.

### Syntax
```python
p = mdb.models['Model-1'].parts['Beam']
cells = p.cells
region = p.Set(cells=cells, name='AllCells')
p.SectionAssignment(region=region, sectionName='SolidSec', offset=0.0)
```

---

## Assembly & Instance

### Description
Instances parts into the global coordinate assembly for meshing and analysis.

### Syntax
```python
a = mdb.models['Model-1'].rootAssembly
a.DatumCsysByDefault(CARTESIAN)
inst = a.Instance(name='Beam-1', part=p, dependent=ON)
a.regenerate()
```

### Parameters
- `dependent`: `ON` (mesh resides on part) or `OFF` (mesh resides on assembly instance).

---

## StaticStep

### Description
Defines a general static analysis step.

### Syntax
```python
mdb.models['Model-1'].StaticStep(
    name='Step-1',
    previous='Initial',
    timePeriod=1.0,
    maxNumInc=1000,
    initialInc=0.1,
    minInc=1e-5,
    maxInc=1.0,
    nlgeom=OFF
)
```

### Parameters
- `nlgeom`: `ON` for large-displacement geometric nonlinearity, `OFF` for linear.

---

## ExplicitDynamicsStep

### Description
Defines an Abaqus/Explicit dynamic step for high-speed impact or wave propagation.

### Syntax
```python
mdb.models['Model-1'].ExplicitDynamicsStep(
    name='ImpactStep',
    previous='Initial',
    timePeriod=0.01,
    nlgeom=ON
)
```

---

## FrequencyStep

### Description
Eigenvalue natural frequency extraction step.

### Syntax
```python
mdb.models['Model-1'].FrequencyStep(
    name='ModalStep',
    previous='Initial',
    numEigen=10,
    eigensolver=LANCZOS
)
```

---

## Boundary Conditions (Encastre & DisplacementBC)

### Description
Applies fixed or prescribed displacements and rotations to nodes, vertices, or faces.

### Syntax
```python
a = mdb.models['Model-1'].rootAssembly
# Fix face (Encastre):
faces = a.instances['Beam-1'].faces.findAt(((0.0, 10.0, 10.0), ))
region = a.Set(faces=faces, name='FixedEnd')
mdb.models['Model-1'].EncastreBC(name='Fix-Base', createStepName='Initial', region=region)

# Prescribed displacement:
mdb.models['Model-1'].DisplacementBC(
    name='Disp-Z',
    createStepName='Step-1',
    region=region,
    u1=0.0, u2=UNSET, u3=-5.0,
    ur1=UNSET, ur2=UNSET, ur3=UNSET
)
```

---

## Loads (Pressure & ConcentratedForce)

### Description
Applies structural loads to geometric surfaces, edges, or vertices.

### Syntax
```python
a = mdb.models['Model-1'].rootAssembly
# Surface pressure:
top_faces = a.instances['Beam-1'].faces.findAt(((50.0, 20.0, 10.0), ))
press_region = a.Surface(side1Faces=top_faces, name='TopPressureSurf')
mdb.models['Model-1'].Pressure(
    name='ApplyPressure',
    createStepName='Step-1',
    region=press_region,
    magnitude=10.0
)

# Concentrated force on vertex:
verts = a.instances['Beam-1'].vertices.findAt(((100.0, 20.0, 20.0), ))
pt_region = a.Set(vertices=verts, name='LoadPt')
mdb.models['Model-1'].ConcentratedForce(
    name='PointForce',
    createStepName='Step-1',
    region=pt_region,
    cf2=-1000.0
)
```

---

## Mesh Generation (seedPart & generateMesh)

### Description
Applies seed sizing, controls element formulation, and generates finite element mesh.

### Syntax
```python
p = mdb.models['Model-1'].parts['Beam']
p.seedPart(size=5.0, deviationFactor=0.1, minSizeFactor=0.1)

import mesh
elemType1 = mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD)
elemType2 = mesh.ElemType(elemCode=C3D6, elemLibrary=STANDARD)
p.setElementType(regions=(p.cells, ), elemTypes=(elemType1, elemType2))
p.setMeshControls(regions=p.cells, technique=SWEEP)
p.generateMesh()
```

---

## Job Creation & Submission

### Description
Creates simulation analysis job, writes input deck (.inp), and monitors run.

### Syntax
```python
myJob = mdb.Job(
    name='Job-Beam',
    model='Model-1',
    description='Automated beam analysis',
    type=ANALYSIS,
    numCpus=4,
    numDomains=4,
    multiprocessingMode=DEFAULT
)
myJob.submit(consistencyChecking=ON)
# Return here in interactive tasks; inspect myJob.status in a later task.
```

---

## Post-Processing (Visualization & openOdb)

### Description
Opens the Output Database (.odb) to extract node displacements, reaction forces, and element stresses.

### Syntax
```python
from visualization import openOdb
import numpy as np

odb = openOdb('Job-Beam.odb')
last_frame = odb.steps['Step-1'].frames[-1]

# Extract Von Mises Stress
stress_field = last_frame.fieldOutputs['S']
mises_values = [val.mises for val in stress_field.values if val.mises is not None]
max_mises = max(mises_values) if mises_values else 0.0

# Extract Displacement magnitude
disp_field = last_frame.fieldOutputs['U']
disp_mags = [val.magnitude for val in disp_field.values]
max_disp = max(disp_mags) if disp_mags else 0.0

print("MAX_STRESS_MISES: %f MPa" % (max_mises))
print("MAX_DISPLACEMENT: %f mm" % (max_disp))
odb.close()
```
