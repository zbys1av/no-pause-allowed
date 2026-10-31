param([switch]$Installer)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Build this package on Windows x64.' }
$Python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (!(Test-Path $Python)) { throw 'Create .venv and install .[dev] first (see README).' }
foreach ($Tool in @('ffmpeg', 'ffprobe')) {
    $Binary = Join-Path (Get-Location) "tools\$Tool.exe"
    if (!(Test-Path $Binary)) { throw "Missing $Binary. Supply a trusted FFmpeg Windows build and its notices; see README." }
    & $Binary -version
    if ($LASTEXITCODE -ne 0) { throw "$Tool is not executable" }
}
if (!(Test-Path 'tools\licenses')) { throw 'Put the FFmpeg distribution licenses and source information in tools\licenses first.' }
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; package not created.' }
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name SmartVideoCutter --paths . scripts/windows_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Copy-Item tools dist\SmartVideoCutter\tools -Recurse -Force
Copy-Item README.md,THIRD_PARTY.md dist\SmartVideoCutter -Force
# Keep Qt/Python/Numpy distribution notices alongside dynamically linked libraries.
$Notices = 'dist\SmartVideoCutter\licenses'
New-Item -ItemType Directory -Path $Notices -Force | Out-Null
& $Python scripts/collect_licenses.py $Notices
if ($LASTEXITCODE -ne 0) { throw 'Could not collect dependency notices.' }
Compress-Archive -Path dist\SmartVideoCutter -DestinationPath dist\SmartVideoCutter-Windows-x64.zip -Force
if ($Installer) {
    & ISCC.exe scripts/installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }
}
Write-Host 'Built portable Windows folder and zip in dist/. Smoke-test on a clean Windows 10/11 machine before distribution.'
