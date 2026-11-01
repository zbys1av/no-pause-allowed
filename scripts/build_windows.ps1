[CmdletBinding()]
param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Build this package on Windows x64.' }
if ($PythonExecutable) { $Python = (Resolve-Path -LiteralPath $PythonExecutable).Path }
else {
    & uv sync --locked --extra dev --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    $Python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
}
# Reuse the checked-in tools only when both match the portable checksum manifest.
$VerifiedTools = $true
foreach ($Tool in @('ffmpeg','ffprobe')) {
    $Line = Get-Content PORTABLE-SHA256.txt | Where-Object { $_ -match "  tools/$Tool.exe$" }
    if (!$Line -or !(Test-Path "tools/$Tool.exe") -or
        (Get-FileHash "tools/$Tool.exe" -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Line.Split(' ')[0]) { $VerifiedTools = $false }
}
if (!$VerifiedTools) {
    & $Python scripts/prepare_portable.py
    if ($LASTEXITCODE -ne 0) { throw 'Verified FFmpeg preparation failed.' }
}
foreach ($Tool in @('ffmpeg', 'ffprobe')) {
    $Binary = Join-Path (Get-Location) "tools\$Tool.exe"
    if (!(Test-Path $Binary)) { throw "Missing bundled tool: $Binary" }
    & $Binary -version
    if ($LASTEXITCODE -ne 0) { throw "$Tool is not executable" }
}
$env:QT_QPA_PLATFORM = 'offscreen'
$env:SMARTCUT_FFMPEG = Join-Path (Get-Location) 'tools\ffmpeg.exe'
$env:SMARTCUT_FFPROBE = Join-Path (Get-Location) 'tools\ffprobe.exe'
& $Python -m pytest -q -s -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; package not created.' }
if (Test-Path 'dist\SmartVideoCutter') {
    $Target = (Resolve-Path -LiteralPath 'dist\SmartVideoCutter').Path
    if ($Target -ne (Join-Path (Get-Location) 'dist\SmartVideoCutter')) { throw 'Unexpected build target' }
    Remove-Item -LiteralPath $Target -Recurse -Force
}
& $Python -m PyInstaller --noconfirm --clean --windowed --onedir --name SmartVideoCutter --paths . --collect-data smartcut scripts/windows_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Copy-Item tools dist\SmartVideoCutter\tools -Recurse -Force
Copy-Item PORTABLE-README.txt,THIRD_PARTY.md dist\SmartVideoCutter -Force
# Keep Qt/Python/Numpy distribution notices alongside dynamically linked libraries.
$Notices = 'dist\SmartVideoCutter\licenses'
New-Item -ItemType Directory -Path $Notices -Force | Out-Null
& $Python scripts/collect_licenses.py $Notices
if ($LASTEXITCODE -ne 0) { throw 'Could not collect dependency notices.' }
$Zip = 'dist\SmartVideoCutter-Windows-x64-portable.zip'
Compress-Archive -Path dist\SmartVideoCutter -DestinationPath $Zip -Force
# Validate the ZIP after extraction and relocation, not just the build directory.
$Smoke = Join-Path (Get-Location) 'build\portable smoke test'
if (Test-Path $Smoke) {
    $Target = (Resolve-Path -LiteralPath $Smoke).Path
    if ($Target -ne (Join-Path (Get-Location) 'build\portable smoke test')) { throw 'Unexpected smoke target' }
    Remove-Item -LiteralPath $Target -Recurse -Force
}
Expand-Archive -Path $Zip -DestinationPath $Smoke
$Report = Join-Path $Smoke 'portable-check.json'
$Exe = Join-Path $Smoke 'SmartVideoCutter\SmartVideoCutter.exe'
$Process = Start-Process -FilePath $Exe -ArgumentList @('--portable-self-test', ('"' + $Report + '"')) -WindowStyle Hidden -PassThru
if (!$Process.WaitForExit(120000)) { $Process.Kill(); throw 'Packaged self-test timed out.' }
if ($Process.ExitCode -ne 0 -or !(Test-Path $Report)) { throw 'Packaged executable failed its offline self-test.' }
$Result = Get-Content $Report -Raw | ConvertFrom-Json
if (!$Result.ok) { throw (Get-Content $Report -Raw) }
Copy-Item $Report dist\portable-check.json
Get-FileHash $Zip -Algorithm SHA256 | ForEach-Object {
    ($_.Hash.ToLowerInvariant() + '  ' + (Split-Path $Zip -Leaf)) | Set-Content ($Zip + '.sha256') -Encoding ascii
}
Write-Host 'Verified portable Windows ZIP, checksum and self-test report are in dist/.'
