# Launch with the same .env settings and GUI plugin setup as the CLI.
$taskRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
& python (Join-Path $taskRepo "run.py") --launch-only
