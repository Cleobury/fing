<#
.SYNOPSIS
    Set up Fing: create the virtual environment, install dependencies and add shortcuts.
.PARAMETER Startup
    Also start Fing automatically when you sign in to Windows.
.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -Startup
#>
param([switch]$Startup)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv'

if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    Write-Host 'Creating virtual environment (Python 3.14)...'
    py -3.14 -m venv $venv
}
Write-Host 'Installing dependencies...'
& (Join-Path $venv 'Scripts\python.exe') -m pip install --upgrade pip --quiet
& (Join-Path $venv 'Scripts\python.exe') -m pip install -r (Join-Path $root 'requirements.txt')

# pythonw.exe runs without a console window.
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path $root 'Fing.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Fing.lnk')
)
if ($Startup) { $targets += Join-Path ([Environment]::GetFolderPath('Startup')) 'Fing.lnk' }
# Shortcuts from before the app was renamed (Jev Harness): replaced by the ones above.
foreach ($dir in @($root, [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Startup'))) {
    $old = Join-Path $dir 'Jev Harness.lnk'
    if (Test-Path $old) {
        if ($dir -eq [Environment]::GetFolderPath('Startup')) { $targets += Join-Path $dir 'Fing.lnk' }
        Remove-Item $old
    }
}
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = Join-Path $venv 'Scripts\pythonw.exe'
    $lnk.Arguments = '-m fing'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = Join-Path $root 'fing\icon.ico'
    $lnk.Description = 'Fing: your desktop assistant'
    $lnk.Save()
    Write-Host "Shortcut: $path"
}
Write-Host "`nDone. Start 'Fing' from the Start menu, then right-click its tray icon (the hand) > Settings > TypeSafe to add your API key."
Write-Host 'The first start downloads the Whisper model (~1.6 GB).'
