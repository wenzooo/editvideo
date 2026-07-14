# DEPLOY_STRATEGIES — EditVideo

Come si applicherebbero le strategie di rilascio progressivo (Canary, Blue-Green,
Shadow, A/B, Feature Flags) a **questo** deploy, e quali sono realistiche.
Contesto vincolante: **un solo Space** Hugging Face (single-node, 2 vCPU /
16 GB), **disco effimero**, **un solo utente**. La conclusione, motivata dai
mental model in [`DECISIONS.md`](DECISIONS.md), è che le strategie che
presuppongono **più istanze o più utenti** non sono applicabili qui — mentre i
**feature flag** sì, e sono lo strumento pratico per un rollout controllato.

Riferimenti: pipeline di deploy in [`../README.md`](../README.md) e
`.github/workflows/deploy.yml`; checklist in
[`REGOLE-ANTI-REGRESSIONE.md`](REGOLE-ANTI-REGRESSIONE.md).

---

## Il deploy reale, oggi

`deploy/hf-space/` è una **copia** del codice canonico (`backend/app` +
`frontend/dist`), riallineata dalla CI (`deploy.yml`, push su `master` → upload
allo Space). Il rilascio è quindi un **restart in-place** della singola istanza:
niente seconda istanza in parallelo, niente load balancer davanti su cui dirottare
traffico. Questo è il fatto tecnico da cui discende tutto il resto.

## Le strategie, una per una

### Canary — non reale, **approssimabile con i flag**

Un canary "vero" invia una **percentuale** di traffico a una versione nuova
tenendo il resto sulla vecchia: richiede ≥2 istanze e un router. Su un solo Space
non esiste il substrato. **Approssimazione realistica**: rilasciare il codice con
la funzione nuova **spenta**, poi accenderla via `FEATURE_FLAGS` e osservare
`/api/metrics` e i log (Request ID) prima di considerarla stabile. Il "canary"
diventa temporale (prima spento → poi acceso) invece che per quota di traffico —
appropriato per un mono-utente.

### Blue-Green — non reale

Richiede due ambienti identici ("blu" attuale, "verde" nuovo) e uno swap atomico
del traffico. Su HF free manca il secondo ambiente e il disco è effimero (lo stato
non è condivisibile tra due istanze). Ne conserviamo solo il **valore pratico**:
un **punto di ripristino** prima del deploy e la verifica della versione
sull'endpoint health dopo (vedi `REGOLE-ANTI-REGRESSIONE.md`), così un rollback è
rapido anche senza il "green" parallelo.

### Shadow (traffic mirroring) — non reale

Duplicherebbe le richieste verso una pipeline "ombra" per confrontarne l'output
senza servirlo all'utente. Qui il lavoro pesante è whisper/ffmpeg: rispecchiare il
traffico **raddoppierebbe** il carico su 2 vCPU, e comunque manca l'ambiente
ombra. Respinto per effetti di secondo ordine.

### A/B testing — non applicabile

Segmenta gli **utenti** per confrontare varianti. Con **un solo utente** non c'è
segmentazione né significatività statistica: nessun valore. (Un flag può comunque
far *provare* una variante a sé stessi, ma non è un A/B.)

### Feature Flags — **applicati, il vero abilitatore**

Sono l'unica di queste leve che si adatta nativamente a un single-node
mono-utente, e sono già in codice.

## Come i feature flag abilitano un rollout controllato (in pratica)

Implementazione: `backend/app/services/flags.py` + env `FEATURE_FLAGS`.

- **Formato**: stringa `"nome=1,altro=0,terzo"` — `nome=1|true|yes|on` acceso, `nome=0` spento, `nome` nudo acceso, nome assente = **spento** (default sicuro).
- **API**: `parse_flags(raw)` è una **funzione pura** (testabile senza env); `is_enabled("nome")` legge la config; `feature_flags()` restituisce l'intero dizionario.
- **Ambito**: runtime, letto da `get_settings()` (env/`.env`); nessuna dipendenza esterna, nessun servizio di flag.

Flusso di canary/rollout consigliato sullo Space:

1. **Rilascia spento.** Il codice della funzione nuova entra dietro `if is_enabled("nome"):`; il deploy la lascia **inerte** (flag non impostato). Rischio del rilascio ≈ zero: il comportamento resta quello noto.
2. **Accendi in modo controllato.** Imposta `FEATURE_FLAGS=nome=1` tra i **secret/variabili dello Space** e riavvia. La funzione è ora attiva solo su quella istanza, sotto il tuo controllo.
3. **Osserva.** Usa `/api/metrics` (contatori job/video, durata media, coda) e i log correlati per **Request ID** per verificare che non ci siano regressioni (errori, code che si allungano, tempi che esplodono).
4. **Consolida o spegni all'istante.** Se regge: la funzione può poi diventare comportamento di default in una release successiva (e il flag si rimuove). Se qualcosa va storto: rimetti `FEATURE_FLAGS=nome=0` e riavvia — **rollback immediato senza redeploy** del codice.

Perché questo è "abbastanza" qui: su un solo nodo con un solo utente, il rischio
da gestire non è "quale % di utenti colpisco" ma "posso spegnere subito se
sbaglio". Il flag dà esattamente quel **kill-switch**, che è l'80% del valore di
un canary a una frazione del costo [Pareto]. Le difese di resilienza collegate
(retry, circuit breaker, degradazione graziosa in `worker.py`/`resilience.py`)
completano il quadro: anche una funzione nuova che fallisce degrada invece di
buttare giù la pipeline.

## Riepilogo

| Strategia | Realistica qui | Nota |
|---|---|---|
| Feature Flags | **Sì** | `services/flags.py` + `FEATURE_FLAGS`; kill-switch e canary temporale |
| Canary | Solo approssimata | via flag (temporale, non per quota di traffico) |
| Blue-Green | No | ne resta il punto di ripristino + verifica versione |
| Shadow | No | raddoppierebbe il carico whisper/ffmpeg |
| A/B testing | No | mono-utente: nessuna segmentazione |

La via realistica per lo Space è: **rilascio spento → accensione via flag →
osservazione con metrics/log → consolidamento o kill-switch**. Le altre strategie
sono giuste in flotte multi-istanza; imporle qui violerebbe Occam/YAGNI/Second-Order.
