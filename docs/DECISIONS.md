# DECISIONS — EditVideo

Registro delle decisioni di ingegneria: per ogni concetto/pattern noto, se è
stato **APPLICATO** (dove/come, con riferimento al file) o **RIFIUTATO** (perché,
citando il mental model che impone di non farlo). Il filo conduttore è uno solo:
EditVideo è un'app **single-node**, **mono-utente**, che gira su un **Hugging
Face Space free** (2 vCPU / 16 GB, disco **effimero**). Molti pattern "enterprise"
non sono neutri: aggiungono costo operativo e superficie di guasto **senza** dare
valore su questo contesto — e proprio i mental model (Occam, YAGNI, Pareto,
Second-Order, Systems Thinking) impongono di **non** adottarli.

Legenda mental model: **KISS** (semplice), **DRY** (non ripeterti), **YAGNI**
(non ti servirà), **Occam** (la soluzione più semplice che spiega/risolve),
**Pareto** (80/20), **Second-Order** (effetti di secondo ordine), **Systems
Thinking** (il sistema nel suo intero, disco effimero incluso).

Approfondimenti collegati: sicurezza in [`SECURITY_REPORT.md`](../SECURITY_REPORT.md)
e [`THREAT_MODEL.md`](THREAT_MODEL.md); rilascio in
[`DEPLOY_STRATEGIES.md`](DEPLOY_STRATEGIES.md); scaling in
[`../SCALING_REPORT.md`](../SCALING_REPORT.md).

---

## Rilascio e testing in produzione

- **Dogfooding** — APPLICATO: l'app è usata ogni giorno dall'autore per il proprio flusso reale (30–40 video/giorno, vedi README). L'operatore è il primo utente: il feedback è immediato, non serve un canale di beta.
- **Alpha / Beta testing** — RIFIUTATO come processo formale: con **un solo utente** la distinzione alpha/beta non ha significato; il dogfooding continuo la assorbe [YAGNI].
- **Canary** — RIFIUTATO come infrastruttura: un canary vero richiede ≥2 istanze e un router che invii una % di traffico a quella nuova — impossibile su un singolo Space [Second-Order]. Approssimato dai **feature flag** (`services/flags.py`): una funzione rischiosa si accende prima solo via `FEATURE_FLAGS` e si spegne all'istante (vedi `DEPLOY_STRATEGIES.md`).
- **Blue-Green** — RIFIUTATO reale: non c'è un secondo ambiente identico né un load balancer che faccia lo swap; su HF il redeploy è un restart **in-place** con disco effimero [Occam]. Ne resta l'idea utile — punto di ripristino prima del deploy — in `docs/REGOLE-ANTI-REGRESSIONE.md`.
- **A/B testing** — RIFIUTATO: nessuna base utenti da segmentare (mono-utente), quindi nessun risultato statisticamente sensato [YAGNI].
- **Feature Flags** — APPLICATO: `services/flags.py` + env `FEATURE_FLAGS` (`"nome=1,altro=0"`), `parse_flags` pura e testabile, default sicuro (flag sconosciuto = spento).
- **Shadow (traffic mirroring)** — RIFIUTATO: duplicare il traffico su una seconda pipeline raddoppierebbe il carico CPU di whisper/ffmpeg su 2 vCPU, e comunque manca il secondo ambiente [Second-Order].
- **Chaos Engineering** — APPLICATO in forma **deterministica**: `tests/test_chaos.py` inietta guasti (ffmpeg/ffprobe che fallisce o va in timeout, disco pieno, DB locked, input troncato, whisper che solleva) e asserisce il degrado con grazia. RIFIUTATO il chaos "in produzione" (kill casuali): interromperebbe il lavoro reale dell'unico utente [Second-Order].

## Metodologie di processo

- **Agile** — APPLICATO in spirito: iterazioni piccole e frequenti, `CHANGELOG.md` a incrementi (v1.1 → v1.46). Non come cerimoniale [Pareto].
- **Scrum** — RIFIUTATO: sprint/standup/retro/ruoli non hanno senso senza un team [YAGNI].
- **Kanban** — APPLICATO leggero: lavoro a flusso; di fatto la **coda job** stessa (stati `queued → running → done/error`) è un mini-kanban del lavoro dei video.
- **XP (Extreme Programming)** — PARZIALE: sì test, refactoring continuo, CI, integrazione frequente; no pair programming (solo dev) [Pareto].
- **Lean** — APPLICATO come principio: eliminare lo spreco = KISS/YAGNI applicati al codice.
- **Waterfall** — RIFIUTATO: i requisiti non sono congelati, si scopre iterando.
- **DevOps** — APPLICATO: stesso repo per build e deploy, CI + workflow di deploy, infrastruttura come codice (`Dockerfile`/`docker-compose.yml`).
- **GitOps** — PARZIALE: `.github/workflows/deploy.yml` usa il push su `master` come trigger dell'upload allo Space (git come sorgente di verità del deploy), ma senza un reconciler tipo ArgoCD [Occam].

