<#
.SYNOPSIS
    Build Fing.exe (dist\Fing\Fing.exe) with PyInstaller and point the Fing shortcuts at it.
.DESCRIPTION
    Run setup.ps1 first. The build is a folder (about 1.5 GB, mostly the CUDA libraries); the shortcuts in the
    project folder, the Start menu and (if Start with Windows is on) Startup are switched from pythonw.exe to it,
    so Task Manager, the taskbar and the firewall show Fing rather than Python.
.EXAMPLE
    .\build.ps1
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run .\setup.ps1 first.' }

$running = Get-Process -Name 'Fing' -ErrorAction SilentlyContinue
if ($running) { throw 'Fing.exe is running: quit it from the tray icon first.' }

Write-Host 'Installing PyInstaller...'
& $python -m pip install pyinstaller==6.22.3 --quiet
Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $root 'dist') --workpath (Join-Path $root 'build') `
        (Join-Path $root 'packaging\fing.spec')
    if ($LASTEXITCODE) { throw "PyInstaller failed ($LASTEXITCODE)" }
} finally { Pop-Location }
$exe = Join-Path $root 'dist\Fing\Fing.exe'

$shell = New-Object -ComObject WScript.Shell
$targets = @((Join-Path $root 'Fing.lnk'), (Join-Path ([Environment]::GetFolderPath('Programs')) 'Fing.lnk'))
$startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Fing.lnk'
if (Test-Path $startup) { $targets += $startup }
foreach ($path in $targets) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $exe
    $lnk.Arguments = ''
    $lnk.WorkingDirectory = Split-Path $exe
    $lnk.IconLocation = $exe
    $lnk.Description = 'Fing: your desktop assistant'
    $lnk.Save()
    Write-Host "Shortcut: $path"
}
Write-Host "`nBuilt $exe. Start 'Fing' from the Start menu as before."
