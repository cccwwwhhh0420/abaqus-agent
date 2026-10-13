# -*- coding: utf-8 -*-
"""Register GUI bridge controls and automatically start event dispatch."""
from __future__ import print_function
from abaqusGui import getAFXApp
from abaqusConstants import ALL
from abaqus_ai_gui import GuiBridge

toolset = getAFXApp().getAFXMainWindow().getPluginToolset()
# Retain the target for the lifetime of the GUI; FOX holds callback references.
bridge = GuiBridge()
for label, message in (("Start Bridge", bridge.ID_START),
                       ("Stop Bridge", bridge.ID_STOP)):
    toolset.registerGuiMenuButton(
        buttonText="Abaqus AI|" + label, object=bridge, messageId=message,
        applicableModules=ALL, version="2.0", author="Abaqus AI Agent",
        description="Control the GUI event bridge", helpUrl="")
toolset.registerKernelMenuButton(
    buttonText="Abaqus AI|Run Single Task", moduleName="abaqus_ai_kernel",
    functionName="run_task()", applicableModules=ALL, version="2.0",
    author="Abaqus AI Agent", description="Manual fallback: execute one queued task",
    helpUrl="")
try:
    bridge.start()
except Exception as exc:
    print("[Abaqus-AI] GUI bridge could not start: " + str(exc))
