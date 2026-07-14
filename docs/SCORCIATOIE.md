# Scorciatoie da tastiera

Riferimento dei tasti attivi in EditVideo. Le scorciatoie a **tasto singolo**
dell'editor (lettere, frecce, Spazio) sono **ignorate mentre scrivi in un campo di
testo**, così non interferiscono con la digitazione; le combinazioni con
modificatore (`Ctrl`/`Cmd`) restano attive ovunque. Tutte le animazioni collegate
rispettano `prefers-reduced-motion`.

Le stesse voci dell'editor sono consultabili nell'app aprendo il **pannello
scorciatoie** con `?`.

---

## Ovunque nell'app

| Tasto | Azione |
|---|---|
| `Ctrl` + `K` / `Cmd` + `K` | Apri / chiudi la **palette dei comandi** |

## Palette dei comandi (aperta)

| Tasto | Azione |
|---|---|
| digitare | Filtra i comandi (ricerca fuzzy) |
| `↑` / `↓` | Sposta la selezione |
| `Home` / `End` | Primo / ultimo comando |
| `Invio` | Esegui il comando selezionato |
| `Esc` | Chiudi la palette |

Comandi disponibili: vai alla Dashboard, torna indietro, ricarica dati, copia link
pagina, apri scorciatoie, esci.

---

## Editor — player e timeline

Attivi quando **non** stai scrivendo in un campo di testo.

| Tasto | Azione |
|---|---|
| `Spazio` | Play / pausa |
| `I` | Inizio trim = posizione corrente |
| `O` | Fine trim = posizione corrente |
| `C` | Apri un taglio (primo `C`) / chiudilo alla posizione corrente (secondo `C`) |
| `←` / `→` | Sposta di ∓1 secondo |
| `Shift` + `←` / `→` | Sposta di ∓0,1 secondi |
| `S` | Salva le modifiche (se ci sono modifiche da salvare) |
| `?` | Apri / chiudi il pannello scorciatoie |

## Editor — sottotitoli

| Tasto | Azione |
|---|---|
| `Ctrl` + `Z` | Annulla l'ultima modifica ai sottotitoli |
| `Ctrl` + `Shift` + `Z` | Ripristina |
| `Ctrl` + `Y` | Ripristina (alternativa) |

> Mentre il cursore è **dentro** un campo di testo del sottotitolo, `Ctrl+Z` /
> `Ctrl+Y` agiscono sull'**annullamento nativo del testo**; lo storico di
> annulla/ripristina delle righe (unisci, dividi, elimina, sostituisci) risponde
> fuori dai campi e dai pulsanti della barra sottotitoli.

## Editor — barra Trova e sostituisci

| Tasto | Azione |
|---|---|
| `Invio` (campo *Trova*) | Occorrenza successiva |
| `Shift` + `Invio` (campo *Trova*) | Occorrenza precedente |
| `Invio` (campo *Sostituisci*) | Sostituisci l'occorrenza corrente |
| `Esc` | Chiudi la barra Trova/Sostituisci |

---

## Pannelli e finestre (scorciatoie, Impostazioni, palette)

| Tasto | Azione |
|---|---|
| `Esc` | Chiudi il pannello |
| `Tab` / `Shift` + `Tab` | Sposta il focus; resta **intrappolato** dentro il pannello aperto |
| `?` | Chiude anche il pannello scorciatoie (oltre ad aprirlo) |

---

Le scorciatoie dell'editor sono definite in `frontend/src/pages/Editor.tsx`; la
palette dei comandi in `frontend/src/components/CommandPalette.tsx`.
