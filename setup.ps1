<#
.SYNOPSIS
    Set up Jev Harness: create the virtual environment, install dependencies and add shortcuts.
.PARAMETER Startup
    Also start Jev Harness automatically when you sign in to Windows.
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
    (Join-Path $root 'Jev Harness.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Jev Harness.lnk')
)
if ($Startup) { $targets += Join-Path ([Environment]::GetFolderPath('Startup')) 'Jev Harness.lnk' }
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = Join-Path $venv 'Scripts\pythonw.exe'
    $lnk.Arguments = '-m jevharness'
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = Join-Path $root 'jevharness\icon.ico'
    $lnk.Description = 'Voice control with Jev'
    $lnk.Save()
    Write-Host "Shortcut: $path"
}
Write-Host "`nDone. Start 'Jev Harness' from the Start menu, then right-click its tray icon > Settings to add your TypeSafe API key."
Write-Host 'The first start downloads the Whisper model (~1.6 GB).'
