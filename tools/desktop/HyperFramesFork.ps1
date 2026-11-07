# Opens this fork's HyperFrames Studio in its own desktop window.
# Usage: HyperFramesFork.ps1 [project folder]. With no folder it lists your projects, last used first.
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

# Project folders directly inside $root, plus $root itself if it is one. Reads names only, one level deep.
function Find-Projects($root) {
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $dirs = @($root) + @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    return @($dirs | Where-Object { Test-Project $_ })
}

function Select-Project {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $found = New-Object System.Collections.ArrayList
    $add = { param($dir) if ($dir -and -not $found.Contains($dir)) { [void]$found.Add($dir) } }

    if (Test-Path -LiteralPath $lastFile) {
        $last = (Get-Content -LiteralPath $lastFile -Raw).Trim()
        if ($last -and (Test-Project $last)) { & $add $last }
    }
    foreach ($root in @(
        "$desktop\video-pipeline\videos", "$desktop\ClaudeTransfer\videos", "$desktop\ClaudeTransfer",
        "$env:USERPROFILE\video-pipeline\videos", 'F:\video-pipeline\videos', 'F:\ClaudeTransfer\videos'
    )) {
        foreach ($dir in Find-Projects $root) { & $add $dir }
    }

    [System.Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'HyperFrames Fork'
    $form.ClientSize = New-Object System.Drawing.Size(640, 330)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.TopMost = $true
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 10)

    $label = New-Object System.Windows.Forms.Label
    $label.Text = if ($found.Count) { 'Choose a project to open:' } else { 'No projects found yet. Click Browse to find one.' }
    $label.SetBounds(14, 12, 610, 24)

    $list = New-Object System.Windows.Forms.ListBox
    $list.SetBounds(14, 40, 612, 230)
    $list.HorizontalScrollbar = $true
    foreach ($dir in $found) { [void]$list.Items.Add($dir) }
    if ($found.Count) { $list.SelectedIndex = 0 }

    $open = New-Object System.Windows.Forms.Button
    $open.Text = 'Open'
    $open.SetBounds(356, 282, 86, 32)
    $browse = New-Object System.Windows.Forms.Button
    $browse.Text = 'Browse...'
    $browse.SetBounds(448, 282, 86, 32)
    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Text = 'Cancel'
    $cancel.SetBounds(540, 282, 86, 32)
    $cancel.DialogResult = 'Cancel'
    $form.AcceptButton = $open
    $form.CancelButton = $cancel

    $accept = {
        if ($list.SelectedItem) {
            $form.Tag = [string]$list.SelectedItem
            $form.DialogResult = 'OK'
            $form.Close()
        }
    }
    $open.Add_Click($accept)
    $list.Add_DoubleClick($accept)
    $browse.Add_Click({
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Choose a HyperFrames project folder, or a folder that contains projects'
        $dialog.SelectedPath = $desktop
        if ($dialog.ShowDialog($form) -ne 'OK') { return }
        $hits = @(Find-Projects $dialog.SelectedPath)
        if (-not $hits.Count) {
            [System.Windows.Forms.MessageBox]::Show(
                $form,
                "No HyperFrames project there. A project folder has both index.html and hyperframes.json.`n`n$($dialog.SelectedPath)",
                'HyperFrames Fork', 'OK', 'Information'
            ) | Out-Null
            return
        }
        foreach ($dir in $hits) { if (-not $list.Items.Contains($dir)) { [void]$list.Items.Add($dir) } }
        $list.SelectedItem = $hits[0]
        $label.Text = 'Choose a project to open:'
    })

    $form.Controls.AddRange(@($label, $list, $open, $browse, $cancel))
    if ($form.ShowDialog() -ne 'OK') { return $null }
    return [string]$form.Tag
}

if (-not $Project) {
    $Project = Select-Project
    if (-not $Project) { exit 0 }
}

if (-not (Test-Project $Project)) {
    Fail "That folder isn't a HyperFrames project. A project folder has both index.html and hyperframes.json:`n$Project"
}
Set-Content -LiteralPath $lastFile -Value $Project

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
