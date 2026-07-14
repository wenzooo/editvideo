<#
.SYNOPSIS
    Bump della versione unica di EditVideo.

.DESCRIPTION
    Allinea in un colpo solo la versione in:
      - backend/app/version.py   (APP_VERSION)
      - frontend/src/version.ts  (APP_VERSION)
    Opzionalmente crea il commit e il tag git corrispondenti.

    E' l'UNICO modo previsto per cambiare la versione: non modificare
    a mano i due file, altrimenti backend e frontend vanno in drift.

.PARAMETER Version
    Versione esplicita in formato X.Y.Z (es. 1.2.0). Ha priorita' su -Part.

.PARAMETER Part
    Se non passi -Version, incrementa questa parte: major | minor | patch (default: patch).

.PARAMETER Commit
    Dopo l'aggiornamento crea un commit git "vX.Y.Z".

.PARAMETER Tag
    Crea anche il tag annotato vX.Y.Z (implica -Commit).

.EXAMPLE
    .\scripts\bump-version.ps1 -Part minor
    # 1.0.0 -> 1.1.0 (solo file)

.EXAMPLE
    .\scripts\bump-version.ps1 -Version 1.2.0 -Tag
    # imposta 1.2.0, committa e tagga v1.2.0

.NOTES
    Ricorda dopo il bump: build frontend (npm run build) e redeploy HF.
    Vedi docs/REGOLE-ANTI-REGRESSIONE.md.
#>
[CmdletBinding()]
param(
    [string]$Version,
    [ValidateSet('major', 'minor', 'patch')][string]$Part = 'patch',
    [switch]$Commit,
    [switch]$Tag
)

$ErrorActionPreference = 'Stop'

# La cartella del progetto e' il parent di scripts/
$root   = Split-Path -Parent $PSScriptRoot
$pyFile = Join-Path $root 'backend\app\version.py'
$tsFile = Join-Path $root 'frontend\src\version.ts'

foreach ($f in @($pyFile, $tsFile)) {
    if (-not (Test-Path $f)) { throw "File versione non trovato: $f" }
}

# Legge la versione corrente dal file Python (fonte di verita')
$pyRaw = [System.IO.File]::ReadAllText($pyFile)
$m = [regex]::Match($pyRaw, 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
if (-not $m.Success) { throw "Impossibile leggere APP_VERSION da $pyFile" }
$current = $m.Groups[1].Value

# Calcola la nuova versione
if (-not $Version) {
    $parts = $current.Split('.')
    [int]$maj = $parts[0]; [int]$min = $parts[1]; [int]$pat = $parts[2]
    switch ($Part) {
        'major' { $maj++; $min = 0; $pat = 0 }
        'minor' { $min++; $pat = 0 }
        'patch' { $pat++ }
    }
    $Version = "$maj.$min.$pat"
}

if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Versione non valida: '$Version'. Usa il formato X.Y.Z."
}
if ($Version -eq $current) {
    throw "La nuova versione ($Version) e' identica a quella corrente."
}

# Aggiorna i due file preservando esattamente il resto del contenuto
$pyNew = [regex]::Replace($pyRaw, 'APP_VERSION\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', "APP_VERSION = `"$Version`"")
[System.IO.File]::WriteAllText($pyFile, $pyNew)

$tsRaw = [System.IO.File]::ReadAllText($tsFile)
$tsNew = [regex]::Replace($tsRaw, 'APP_VERSION\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"', "APP_VERSION = `"$Version`"")
[System.IO.File]::WriteAllText($tsFile, $tsNew)

Write-Host "Versione aggiornata: $current -> $Version" -ForegroundColor Green
Write-Host "  - $pyFile"
Write-Host "  - $tsFile"

if ($Commit -or $Tag) {
    Push-Location $root
    try {
        git add $pyFile $tsFile | Out-Null
        git commit -q -m "v$Version" | Out-Null
        Write-Host "Commit creato: v$Version" -ForegroundColor Green
        if ($Tag) {
            git tag -a "v$Version" -m "v$Version" | Out-Null
            Write-Host "Tag creato: v$Version" -ForegroundColor Green
        }
    }
    finally { Pop-Location }
}

Write-Host ""
Write-Host "Prossimi passi (vedi docs/REGOLE-ANTI-REGRESSIONE.md):" -ForegroundColor Cyan
Write-Host "  1. cd frontend; npm run build   (rigenera dist con la nuova versione)"
Write-Host "  2. redeploy su Hugging Face Space brushk/editvideo"
