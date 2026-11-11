# A small window listing every feature of this HyperFrames setup in a dropdown. Picking one shows what it
# does and what to ask Claude; "Copy request" puts that request on the clipboard. The list is features.json.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$here = $PSScriptRoot
$data = Get-Content -LiteralPath (Join-Path $here 'features.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$features = @($data.features)

function Resolve-FeaturePath([string]$path) {
    if (-not $path) { return $null }
    $path = $path.Replace('%DESKTOP%', [Environment]::GetFolderPath('Desktop'))
    if ($path.StartsWith('~')) { $path = $env:USERPROFILE + $path.Substring(1) }
    return $path
}

$font = New-Object System.Drawing.Font('Segoe UI', 10)
$form = New-Object System.Windows.Forms.Form -Property @{
    Text = 'HyperFrames features'; Width = 640; Height = 470; StartPosition = 'CenterScreen'
    Font = $font; MinimumSize = New-Object System.Drawing.Size(520, 400); BackColor = [System.Drawing.Color]::White
}

$label = New-Object System.Windows.Forms.Label -Property @{
    Text = 'Choose a feature:'; Left = 16; Top = 16; AutoSize = $true
}
$groupBox = New-Object System.Windows.Forms.ComboBox -Property @{
    Left = 16; Top = 42; Width = 200; DropDownStyle = 'DropDownList'
}
$combo = New-Object System.Windows.Forms.ComboBox -Property @{
    Left = 224; Top = 42; Width = 384; DropDownStyle = 'DropDownList'; MaxDropDownItems = 20
    Anchor = 'Top, Left, Right'
}
$details = New-Object System.Windows.Forms.RichTextBox -Property @{
    Left = 16; Top = 84; Width = 592; Height = 270; ReadOnly = $true; BorderStyle = 'None'
    BackColor = [System.Drawing.Color]::FromArgb(246, 247, 249); Anchor = 'Top, Bottom, Left, Right'
}
$copy = New-Object System.Windows.Forms.Button -Property @{
    Text = 'Copy request for Claude'; Left = 16; Top = 368; Width = 200; Height = 34; Anchor = 'Bottom, Left'
}
$open = New-Object System.Windows.Forms.Button -Property @{
    Text = 'Open'; Left = 224; Top = 368; Width = 100; Height = 34; Anchor = 'Bottom, Left'; Visible = $false
}
$status = New-Object System.Windows.Forms.Label -Property @{
    Left = 336; Top = 377; AutoSize = $true; ForeColor = [System.Drawing.Color]::SeaGreen; Anchor = 'Bottom, Left'
}

$allGroups = 'All features'
$groupBox.Items.Add($allGroups) | Out-Null
$features | ForEach-Object { $_.group } | Select-Object -Unique | ForEach-Object { $groupBox.Items.Add($_) | Out-Null }

function Fill-Features {
    $combo.Items.Clear()
    $chosen = $groupBox.SelectedItem
    foreach ($f in $features) {
        if ($chosen -ne $allGroups -and $f.group -ne $chosen) { continue }
        $text = if ($chosen -eq $allGroups) { "$($f.group): $($f.name)" } else { $f.name }
        $combo.Items.Add([pscustomobject]@{ Text = $text; Feature = $f }) | Out-Null
    }
    $combo.DisplayMember = 'Text'
    if ($combo.Items.Count) { $combo.SelectedIndex = 0 }
}

function Show-Feature {
    $f = $combo.SelectedItem.Feature
    $details.Clear()
    $details.SelectionFont = New-Object System.Drawing.Font('Segoe UI Semibold', 13)
    $details.AppendText("$($f.name)`n")
    $details.SelectionFont = New-Object System.Drawing.Font('Segoe UI', 9)
    $details.SelectionColor = [System.Drawing.Color]::Gray
    $details.AppendText("$($f.group)`n`n")
    $details.SelectionFont = $font
    $details.SelectionColor = [System.Drawing.Color]::Black
    foreach ($b in $f.bullets) { $details.AppendText([char]0x2022 + "  $b`n") }
    $details.AppendText("`n")
    $details.SelectionFont = New-Object System.Drawing.Font('Segoe UI Semibold', 10)
    $details.AppendText("Ask Claude:`n")
    $details.SelectionFont = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Italic)
    $details.AppendText([char]0x201C + $f.ask + [char]0x201D)
    $open.Visible = [bool]$f.open
    $status.Text = ''
}

$groupBox.Add_SelectedIndexChanged({ Fill-Features })
$combo.Add_SelectedIndexChanged({ Show-Feature })
$copy.Add_Click({
    [System.Windows.Forms.Clipboard]::SetText($combo.SelectedItem.Feature.ask)
    $status.Text = 'Copied - paste it into Claude'
})
$open.Add_Click({
    $path = Resolve-FeaturePath $combo.SelectedItem.Feature.open
    if ($path -and (Test-Path -LiteralPath $path)) { Start-Process -FilePath $path }
    else { $status.ForeColor = [System.Drawing.Color]::Firebrick; $status.Text = "Not found: $path" }
})

$form.Controls.AddRange(@($label, $groupBox, $combo, $details, $copy, $open, $status))
$groupBox.SelectedIndex = 0
[System.Windows.Forms.Application]::Run($form)
