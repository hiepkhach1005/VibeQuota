# Builds dist\VibeQuota.exe: one file, nothing to install.
#   .\build.ps1
Set-Location $PSScriptRoot
# exit codes, not ErrorActionPreference: Windows PowerShell treats any stderr line (pip notices) as an error

python -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller
if ($LASTEXITCODE) { exit $LASTEXITCODE }
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name VibeQuota `
    --icon "$PSScriptRoot\assets\app_icon.ico" --manifest "$PSScriptRoot\assets\VibeQuota.manifest" `
    --add-data "$PSScriptRoot\assets\*.png;assets" `
    --distpath dist --workpath build --specpath build vibequota.py
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Write-Host "Built dist\VibeQuota.exe"
