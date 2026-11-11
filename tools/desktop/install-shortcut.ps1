# Puts a "HyperFrames Fork" shortcut on the Windows desktop. Run it once, or again if the fork moves.
# Usage: install-shortcut.ps1 [-Project <folder> -Name <shortcut name>] for a shortcut that opens one project directly,
# or install-shortcut.ps1 -Features for a "HyperFrames Features" shortcut to the feature menu.
param([string]$Project, [string]$Name = 'HyperFrames Fork', [switch]$Features)

$launcher = Join-Path $PSScriptRoot 'HyperFramesFork.ps1'
if ($Features) {
    $launcher = Join-Path $PSScriptRoot 'HyperFramesFeatures.ps1'
    if ($Name -eq 'HyperFrames Fork') { $Name = 'HyperFrames Features' }
}
$desktop = [Environment]::GetFolderPath('Desktop')
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
if ($Project) { $arguments += " -Project `"$Project`"" }

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop "$Name.lnk"))
# Windows 11 hosts consoles in Windows Terminal, which ignores -WindowStyle Hidden and shows a window that
# kills the launcher when closed. A headless conhost runs it with no window at all.
$link.TargetPath = "$env:SystemRoot\System32\conhost.exe"
$link.Arguments = "--headless `"$powershell`" $arguments"
$link.WorkingDirectory = $PSScriptRoot
$link.IconLocation = (Join-Path $PSScriptRoot 'app.ico')
$link.Description = if ($Features) { 'Every HyperFrames feature, what it does and what to ask Claude' } elseif ($Project) { "Opens $(Split-Path -Leaf $Project) in HyperFrames Studio" } else { 'HyperFrames Studio from your fork' }
$link.Save()
Write-Output "Created $($link.FullName)"
