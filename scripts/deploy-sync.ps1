<#
.SYNOPSIS
    Sincronizza il codice canonico dentro deploy/hf-space/ per il deploy su
    Hugging Face Space (brushk/editvideo). Elimina il drift di versione.

.DESCRIPTION
    Allinea la copia nello Space al codice CANONICO (quello nella root del repo),
    in modo IDEMPOTENTE: puoi rieseguirlo quante volte vuoi, il risultato non
    cambia. Cosa sincronizza (mirror):

      - backend/app/             -> deploy/hf-space/app/            (incl. version.py)
      - frontend/dist/           -> deploy/hf-space/frontend/dist/  (build committata)
      - backend/requirements.txt -> deploy/hf-space/requirements.txt

    Cosa NON tocca (file SPECIFICI dello Space, volutamente diversi):
      - deploy/hf-space/Dockerfile  (utente uid 1000, path HF, EMBEDDED_WORKER, WHISPER_*)
      - deploy/hf-space/README.md   (frontmatter YAML richiesto da HF Spaces)
      - deploy/hf-space/.gitignore

    Drift storico risolto: la vecchia deploy/hf-space/app/main.py aveva la
    versione HARDCODED ("0.2.0") e non importava da version.py. Il main.py
    canonico e' gia' adatto allo Space (gli header di sicurezza sono pensati
    per l'iframe di HF; i path statici arrivano dalla env FRONTEND_DIST del
    Dockerfile), quindi viene copiato tal quale: l'hardcode sparisce e la
    versione arriva da version.py. Rete di sicurezza: se il main.py
    sincronizzato contenesse ancora una versione literal, lo script applica un
    fix mirato (version=APP_VERSION + import da version.py).

    NON esegue git/commit/push, NON pubblica lo Space, NON bumpa la versione:
    stampa solo un riepilogo e il promemoria dei passi manuali di deploy.

.NOTES
    Prerequisito: frontend gia' buildato (frontend/dist). Processo completo in
    docs/REGOLE-ANTI-REGRESSIONE.md (par. 3-5).

.EXAMPLE
    .\scripts\deploy-sync.ps1
    # Allinea deploy/hf-space al codice canonico e stampa il riepilogo.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# La root del repo e' il parent di scripts/ (path robusti, niente cd).
$Root     = Split-Path -Parent $PSScriptRoot

$SrcApp   = Join-Path $Root 'backend\app'
$SrcDist  = Join-Path $Root 'frontend\dist'
$SrcReq   = Join-Path $Root 'backend\requirements.txt'
$SrcVer   = Join-Path $Root 'backend\app\version.py'

$Space    = Join-Path $Root 'deploy\hf-space'
$DstApp   = Join-Path $Space 'app'
$DstDist  = Join-Path $Space 'frontend\dist'
$DstReq   = Join-Path $Space 'requirements.txt'
$DstMain  = Join-Path $DstApp 'main.py'
$DstVer   = Join-Path $DstApp 'version.py'
$SpaceDoc = Join-Path $Space 'Dockerfile'
$SpaceRdm = Join-Path $Space 'README.md'

function Write-Step([string]$Msg) { Write-Host ''; Write-Host ">> $Msg" -ForegroundColor Cyan }
function Write-OK([string]$Msg)   { Write-Host "   OK  $Msg" -ForegroundColor Green }
function Write-Note([string]$Msg) { Write-Host "   --  $Msg" -ForegroundColor DarkGray }
function Write-Warn2([string]$Msg){ Write-Host "   !!  $Msg" -ForegroundColor Yellow }

# --- Precondizioni -----------------------------------------------------------
foreach ($p in @($SrcApp, $SrcReq, $SrcVer)) {
    if (-not (Test-Path $p)) { throw "Sorgente canonica mancante: $p" }
}
if (-not (Test-Path (Join-Path $SrcDist 'index.html'))) {
    throw "frontend/dist non buildato (manca index.html). Esegui prima:  .\scripts\tasks.ps1 -Task build"
}
if (-not (Test-Path $Space)) { throw "Cartella dello Space mancante: $Space" }

