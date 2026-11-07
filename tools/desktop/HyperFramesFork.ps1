# Opens this fork's HyperFrames Studio in its own desktop window.
# Usage: HyperFramesFork.ps1 [project folder]. With no folder it shows the Windows Explorer Open window.
param([string]$Project)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cli = Join-Path $repo 'packages\cli\dist\cli.js'
$dataDir = Join-Path $env:LOCALAPPDATA 'HyperFramesFork'
$lastFile = Join-Path $dataDir 'last-project.txt'
New-Item -ItemType Directory -Force $dataDir | Out-Null

function Fail($message) {
    [System.Windows.Forms.MessageBox]::Show($message, 'HyperFrames Fork', 'OK', 'Error') | Out-Null
    exit 1
}

Add-Type -Namespace HfFork -Name Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
public delegate bool EnumProc(IntPtr h, IntPtr lParam);
public static long[] WindowsTitled(string title) {
    var found = new System.Collections.Generic.List<long>();
    EnumWindows((h, l) => {
        if (!IsWindowVisible(h)) return true;
        var sb = new System.Text.StringBuilder(512);
        GetWindowText(h, sb, 512);
        if (sb.ToString() == title) found.Add(h.ToInt64());
        return true;
    }, IntPtr.Zero);
    return found.ToArray();
}
'@

# Handles of the Studio app windows open right now.
function Get-StudioWindows { return @([HfFork.Win]::WindowsTitled('HyperFrames Studio')) }

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

function Test-Project($dir) {
    return (Test-Path -LiteralPath (Join-Path $dir 'index.html')) -and (Test-Path -LiteralPath (Join-Path $dir 'hyperframes.json'))
}

# Where the Explorer dialog starts: the folder holding the last project, else the first videos folder found.
function Get-StartFolder {
    if (Test-Path -LiteralPath $lastFile) {
        $last = (Get-Content -LiteralPath $lastFile -Raw).Trim()
        if ($last -and (Test-Path -LiteralPath $last)) { return (Split-Path -Parent $last) }
    }
    $desktop = [Environment]::GetFolderPath('Desktop')
    foreach ($dir in @("$desktop\video-pipeline\videos", "$desktop\ClaudeTransfer\videos", 'F:\video-pipeline\videos', $desktop)) {
        if (Test-Path -LiteralPath $dir) { return $dir }
    }
    return $desktop
}

# The standard Windows Explorer "Open" window. Go into a project folder and open its index.html.
function Select-Project {
    [System.Windows.Forms.Application]::EnableVisualStyles()
    $owner = New-Object System.Windows.Forms.Form -Property @{ TopMost = $true; ShowInTaskbar = $false; Opacity = 0 }
    $owner.Show()
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'HyperFrames Fork - open a project (choose its index.html)'
    $dialog.Filter = 'HyperFrames project (index.html, hyperframes.json)|index.html;hyperframes.json|All files (*.*)|*.*'
    $dialog.InitialDirectory = Get-StartFolder
    $dialog.AutoUpgradeEnabled = $true
    try {
        while ($dialog.ShowDialog($owner) -eq 'OK') {
            $dir = Split-Path -Parent $dialog.FileName
            if (Test-Project $dir) { return $dir }
            [System.Windows.Forms.MessageBox]::Show(
                $owner,
                "That isn't a HyperFrames project. Open the index.html inside a project folder (one that also has hyperframes.json).`n`n$dir",
                'HyperFrames Fork', 'OK', 'Information'
            ) | Out-Null
            $dialog.InitialDirectory = $dir
        }
    } finally {
        $owner.Close()
    }
    return $null
}

if (-not $Project) {
    $Project = Select-Project
    if (-not $Project) { exit 0 }
}

if (-not (Test-Project $Project)) {
    Fail "That folder isn't a HyperFrames project. A project folder has both index.html and hyperframes.json:`n$Project"
}
Set-Content -LiteralPath $lastFile -Value $Project

# Stops only the server this launcher started. `preview --stop` stops every Studio for the project, so
# closing one window used to kill the server behind another window of the same project.
$serverPid = $null
function Stop-OwnServer {
    if (-not $serverPid) { return }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match 'preview') {
        & taskkill.exe /PID $serverPid /T /F 2>&1 | Out-Null
    }
}

Push-Location $Project
try {
    $output = & node $cli preview --background --no-open --json --force-new 2>&1 | Out-String
    $json = $output.Substring($output.IndexOf('{')) | ConvertFrom-Json
    if (-not $json.ok) { Fail "The Studio didn't start:`n$output" }
    $serverPid = $json.result.pid
    $url = "http://$($json.result.host):$($json.result.port)/"

    # Open an app window in the browser's normal profile. A separate, fresh profile played Studio's
    # preview without sound on this PC, while the normal profile plays it with sound.
    $before = Get-StudioWindows
    Start-Process -FilePath $browser -ArgumentList @("--new-window", "--app=$url", '--window-size=1500,950')

    # The browser hands the window to its running process, so wait for the window itself: the new
    # window titled exactly like the page (normal tabs end in "- Google Chrome"), then until it closes.
    $mine = $null
    for ($i = 0; $i -lt 120 -and -not $mine; $i++) {
        Start-Sleep -Milliseconds 500
        $mine = Get-StudioWindows | Where-Object { $before -notcontains $_ } | Select-Object -First 1
    }
    if (-not $mine) { Fail "The Studio window didn't open." }
    while ([HfFork.Win]::IsWindow([IntPtr]$mine)) { Start-Sleep -Seconds 2 }
} catch {
    Fail "The Studio didn't start:`n$_"
} finally {
    Stop-OwnServer
    Pop-Location
}
