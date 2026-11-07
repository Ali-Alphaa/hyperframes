# Puts a "HyperFrames Fork" shortcut on the Windows desktop. Run it once, or again if the fork moves.
$launcher = Join-Path $PSScriptRoot 'HyperFramesFork.ps1'
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop 'HyperFrames Fork.lnk'))
$link.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$link.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
$link.WorkingDirectory = $PSScriptRoot
$link.IconLocation = (Join-Path $PSScriptRoot 'app.ico')
$link.Description = 'HyperFrames Studio from your fork'
$link.Save()
Write-Output "Created $($link.FullName)"