# Versione canonica (fonte di verita').
$verRaw = [System.IO.File]::ReadAllText($SrcVer)
$verM   = [regex]::Match($verRaw, 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
if (-not $verM.Success) { throw "Impossibile leggere APP_VERSION da $SrcVer" }
$AppVersion = $verM.Groups[1].Value

Write-Host ''
Write-Host "EditVideo - deploy-sync  (versione canonica: $AppVersion)" -ForegroundColor Green
Write-Host "Root repo : $Root"
Write-Host "Space dir : $Space"

$script:changed = 0

# --- Helper: mirror di una cartella (robocopy /MIR, escluso __pycache__/.pyc) -
function Invoke-Mirror([string]$Src, [string]$Dst, [string]$Label) {
    New-Item -ItemType Directory -Force -Path $Dst | Out-Null
    $roboArgs = @($Src, $Dst, '/MIR',
                  '/XD', '__pycache__', '.pytest_cache',
                  '/XF', '*.pyc',
                  '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:2', '/W:1')
    $null = & robocopy @roboArgs 2>&1
    $code = $LASTEXITCODE
    # robocopy: 0-7 = successo (0 = nessuna modifica), >=8 = errore.
    if ($code -ge 8) { throw "robocopy fallito ($Label): exit code $code" }
    $n = @(Get-ChildItem $Dst -Recurse -File | Where-Object { $_.FullName -notmatch '__pycache__' }).Count
    if ($code -eq 0) { Write-Note "$Label gia' allineato ($n file)" }
    else             { Write-OK   "$Label sincronizzato ($n file)"; $script:changed++ }
}

Write-Step '1) app/  ->  deploy/hf-space/app/'
Invoke-Mirror $SrcApp $DstApp 'app/'

Write-Step '2) frontend/dist/  ->  deploy/hf-space/frontend/dist/'
Invoke-Mirror $SrcDist $DstDist 'frontend/dist/'

Write-Step '3) requirements.txt  ->  deploy/hf-space/requirements.txt'
$reqChanged = $true
if (Test-Path $DstReq) {
    $reqChanged = ((Get-FileHash $SrcReq -Algorithm SHA256).Hash -ne (Get-FileHash $DstReq -Algorithm SHA256).Hash)
}
if ($reqChanged) { Copy-Item $SrcReq $DstReq -Force; Write-OK 'requirements.txt aggiornato'; $script:changed++ }
else             { Write-Note 'requirements.txt gia'' allineato' }

# --- 4) Versione in app/main.py: garantita da version.py, mai hardcoded ------
Write-Step '4) Verifica versione in app/main.py (niente 0.2.0 hardcoded)'
if (-not (Test-Path $DstVer))  { throw "Atteso version.py nello Space dopo il mirror: $DstVer" }
if (-not (Test-Path $DstMain)) { throw "Atteso main.py nello Space dopo il mirror: $DstMain" }

$mainRaw  = [System.IO.File]::ReadAllText($DstMain)
$literalM = [regex]::Match($mainRaw, 'version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
if ($literalM.Success) {
    # Rete di sicurezza: il main.py sincronizzato ha ancora una versione literal.
    Write-Warn2 "main.py conteneva versione hardcoded '$($literalM.Groups[1].Value)': applico fix mirato."
    $mainRaw = [regex]::Replace($mainRaw, '(version\s*=\s*)"[0-9]+\.[0-9]+\.[0-9]+"', '${1}APP_VERSION')
    if ($mainRaw -notmatch 'from\s+\.version\s+import\s+APP_VERSION') {
        $mainRaw = $mainRaw -replace '(from \.config import get_settings\r?\n)', "`$1from .version import APP_VERSION`r`n"
    }
    [System.IO.File]::WriteAllText($DstMain, $mainRaw)
    $script:changed++
}

# Verifica finale: fallisce forte se il drift NON e' risolto.
$verSpace = [regex]::Match([System.IO.File]::ReadAllText($DstVer), 'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
if (-not $verSpace.Success -or $verSpace.Groups[1].Value -ne $AppVersion) {
    throw "version.py nello Space ($($verSpace.Groups[1].Value)) non allineato alla versione canonica ($AppVersion)."
}
if ($mainRaw -match 'version\s*=\s*"[0-9]+\.[0-9]+\.[0-9]+"') {
    throw 'main.py nello Space ha ANCORA una versione hardcoded: fix non riuscito.'
}
if ($mainRaw -notmatch 'version\s*=\s*APP_VERSION') {
    throw 'main.py nello Space non usa version=APP_VERSION.'
}
if ($mainRaw -notmatch 'from\s+\.version\s+import\s+APP_VERSION') {
    throw 'main.py nello Space non importa APP_VERSION da version.py.'
}
Write-OK "app/main.py usa version=APP_VERSION; app/version.py = $AppVersion (nessun 0.2.0 hardcoded)"

# --- 5) File specifici dello Space: preservati (solo verifica presenza) ------
Write-Step '5) File specifici dello Space (preservati, NON sincronizzati)'
if (Test-Path $SpaceDoc) {
    Write-Note 'Dockerfile: preservato (uid 1000, path HF, EMBEDDED_WORKER/WHISPER_*).'
    $dfRaw = Get-Content $SpaceDoc -Raw
    foreach ($needle in @('app ./app', 'frontend/dist', 'requirements.txt')) {
        if ($dfRaw -notmatch [regex]::Escape($needle)) {
            Write-Warn2 "Dockerfile non fa COPY di '$needle': verifica il layout dello Space."
        }
    }
} else { Write-Warn2 "Dockerfile dello Space mancante: $SpaceDoc" }
if (Test-Path $SpaceRdm) { Write-Note 'README.md: preservato (frontmatter YAML richiesto da HF).' }
else { Write-Warn2 "README.md dello Space mancante: $SpaceRdm" }

# --- Riepilogo + promemoria deploy ------------------------------------------
Write-Host ''
if ($script:changed -eq 0) {
    Write-Host "Riepilogo: deploy/hf-space era gia' allineato (nessuna modifica)." -ForegroundColor Green
} else {
    Write-Host "Riepilogo: deploy/hf-space allineato alla versione $AppVersion ($script:changed elementi aggiornati)." -ForegroundColor Green
}

Write-Host ''
Write-Host 'Prossimi passi per il DEPLOY (manuali - questo script non fa git ne'' push):' -ForegroundColor Cyan
Write-Host '  1. Se hai toccato il frontend, rifai la build:  .\scripts\tasks.ps1 -Task build'
Write-Host '     poi ri-esegui questo script per riallineare deploy/hf-space.'
Write-Host '  2. Pubblica lo Space brushk/editvideo, dai file in deploy/hf-space/, in UNO dei modi:'
Write-Host '       a) git: commit e push verso il remote git dello Space HF;'
Write-Host '       b) upload manuale dei file via UI di Hugging Face (Files -> Add file -> Upload).'
Write-Host '  3. Secrets dello Space: ADMIN_PASSWORD e SECRET_KEY impostati (sessioni persistenti).'
Write-Host '  4. Post-deploy: Space "Running", login OK, /api/health riporta la versione attesa'
Write-Host "     ($AppVersion, non 0.2.0). Checklist completa: docs/REGOLE-ANTI-REGRESSIONE.md (par. 4-5)."
Write-Host ''

# Uscita pulita: qui si arriva solo se tutto e' andato bene ($ErrorActionPreference
# = 'Stop' aborta prima su qualsiasi errore). Evita che l'exit code di robocopy
# (0-7 = successo, ma non-zero) trapeli e venga scambiato per un fallimento.
exit 0
