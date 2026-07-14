<#
.SYNOPSIS
    Task runner di EditVideo: un solo entry-point per i comandi ricorrenti.

.DESCRIPTION
    Incapsula i comandi gia' esistenti del progetto (npm, python run.py, pytest,
    bump-version.ps1) cosi' non devi ricordarti cd, flag e percorsi. NON reinventa
    nulla e NON cambia la logica dell'app: e' solo comodita'. Il processo completo
    pre-deploy resta quello di docs/REGOLE-ANTI-REGRESSIONE.md.

    Uso:
        .\scripts\tasks.ps1 -Task <nome> [argomenti...]

    Senza -Task, o con un task sconosciuto, stampa questo aiuto.

.PARAMETER Task
    Nome del task: install | dev | build | test | verify | bump | help.

.EXAMPLE
    .\scripts\tasks.ps1 -Task build
    # Ricostruisce frontend/dist (deve finire senza errori).

.EXAMPLE
    .\scripts\tasks.ps1 -Task bump -Part minor
    # Inoltra "-Part minor" a scripts/bump-version.ps1.

.NOTES
    Param block "semplice" di proposito (niente [CmdletBinding]): cosi' i flag
    extra (es. -Part, -Version, -Tag) finiscono in $args e vengono inoltrati
    tali e quali a bump-version.ps1.
#>
param(
    [string]$Task
)

$ErrorActionPreference = 'Stop'

# Argomenti extra (tutto cio' che non e' -Task) da inoltrare, usati da 'bump'.
$Forward = $args

# --- Percorsi robusti relativi alla root del repo (parent di scripts/) -------
$Root       = Split-Path -Parent $PSScriptRoot
$Frontend   = Join-Path $Root 'frontend'
$Backend    = Join-Path $Root 'backend'
$BumpScript = Join-Path $PSScriptRoot 'bump-version.ps1'

# Interprete Python 3.11 dedicato ai test (vedi -Task test).
$Python311  = 'C:\Users\brsan\AppData\Local\Programs\Python\Python311\python.exe'

function Write-Step([string]$Msg) {
    Write-Host ''
    Write-Host ">> $Msg" -ForegroundColor Cyan
}

function Show-Help {
    Write-Host ''
    Write-Host 'EditVideo - task runner' -ForegroundColor Green
    Write-Host 'Uso: .\scripts\tasks.ps1 -Task <nome> [argomenti]'
    Write-Host ''
    Write-Host 'Task disponibili:'
    Write-Host '  install   npm install nel frontend'
    Write-Host '  dev       avvia l''app in locale (python run.py: API + worker embedded)'
    Write-Host '  build     build del frontend (npm run build -> rigenera frontend/dist)'
    Write-Host '  test      pytest backend: test_units + test_validation (Python 3.11)'
    Write-Host '  verify    build frontend + test backend (check pre-deploy)'
    Write-Host '  bump      inoltra i flag a scripts/bump-version.ps1 (-Part / -Version / -Tag)'
    Write-Host '  help      questo messaggio'
    Write-Host ''
    Write-Host 'Esempi:'
    Write-Host '  .\scripts\tasks.ps1 -Task build'
    Write-Host '  .\scripts\tasks.ps1 -Task test'
    Write-Host '  .\scripts\tasks.ps1 -Task bump -Part minor'
    Write-Host ''
    Write-Host 'Nota: ''dev'' richiede ffmpeg nel PATH. Il processo completo pre-deploy'
    Write-Host 'e'' descritto in docs/REGOLE-ANTI-REGRESSIONE.md.'
    Write-Host ''
}

function Invoke-Install {
    Write-Step "install - npm install in $Frontend"
    Push-Location $Frontend
    try {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install ha restituito exit code $LASTEXITCODE." }
    }
    finally { Pop-Location }
}

function Invoke-Build {
    Write-Step "build - npm run build in $Frontend"
    Push-Location $Frontend
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build ha restituito exit code $LASTEXITCODE." }
        Write-Host 'Build frontend OK (frontend/dist aggiornata).' -ForegroundColor Green
    }
    finally { Pop-Location }
}

function Invoke-Dev {
    Write-Step "dev - python run.py in $Root  (Ctrl+C per fermare)"
    Push-Location $Root
    try { python run.py }
    finally { Pop-Location }
}

function Invoke-Test {
    Write-Step "test - pytest (Python 3.11) in $Backend"
    if (-not (Test-Path $Python311)) {
        throw "Interprete Python 3.11 non trovato: $Python311"
    }
    # PYTHONHOME va azzerato: se e' impostato verso un'altra installazione, il
    # 3.11 non trova la propria stdlib e i test falliscono in import.
    $hadHome  = Test-Path Env:PYTHONHOME
    $prevHome = if ($hadHome) { $env:PYTHONHOME } else { $null }
    Push-Location $Backend
    try {
        Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
        & $Python311 -m pytest tests/test_units.py tests/test_validation.py
        if ($LASTEXITCODE -ne 0) { throw "pytest ha restituito exit code $LASTEXITCODE." }
        Write-Host 'Test backend OK.' -ForegroundColor Green
    }
    finally {
        Pop-Location
        if ($hadHome) { $env:PYTHONHOME = $prevHome }
        else { Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue }
    }
}

function Invoke-Verify {
    Write-Step 'verify - build frontend + test backend'
    Invoke-Build
    Invoke-Test
    Write-Host ''
    Write-Host 'verify OK: build verde e test verdi.' -ForegroundColor Green
}

function Invoke-Bump([string[]]$ExtraArgs) {
    if (-not $ExtraArgs -or $ExtraArgs.Count -eq 0) {
        Write-Host ''
        Write-Host "Il task 'bump' inoltra i flag a scripts\bump-version.ps1." -ForegroundColor Yellow
        Write-Host 'Passa almeno un argomento, es.:'
        Write-Host '  .\scripts\tasks.ps1 -Task bump -Part minor'
        Write-Host '  .\scripts\tasks.ps1 -Task bump -Version 1.2.0 -Tag'
        Write-Host '(nessun argomento: non eseguo un bump implicito)' -ForegroundColor Yellow
        return
    }
    Write-Step "bump - bump-version.ps1 $($ExtraArgs -join ' ')"
    & $BumpScript @ExtraArgs
}

# --- Dispatch ----------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($Task)) {
    Show-Help
    return
}

switch ($Task.ToLowerInvariant()) {
    'install' { Invoke-Install; break }
    'dev'     { Invoke-Dev;     break }
    'build'   { Invoke-Build;   break }
    'test'    { Invoke-Test;    break }
    'verify'  { Invoke-Verify;  break }
    'bump'    { Invoke-Bump $Forward; break }
    'help'    { Show-Help; break }
    '-h'      { Show-Help; break }
    '--help'  { Show-Help; break }
    '/?'      { Show-Help; break }
    default {
        Write-Host ''
        Write-Host "Task sconosciuto: '$Task'" -ForegroundColor Yellow
        Show-Help
        exit 2
    }
}
