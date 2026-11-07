# Opens this fork's HyperFrames Studio in its own desktop window.
# Usage: HyperFramesFork.ps1 [project folder]. With no folder it asks, starting from the last project used.
param([string]$Project)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cli = Join-Path $repo 'packages\cli\dist\cli.js'
$dataDir = Join-Path $env:LOCALAPPDATA 'HyperFramesFork'
$lastFile = Join-Path $dataDir 'last-project.txt'
New-Item -ItemType Directory -Force $dataDir | Out-Null

function Fail($message) {
    [System.Windows.Forms.MessageBox]::Show($message, 'HyperFrames Fork', 'OK', 'Error') | Out-Null
    exit 1
}

function Find-Browser {
    $candidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    return $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

if (-not (Test-Path $cli)) { Fail "The fork isn't built yet. In $repo run: bun install, then bun run build" }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail 'Node.js was not found.' }
$browser = Find-Browser
if (-not $browser) { Fail 'Chrome or Edge is needed to show the Studio window.' }

if (-not $Project) {
    $start = if (Test-Path $lastFile) { (Get-Content $lastFile -Raw).Trim() } else { [Environment]::GetFolderPath('Desktop') }
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose a HyperFrames project folder'
    if (Test-Path $start) { $dialog.SelectedPath = $start }
    if ($dialog.ShowDialog() -ne 'OK') { exit 0 }
    $Project = $dialog.SelectedPath
}

if (-not (Test-Path (Join-Path $Project 'index.html'))) {
    Fail "That folder isn't a HyperFrames project, because it has no index.html:`n$Project"
}
Set-Content -Path $lastFile -Value $Project

Push-Location $Project
try {
    $output = & node $cli preview --background --no-open --json --force-new 2>&1 | Out-String
    $json = $output.Substring($output.IndexOf('{')) | ConvertFrom-Json
    if (-not $json.ok) { Fail "The Studio didn't start:`n$output" }
    $url = "http://$($json.result.host):$($json.result.port)/"

    # A separate browser profile makes this a real window of its own, and lets us know when it closes.
    $profile = Join-Path $dataDir 'window'
    Start-Process -FilePath $browser -Wait -ArgumentList @(
        "--app=$url", "--user-data-dir=`"$profile`"", '--window-size=1500,950', '--no-first-run', '--no-default-browser-check'
    )
} catch {
    Fail "The Studio didn't start:`n$_"
} finally {
    & node $cli preview --stop 2>&1 | Out-Null
    Pop-Location
}
