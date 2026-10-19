param([switch]$NoBrowser, [string]$GameDir, [string]$Workspace, [string]$Config, [int]$Port = -1)
$ErrorActionPreference = 'Stop'
$toolDir = $PSScriptRoot
$localPython = Join-Path $toolDir '.venv\Scripts\python.exe'
$python = if (Test-Path -LiteralPath $localPython) { $localPython } else { (Get-Command python.exe -ErrorAction Stop).Source }
& $python -c 'import PIL, numpy' 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Install dependencies: python -m pip install -r requirements.txt' }
$runFile = Join-Path $toolDir 'run.py'
$options = @()
if ($GameDir) { $options += @('--game-dir', $GameDir) }
if ($Workspace) { $options += @('--workspace', $Workspace) }
if ($Config) { $options += @('--config', $Config) }
if ($Port -ge 0) { $options += @('--port', [string]$Port) }
$pathsText = & $python -X utf8 $runFile paths @options
if ($LASTEXITCODE -ne 0) { throw 'Invalid workshop configuration.' }
$paths = $pathsText | ConvertFrom-Json
New-Item -ItemType Directory -Path $paths.workspace -Force | Out-Null
$recordPath = Join-Path $paths.workspace 'server.json'
if (Test-Path -LiteralPath $recordPath) {
    try {
        $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $recordUri = [Uri]$record.url
        if ($recordUri.Host -ne '127.0.0.1' -or $recordUri.Scheme -ne 'http') { throw 'Invalid local URL' }
        $health = Invoke-RestMethod -Uri ($record.url + '/api/health') -TimeoutSec 2
        if ($health.service -eq 'HomeMate Asset Workshop' -and $health.workspace -eq $paths.workspace -and $health.game_dir -eq $paths.game_dir) {
            if (!$NoBrowser) { Start-Process $record.url }
            Write-Host ('READY ' + $record.url)
            exit 0
        }
    } catch { }
}
$arguments = @('-X', 'utf8', ('"' + $runFile + '"'), 'serve', '--no-browser')
foreach ($value in $options) { $arguments += ('"' + $value + '"') }
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $toolDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $paths.workspace 'server.log') -RedirectStandardError (Join-Path $paths.workspace 'server-error.log')
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (Test-Path -LiteralPath $recordPath) {
        try {
            $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $health = Invoke-RestMethod -Uri ($record.url + '/api/health') -TimeoutSec 1
            if ($health.service -eq 'HomeMate Asset Workshop' -and $health.instance -eq $record.instance -and $health.game_dir -eq $paths.game_dir) {
                if (!$NoBrowser) { Start-Process $record.url }
                Write-Host ('READY ' + $record.url)
                exit 0
            }
        } catch { }
    }
    if ($process.HasExited) { throw ('Server stopped. Read ' + (Join-Path $paths.workspace 'server-error.log')) }
    Start-Sleep -Milliseconds 250
}
throw 'Server startup timed out; check workspace/server-error.log.'