## Pratiche di sviluppo

- **TDD** — PARZIALE: i servizi puri (`timeline`, `captions`, `retakes`, `styles`, `resilience`, `flags`, `metrics`) sono coperti da test unitari; non è TDD dogmatico ovunque [Pareto].
- **BDD** — RIFIUTATO: nessuno stakeholder non tecnico da servire con Gherkin; sarebbe cerimonia pura [YAGNI].
- **DDD** — RIFIUTATO come metodologia pesante (bounded context/aggregati): il dominio è piccolo e chiaro. Ne resta solo il **linguaggio ubiquo** (termini "taglio", "doppione", "format", tutto in italiano) [Occam].
- **Refactoring** — APPLICATO in continuo: estrazione dei `services/`, e questa stessa passata (resilience/metrics/flags) è refactoring evolutivo.
- **Pair / Mob programming** — N.A.: singolo sviluppatore (il pairing con l'assistente AI è una forma informale dello stesso principio).
- **Rubber Duck** — APPLICATO informalmente: i commenti/docstring **in italiano** motivano il *perché* di ogni scelta non ovvia (config.py, worker.py), che è esattamente esplicitare il ragionamento.
- **Code Review** — APPLICATO: review sulle PR + gate CI (`ci.yml`); disponibile `/code-review`.
- **Static Analysis** — APPLICATO: `ruff` (backend, advisory in CI), `tsc --noEmit` e **ESLint bloccante** (`--max-warnings 0`) nel job frontend.
- **Profiling** — ON-DEMAND: la memoization dei probe nasce da un costo osservato; nessun profiler continuo in produzione [YAGNI].

## Principi di design

- **KISS** — APPLICATO, pervasivo: `resilience.py` è **un solo file** senza librerie esterne (niente tenacity); la coda è una tabella, non un broker.
- **DRY** — APPLICATO: `services/metrics.py` è condiviso da `/api/health` profondo e `/api/metrics`; `transcribe_words(model_name=…)` riusa lo stesso path per modello principale e fallback; `log_context` riusa il meccanismo a contextvars per job/video/request-id.
- **YAGNI** — APPLICATO: rifiuto esplicito di broker, microservizi, CQRS, sharding (vedi sotto).
- **SOLID** — APPLICATO dove dà valore, non dogmatico: SRP e DIP sui `services`; niente gerarchie astratte forzate.
- **Separation of Concerns** — APPLICATO: `routers/` (HTTP) vs `services/` (logica) vs `config.py` (impostazioni) vs `db.py`/`models.py` (persistenza).
- **Single Responsibility** — APPLICATO: funzioni **pure** (timeline/captions/retakes/styles) separate da quelle con I/O (ffmpeg/transcribe/worker).
- **Composition over Inheritance** — APPLICATO: `retry_call`/`CircuitBreaker` compongono comportamento tramite iniezione (`sleeper`/`clock`), niente sottoclassi.
- **Convention over Configuration** — APPLICATO: `config.py` ha default sensati per tutto; le env sono opzionali (l'app parte con zero config in dev).
- **Law of Demeter** — RISPETTATA ragionevolmente: i router parlano con i servizi, non frugano negli oggetti altrui.
- **Least Astonishment** — APPLICATO: default sicuri, health "graduato", messaggi d'errore in italiano coerenti, feature flag spenti se non nominati.

## Architetture

- **Microservices** — RIFIUTATO: su un nodo il costo operativo (rete interna, deploy multipli, osservabilità distribuita) supera ogni beneficio, e su HF free è materialmente impossibile. Un **monolite modulare** è la scelta corretta [Systems Thinking / Second-Order].
- **Monolith First** — APPLICATO: è esattamente l'architettura (monolite FastAPI modulare), pronta a essere spezzata solo *se e quando* servirà davvero.
- **Event-Driven** — RIFIUTATO come architettura (nessun broker/bus): la **coda job** (tabella `jobs` + polling con claim ottimistico) è la minima forma di asincronia che serve [Occam].
- **CQRS** — RIFIUTATO: separare modelli di lettura e scrittura per un CRUD su SQLite/Postgres è complessità pura [Occam].
- **Event Sourcing** — RIFIUTATO: non serve ricostruire lo stato dagli eventi; lo stato corrente basta. La tabella `jobs` funge già da storico leggero [YAGNI].
- **Hexagonal / Clean / Onion** — PARZIALE in spirito: `config.py` è un *port* per lo storage ("quando si vorrà migrare a S3/R2 si sostituisce questo layer, non il resto"), e i `services` sono disaccoppiati dai router; ma senza la piena stratificazione a cerchi concentrici [Pareto].
- **MVC** — PARZIALE: il flusso router → service → model vi somiglia, ma non è un MVC formale (nessun controller/view espliciti lato backend).
- **MVVM** — RIFIUTATO: React con hook e stato locale non ha bisogno del binding ViewModel formale; introdurrebbe astrazione senza guadagno [YAGNI / Least Astonishment].
- **Repository** — RIFIUTATO come layer dedicato: l'ORM SQLAlchemy **è già** un data-mapper/repository; un wrapper aggiuntivo sarebbe codice ripetuto senza valore [Occam / DRY].

## Performance e scaling

- **Caching** — APPLICATO: probe `ffprobe` in cache LRU (`services/ffmpeg.py`), media con `Cache-Control` + `ETag`/304 (`routers/videos.py`), asset buildati `immutable`, `secret.key` in memoria, modello whisper in cache di processo.
- **Lazy loading** — APPLICATO: import pesanti differiti (`faster_whisper`, retakes, silence caricati solo quando servono); frontend con `Suspense`/route lazy.
- **Eager loading** — APPLICATO dove giusto: `ensure_dirs()` all'avvio; `noload` mirato per non trascinare relazioni negli enqueue di massa.
- **Memoization** — APPLICATO: il probe è memoizzato per `(path, mtime, size)`.
- **Load Balancing** — RIFIUTATO: un nodo, niente da bilanciare (il proxy HF sta davanti ma non distribuiamo su repliche) [Occam].
- **Horizontal Scaling** — RIFIUTATO come target: SQLite + disco effimero condiviso non scalano su più nodi. La coda a **claim ottimistico** *regge* comunque più worker (portabile su Postgres): è concorrenza intra-nodo, non scaling orizzontale [Second-Order].
- **Vertical Scaling** — APPLICATO come unica leva reale: più vCPU/RAM velocizza whisper/ffmpeg; `WORKER_CONCURRENCY` regola il parallelismo del singolo nodo.
- **Sharding** — RIFIUTATO: un solo DB, volumi minuscoli; partizionare sarebbe assurdo [Occam].
- **Partitioning** — RIFIUTATO: idem, nessuna tabella grande da partizionare [YAGNI].
- **Compression** — APPLICATO: `GZipMiddleware` sulle risposte grandi; export H.264 con CRF (compressione del deliverable).

## Resilienza e osservabilità

- **Fail Fast** — APPLICATO: `run.py` esce se manca ffmpeg/ffprobe; avvio **rifiutato** con `ADMIN_PASSWORD=changeme` e `APP_ENV=prod`; `retry_call` con `retry_on` selettivo risale subito sugli errori non transitori; Pydantic rifiuta Inf/NaN.
- **Graceful Degradation** — APPLICATO: whisper degrada al modello di fallback e, in ultima istanza, esporta **senza sottotitoli** (`EXPORT_ALLOW_WITHOUT_SUBS`); analisi silenzi/doppioni proseguono su errore ("continuo"); `/api/health` resta sempre servibile.
- **Circuit Breaker** — APPLICATO: `resilience.CircuitBreaker` sul modello whisper principale (dopo N fallimenti consecutivi salta subito al fallback per un cooldown).
- **Retry** — APPLICATO: `resilience.retry_call` con **backoff esponenziale** (`JOB_MAX_RETRIES`, `JOB_RETRY_BACKOFF_SECONDS`).
- **Bulkhead** — RIFIUTATO come pattern esplicito (nessun pool isolato per tipo di job): `WORKER_CONCURRENCY` limita già la concorrenza su un nodo, l'isolamento a compartimenti non serve [YAGNI].
- **Health Checks** — APPLICATO: `/api/health` a due livelli — minimale `{"ok": true}` per l'uptime anonimo, profondo (DB/disco/coda) se autenticato e `HEALTH_DEEP` on.
- **Observability** — APPLICATO: request-id di correlazione, `/api/metrics`, health profondo, logging strutturato.
- **Logging** — APPLICATO: `logging_conf` strutturato + `log_context` (job/video/request-id nel contesto).
- **Tracing** — PARZIALE (poor-man): un **Request ID** propagato per correlare le righe di una richiesta; niente OpenTelemetry distribuito (un solo nodo) [YAGNI].
- **Monitoring** — APPLICATO leggero: `/api/metrics` (contatori aggregati con SQLAlchemy) + health per l'uptime; nessuno stack Prometheus/Grafana (sovradimensionato per single-node) [Occam].

## Sicurezza

- **Zero Trust** — PARZIALE: ogni richiesta è autenticata (token HMAC verificato stateless), nessuna fiducia implicita nell'iframe HF; non zero-trust di rete (fuori scope su single-node).
- **Defense in Depth** — APPLICATO: auth + rate limit (login per-IP e **globale**, upload per-IP, batch **globale**) + guardia sulla dimensione delle richieste + security header + validazione input.
- **Least Privilege** — APPLICATO: `require_auth` PRIMA del rate limiter (l'anonimo prende 401 senza consumare budget); `secret.key` a `0600`.
- **Threat Modeling** — APPLICATO: [`THREAT_MODEL.md`](THREAT_MODEL.md) + audit in [`SECURITY_REPORT.md`](../SECURITY_REPORT.md).
- **Penetration Testing** — APPLICATO in forma di audit "occhio da attaccante" (`SECURITY_REPORT.md` + `tests/test_security_audit.py`/`test_security_hardening.py`); non un pentest esterno formale [Pareto].
- **Security by Design** — APPLICATO: default sicuri, avvio bloccato in prod se insicuro, path traversal della SPA neutralizzato, comandi esterni sempre come lista di argomenti (mai `shell=True`).
- **Input Validation** — APPLICATO: schemi Pydantic (`allow_inf_nan=False`), tetto su `Content-Length`/numero file, tetto sul body JSON.
- **Rate Limiting** — APPLICATO: login (per-IP + globale), upload (per-IP), enqueue di massa `/api/batch` (globale, chiave costante non aggirabile via `X-Forwarded-For`).

## AI / LLM

> Nel prodotto l'unico uso di modelli è la **trascrizione ASR** con faster-whisper: **non** c'è un LLM generativo, quindi la maggior parte di questi pattern è N.A.

- **RAG** — RIFIUTATO/N.A.: nessun LLM generativo né knowledge base da recuperare. L'`initial_prompt` di whisper (`WHISPER_PROMPT`, brand/vocabolario) è una primitiva iniezione di contesto, non RAG [YAGNI].
- **Fine-Tuning** — RIFIUTATO: si usa whisper pre-addestrato; il fine-tuning non è giustificato (dati/costo). `WHISPER_PROMPT` copre l'80% del bisogno [Pareto].
- **Few-shot / Zero-shot** — N.A.: l'ASR non è prompt-shot; di fatto zero-shot (modello generico) con un hint di vocabolario.
- **CoT / ToT / Self-Consistency / Reflection** — RIFIUTATI/N.A.: nessun reasoning LLM nel runtime del prodotto.
- **Agentic Workflow** — RIFIUTATO nel prodotto: la pipeline è un DAG **deterministico** di servizi (silenzi → trascrizione → doppioni → export), non agenti [Occam]. (Lo *sviluppo* usa agenti, ma è meta, fuori dal runtime.)
- **Human in the Loop** — APPLICATO: l'operatore rivede tagli e sottotitoli nell'Editor (stato "da controllare") prima dell'export; l'automazione totale (auto-export) è **opt-in**.

## Prodotto e delivery

- **MVP** — APPLICATO: c'è stato un MVP, poi 40+ iterazioni di rifinitura (README, "dopo l'MVP…").
- **PoC / Prototype** — SUPERATI: il prodotto è in uso quotidiano, non più prototipo.
- **Design Sprint** — RIFIUTATO come cerimonia: singolo sviluppatore [YAGNI].
- **Lean Startup / Build-Measure-Learn** — APPLICATO in spirito: usa l'app → osserva la frizione → itera; `/api/metrics` rafforza ora la parte "measure".
- **Product Discovery** — INFORMALE: un solo utente = discovery diretta, senza processo dedicato.
- **CI** — APPLICATO: `.github/workflows/ci.yml` (frontend + backend + smoke E2E isolato).
- **CD (Continuous Delivery)** — APPLICATO parziale: `deploy.yml` su push `master` allinea e carica lo Space; non continuous **deployment** automatico a ogni commit (c'è il gate umano) [Second-Order].

## Mental model di fondo

- **First Principles** — APPLICATO: la coda = tabella + claim ottimistico (ragionata dal problema reale, non da "serve un broker"); lo storage dietro `config.py`.
- **Occam's Razor** — APPLICATO: la soluzione più semplice che risolve — nessuna astrazione superflua.
- **Pareto (80/20)** — APPLICATO: `initial_prompt` invece di fine-tuning; backoff semplice; le poche difese che coprono la gran parte del rischio.
- **Second-Order Thinking** — APPLICATO: microservizi/shadow/blue-green respinti per i costi di secondo ordine su HF free (deploy, RAM, disco effimero).
- **Inversion** — APPLICATO: "cosa fa fallire il sistema?" → chaos tests, recovery dei job all'avvio, fail-fast sui prerequisiti.
- **Systems Thinking** — APPLICATO: il **disco effimero** è trattato come proprietà del sistema che condiziona retention, `secret.key`, sessioni e backup — non un dettaglio locale.
