"""Configurazione centralizzata. Tutto arriva da env / .env con default sensati.

Ogni percorso su disco passa da qui: quando si vorrà migrare a S3/R2 si
sostituisce questo layer, non il resto dell'app.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_FONT = "Arial" if os.name == "nt" else "DejaVu Sans"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- ambiente ---
    # "dev" (default: warning sui default insicuri) o "prod" (rifiuta l'avvio con
    # ADMIN_PASSWORD=changeme). Impostato a "prod" nel Dockerfile / compose.
    app_env: str = "dev"

    # --- sicurezza ---
    admin_password: str = "changeme"
    secret_key: str = ""          # se vuota: generata e persistita in data/secret.key
    session_days: int = 30
    # cookie di sessione: flag Secure (inviato solo su HTTPS). Default True (in
    # produzione l'app gira dietro HTTPS; su localhost i browser trattano comunque
    # 127.0.0.1 come contesto sicuro). La SPA usa comunque il Bearer da localStorage.
    cookie_secure: bool = True
    # rate limit del login (anti brute-force, per IP): tentativi FALLITI consentiti
    # nella finestra scorrevole prima di rispondere 429. Il successo azzera il conteggio.
    login_max_attempts: int = 10
    login_window_seconds: int = 300   # ampiezza della finestra (s), default 5 min
    # limite GLOBALE (tutti gli IP insieme) di login falliti nella finestra: chiude
    # il bypass del rate-limit ruotando X-Forwarded-File (header spoofabile), perche'
    # i tentativi confluiscono comunque in questo contatore unico. Soglia alta: un
    # uso legittimo non la tocca, un brute-force distribuito si'.
    login_global_max_attempts: int = 20
    # tetto al numero di chiavi (IP) tenute in memoria dal rate limiter: evita la
    # crescita illimitata (DoS memoria) con IP fittizi spoofati via XFF.
    rate_limit_max_keys: int = 4096

    # --- percorsi / db ---
    media_root: Path = Path("media")
    data_dir: Path = Path("data")
    database_url: str = ""        # vuoto = sqlite in data_dir
    frontend_dist: Path | None = None  # default: <repo>/frontend/dist

    # --- trascrizione (faster-whisper, CPU-only di default) ---
    # QUALITA' PRIMA DI TUTTO: 'medium' + beam 5 = molto piu' fedele in italiano
    # (soggetto che sbaglia ma si capisce, tagli/sottotitoli automatici piu' giusti).
    # Piu' lento per clip ma gira da solo. Per privilegiare la velocita': imposta
    # l'env WHISPER_MODEL=small e WHISPER_BEAM=1 (o large-v3 se hai una macchina piu' potente).
    whisper_model: str = "medium"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"
    whisper_language: str = "it"  # vuoto = auto-detect
    whisper_beam: int = 5
    whisper_cpu_threads: int = 0  # 0 = auto
    # contesto/vocabolario per orientare la trascrizione (nomi propri, brand di
    # abbigliamento, ecc.). Passato come initial_prompt a Whisper: elencare qui i
    # brand ricorrenti aiuta a trascriverli giusti. Es.:
    # WHISPER_PROMPT="Moda e streetwear. Brand: Nike, Adidas, Stone Island, Carhartt, Represent."
    whisper_prompt: str = ""

    # --- sottotitoli ---
    sub_max_chars: int = 42
    sub_max_gap: float = 0.8      # gap (s) tra parole che forza una nuova caption
    sub_font: str = _DEFAULT_FONT
    # spaziatura tra le lettere (ASS Spacing, px a PlayRes 1080x1920): un filo
    # d'aria rende il testo meno "attaccato" e piu' premium.
    sub_spacing: float = 1.5

    # --- export ---
    export_width: int = 1080
    export_height: int = 1920
    export_crf: int = 20
    export_preset: str = "veryfast"
    export_audio_bitrate: str = "160k"
    # dissolvenza video ("dip") ai TAGLI GROSSI: 0 = off (default, hard cut).
    # Es. JOIN_DIP=0.12 -> dip morbida ~0.12s ai tagli dove il buco rimosso e'
    # >= join_dip_min_gap. NON cambia la durata (sottotitoli sincronizzati).
    join_dip: float = 0.0
    join_dip_min_gap: float = 0.6   # applica la dip solo ai tagli >= questo (s)
    # push-in CONTINUO lento su tutto il video (Ken Burns): maschera i tagli e da'
    # un look commerciale/reel. Es. SMOOTH_ZOOM=0.06 -> +6% di zoom dall'inizio alla
    # fine. 0 = off. Non cambia la durata (sottotitoli sincronizzati).
    smooth_zoom: float = 0.0

    # --- zoom d'ingresso (+ suono) ---
    intro_zoom_amount: float = 0.12    # 12% di punch-in
    intro_zoom_duration: float = 0.9   # secondi (zoom in e out, curva sin)
    intro_sound: str = ""              # vuoto = whoosh incluso (app/assets/whoosh.wav)
    intro_sound_volume: float = 0.85

    # --- taglia-silenzi automatico ---
    silence_noise_db: float = -35.0
    silence_min_dur: float = 0.4    # pausa minima rilevata (s)
    silence_leave: float = 0.30     # respiro residuo lasciato per pausa (s): piu' naturale, giunture meno brusche

    # --- velocizza silenzi lunghi (invece di tagliarli): es. apertura pacchetti ---
    # Un silenzio piu' lungo di speedup_min NON viene tagliato ma accelerato di
    # speedup_factor (mantiene il visivo, comprime il tempo). Si lascia speedup_edge
    # secondi a velocita' 1 ai due lati per un ingresso/uscita morbidi.
    speedup_min: float = 2.5        # soglia (s) oltre la quale si velocizza invece di tagliare
    speedup_factor: float = 4.0     # fattore di accelerazione del tratto silenzioso
    speedup_edge: float = 0.15      # respiro a velocita' 1 ai bordi del tratto velocizzato

    # --- taglia doppioni/ripartenze ---
    retake_min_match: int = 3       # parole uguali consecutive per riconoscere la ripresa
    retake_window: float = 10.0     # la ripresa deve iniziare entro N secondi
    retake_max_cut: float = 20.0    # lunghezza massima di un taglio doppione
    # ripresa dell'INTERO discorso (il soggetto ricomincia da capo): finestra e
    # taglio molto piu' ampi + min_match piu' alto per evitare falsi positivi a distanza
    retake_min_match_full: int = 5       # parole d'incipit (con tolleranza 1) per riconoscere una ripresa piena
    retake_window_full: float = 180.0    # la ripresa piena puo' ripartire entro N secondi
    retake_max_cut_full: float = 300.0   # lunghezza massima di un taglio "full restart"

    # --- upload ---
    max_upload_mb: int = 2048   # limite per singolo file
    # numero massimo di file per singola richiesta di upload (anti-abuso)
    max_upload_files: int = 10
    # tetto sull'INTERA richiesta di upload (Content-Length, MB): rifiutata con
    # 413 PRIMA di leggere/spolare il body su disco. 0 = auto (max_upload_mb *
    # max_upload_files + margine per header multipart, cosi' una richiesta con
    # tutti i file al limite passa). Un valore esplicito deve essere >= max_upload_mb.
    max_request_mb: int = 0
    # tetto (KB) sul body delle route NON di upload (JSON piccoli: login,
    # segmenti sottotitoli, template). FastAPI bufferizza il body in RAM prima
    # della validazione: senza tetto un Content-Length enorme e' un DoS di memoria.
    max_json_body_kb: int = 1024

    # --- retention / garbage collection ---
    # Gli export sono rigenerabili (originale + stato a DB): oltre N giorni si
    # cancellano da disco. 0 = GC export disattivata. Gli ORIGINALI non vengono
    # mai toccati automaticamente (non rigenerabili).
    retention_exports_days: int = 14
    # i job terminati (done/error/canceled) piu' vecchi di N giorni vengono
    # eliminati dalla tabella jobs (la coda e' anche lo storico). 0 = mai.
    retention_jobs_days: int = 30
    # intervallo (s) tra due passate di pulizia nel worker
    retention_sweep_seconds: float = 3600.0

    # --- worker ---
    embedded_worker: bool = True
    worker_concurrency: int = 1
    worker_poll_seconds: float = 1.5

    # --- logging ---
    log_level: str = "INFO"     # DEBUG/INFO/WARNING/ERROR (da env LOG_LEVEL)

    # --- resilienza job / degradazione graziosa ---
    # tentativi automatici extra di un job fallito prima di marcarlo ERROR: un
    # fallimento transitorio (ffmpeg che sbraita, memoria, race sul disco effimero
    # di HF) viene riprovato invece di perdere il lavoro. 0 = nessun retry.
    job_max_retries: int = 2
    # attesa (s) tra un tentativo fallito e il successivo (backoff semplice, non
    # esponenziale: KISS, single-node). Da' tempo a una risorsa occupata di liberarsi.
    job_retry_backoff_seconds: float = 3.0
    # se il modello Whisper principale non si carica/fallisce, si degrada a questo
    # modello piu' leggero invece di far fallire l'intera trascrizione. Vuoto = nessun
    # fallback (la trascrizione fallisce direttamente).
    whisper_fallback_model: str = "small"
    # se la trascrizione fallisce del tutto, l'export procede SENZA sottotitoli (video
    # comunque montato/tagliato) invece di finire in ERROR: meglio un export utile
    # senza caption che nessun export.
    export_allow_without_subs: bool = True

    # --- observability ---
    # header con l'id di correlazione della richiesta (log/tracing): se il client non
    # lo manda, il server ne genera uno e lo rimanda in risposta.
    request_id_header: str = "X-Request-ID"
    # health check "profondo": oltre a {"ok": true}, verifica le dipendenze
    # (ffmpeg/ffprobe/DB). Disattivabile per alleggerire probe frequenti da un monitor.
    health_deep: bool = True
    # espone metriche di base (contatori/durate job) su endpoint dedicato.
    metrics_enabled: bool = True

    # --- performance / cache ---
    # max-age (s) del Cache-Control per i media IMMUTABILI (gli export sono
    # identificati univocamente e non cambiano): il browser puo' ricacharli a lungo.
    media_cache_max_age: int = 86400
    # dimensione della cache LRU dei probe ffprobe (durata/risoluzione per path):
    # evita di rieseguire ffprobe sullo stesso file.
    probe_cache_size: int = 256

    # --- rate limit upload (per IP) ---
    # upload consentiti per IP nella finestra scorrevole (anti-abuso, in aggiunta ai
    # limiti per-file/per-richiesta gia' presenti).
    upload_rate_max: int = 30
    upload_rate_window_seconds: int = 60

    # --- feature flags ---
    # stringa "nome=1,altro=0" interpretata da services/flags.py (gestione
    # centralizzata degli interruttori di funzionalita'). Vuota = tutti spenti.
    feature_flags: str = ""

    # ------------------------------------------------------------------
    @property
    def originals_dir(self) -> Path:
        return self.media_root / "originals"

    @property
    def thumbnails_dir(self) -> Path:
        return self.media_root / "thumbnails"

    @property
    def subs_dir(self) -> Path:
        return self.media_root / "subs"

    @property
    def exports_dir(self) -> Path:
        return self.media_root / "exports"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.originals_dir, self.thumbnails_dir,
                  self.subs_dir, self.exports_dir):
            d.mkdir(parents=True, exist_ok=True)

    def validate_runtime(self) -> list[str]:
        """Controlli difensivi sulle impostazioni critiche all'avvio.

        Ritorna una lista di avvisi leggibili (NON solleva mai): sta al chiamante
        loggarli. I valori fuori-range vengono comunque gestiti con clamp/default
        a runtime (es. WORKER_CONCURRENCY passa da max(1, ...)), quindi qui si
        avvisa soltanto invece di far crashare l'app.
        """
        w: list[str] = []
        if self.worker_concurrency < 1:
            w.append(f"WORKER_CONCURRENCY={self.worker_concurrency} non valido (<1): userò 1")
        if self.worker_poll_seconds <= 0:
            w.append(f"WORKER_POLL_SECONDS={self.worker_poll_seconds} non valido (<=0): atteso > 0")
        if self.whisper_beam < 1:
            w.append(f"WHISPER_BEAM={self.whisper_beam} non valido (<1): userò 1")
        if self.sub_max_chars < 1:
            w.append(f"SUB_MAX_CHARS={self.sub_max_chars} non valido (<1)")
        if self.silence_min_dur <= 0:
            w.append(f"SILENCE_MIN_DUR={self.silence_min_dur} non valido (<=0)")
        if self.silence_leave < 0:
            w.append(f"SILENCE_LEAVE={self.silence_leave} non valido (<0)")
        if self.silence_noise_db >= 0:
            w.append(f"SILENCE_NOISE_DB={self.silence_noise_db} sospetto (atteso negativo, es. -35)")
        if self.retake_min_match < 1:
            w.append(f"RETAKE_MIN_MATCH={self.retake_min_match} non valido (<1)")
        if self.retake_window <= 0:
            w.append(f"RETAKE_WINDOW={self.retake_window} non valido (<=0)")
        if self.retake_max_cut <= 0:
            w.append(f"RETAKE_MAX_CUT={self.retake_max_cut} non valido (<=0)")
        if self.retake_min_match_full < 1:
            w.append(f"RETAKE_MIN_MATCH_FULL={self.retake_min_match_full} non valido (<1)")
        if self.retake_window_full <= 0:
            w.append(f"RETAKE_WINDOW_FULL={self.retake_window_full} non valido (<=0)")
        if self.retake_max_cut_full <= 0:
            w.append(f"RETAKE_MAX_CUT_FULL={self.retake_max_cut_full} non valido (<=0)")
        if self.export_width <= 0 or self.export_height <= 0:
            w.append(f"EXPORT_WIDTH/HEIGHT={self.export_width}x{self.export_height} non validi")
        if not 0 <= self.export_crf <= 51:
            w.append(f"EXPORT_CRF={self.export_crf} fuori range (0-51)")
        if self.max_upload_mb <= 0:
            w.append(f"MAX_UPLOAD_MB={self.max_upload_mb} non valido (<=0)")
        if self.max_upload_files < 1:
            w.append(f"MAX_UPLOAD_FILES={self.max_upload_files} non valido (<1)")
        if self.max_json_body_kb <= 0:
            w.append(f"MAX_JSON_BODY_KB={self.max_json_body_kb} non valido (<=0): "
                     f"bloccherebbe ogni richiesta API con body")
        if self.max_request_mb and self.max_request_mb < self.max_upload_mb:
            w.append(f"MAX_REQUEST_MB={self.max_request_mb} < MAX_UPLOAD_MB="
                     f"{self.max_upload_mb}: un singolo file al limite verrebbe rifiutato")
        if self.retention_exports_days < 0:
            w.append(f"RETENTION_EXPORTS_DAYS={self.retention_exports_days} non valido (<0)")
        if self.retention_jobs_days < 0:
            w.append(f"RETENTION_JOBS_DAYS={self.retention_jobs_days} non valido (<0)")
        if self.retention_sweep_seconds <= 0:
            w.append(f"RETENTION_SWEEP_SECONDS={self.retention_sweep_seconds} non valido (<=0)")
        if self.app_env not in ("dev", "prod"):
            w.append(f"APP_ENV={self.app_env!r} sconosciuto (atteso 'dev' o 'prod')")
        if self.session_days <= 0:
            w.append(f"SESSION_DAYS={self.session_days} non valido (<=0)")
        if self.login_max_attempts < 1:
            w.append(f"LOGIN_MAX_ATTEMPTS={self.login_max_attempts} non valido (<1)")
        if self.login_window_seconds <= 0:
            w.append(f"LOGIN_WINDOW_SECONDS={self.login_window_seconds} non valido (<=0)")
        if self.job_max_retries < 0:
            w.append(f"JOB_MAX_RETRIES={self.job_max_retries} non valido (<0)")
        if self.job_retry_backoff_seconds < 0:
            w.append(f"JOB_RETRY_BACKOFF_SECONDS={self.job_retry_backoff_seconds} non valido (<0)")
        if self.media_cache_max_age < 0:
            w.append(f"MEDIA_CACHE_MAX_AGE={self.media_cache_max_age} non valido (<0)")
        if self.probe_cache_size < 1:
            w.append(f"PROBE_CACHE_SIZE={self.probe_cache_size} non valido (<1)")
        if self.upload_rate_max < 1:
            w.append(f"UPLOAD_RATE_MAX={self.upload_rate_max} non valido (<1)")
        if self.upload_rate_window_seconds <= 0:
            w.append(f"UPLOAD_RATE_WINDOW_SECONDS={self.upload_rate_window_seconds} non valido (<=0)")
        # cartelle scrivibili (best-effort: dopo ensure_dirs dovrebbero già esistere)
        for label, d in (("MEDIA_ROOT", self.media_root), ("DATA_DIR", self.data_dir)):
            try:
                if d.exists() and not os.access(d, os.W_OK):
                    w.append(f"{label}={d} non sembra scrivibile")
            except OSError:
                pass
        return w

    def public_config(self) -> dict:
        """Configurazione effettiva NON sensibile, da loggare all'avvio.
        Esclude di proposito admin_password e secret_key."""
        return {
            "app_env": self.app_env,
            "media_root": str(self.media_root),
            "data_dir": str(self.data_dir),
            "database": "custom" if self.database_url else "sqlite",
            "embedded_worker": self.embedded_worker,
            "worker_concurrency": self.worker_concurrency,
            "worker_poll_seconds": self.worker_poll_seconds,
            "whisper_model": self.whisper_model,
            "whisper_device": self.whisper_device,
            "whisper_compute": self.whisper_compute,
            "whisper_language": self.whisper_language or "auto",
            "export": f"{self.export_width}x{self.export_height} crf{self.export_crf} {self.export_preset}",
            "max_upload_mb": self.max_upload_mb,
            "max_upload_files": self.max_upload_files,
            "retention": f"exports {self.retention_exports_days}g / jobs {self.retention_jobs_days}g",
            "log_level": self.log_level,
            "login_rate_limit": f"{self.login_max_attempts}/{self.login_window_seconds}s",
            "job_max_retries": self.job_max_retries,
            "export_allow_without_subs": self.export_allow_without_subs,
            "feature_flags": self.feature_flags or "-",
        }

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"

    def resolved_frontend_dist(self) -> Path:
        if self.frontend_dist:
            return Path(self.frontend_dist)
        return Path(__file__).resolve().parents[2] / "frontend" / "dist"

    def resolved_intro_sound(self) -> Path | None:
        p = Path(self.intro_sound) if self.intro_sound else \
            Path(__file__).resolve().parent / "assets" / "whoosh.wav"
        return p if p.exists() else None

    def resolved_max_request_bytes(self) -> int:
        """Tetto in byte sull'intera richiesta di upload (check su Content-Length).
        Esplicito se MAX_REQUEST_MB > 0, altrimenti auto: spazio per
        max_upload_files file al limite max_upload_mb (l'endpoint accetta piu'
        file per richiesta) + margine per l'overhead multipart (boundary/header)."""
        if self.max_request_mb > 0:
            mb = self.max_request_mb
        else:
            mb = self.max_upload_mb * max(1, self.max_upload_files) + 64
        return mb * 1024 * 1024

    # cache in memoria della chiave di firma: evita una lettura da disco (e il
    # relativo TOCTOU) per OGNI richiesta autenticata
    _secret_cache: str = PrivateAttr(default="")

    def resolved_secret(self) -> str:
        """Chiave di firma HMAC. Precedenza: env SECRET_KEY > data/secret.key.

        Il file viene creato con permessi 0600 e scrittura atomica (tmp +
        os.link/os.replace): niente chiavi leggibili da altri utenti, niente
        letture parziali. Un file VUOTO (write interrotto, disco pieno) viene
        rigenerato invece di firmare con chiave vuota. La creazione concorrente
        (web/worker sullo stesso volume) è risolta da os.link, che è atomico e
        fallisce se il file esiste già: vince il primo, l'altro rilegge.
        """
        if self.secret_key:
            return self.secret_key
        if self._secret_cache:
            return self._secret_cache
        key_file = self.data_dir / "secret.key"
        key = ""
        try:
            key = key_file.read_text().strip()
        except OSError:
            pass
        if not key:
            new_key = secrets.token_hex(32)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = key_file.with_name(f".secret.key.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(new_key)
            try:
                if key_file.exists():
                    # file presente ma vuoto/corrotto: lo si sostituisce
                    os.replace(tmp, key_file)
                    key = new_key
                else:
                    try:
                        os.link(tmp, key_file)
                        key = new_key
                    except FileExistsError:
                        # un altro processo l'ha appena creata: si usa la sua
                        key = key_file.read_text().strip() or new_key
                    except OSError:
                        # filesystem senza hardlink: fallback (best-effort)
                        os.replace(tmp, key_file)
                        key = new_key
            finally:
                tmp.unlink(missing_ok=True)
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
        self._secret_cache = key
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
