param([string]$Workspace, [string]$Config)
$ErrorActionPreference = 'Stop'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $localPython) { $localPython } else { (Get-Command python.exe -ErrorAction Stop).Source }
$options = @()
if ($Workspace) { $options += @('--workspace', $Workspace) }
if ($Config) { $options += @('--config', $Config) }
& $python -X utf8 (Join-Path $PSScriptRoot 'run.py') stop @options
if ($LASTEXITCODE -ne 0) { throw 'Unable to stop the managed workshop service.' }
