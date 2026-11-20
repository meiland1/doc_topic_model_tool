# How the code fits together

These are my notes for anyone picking up this codebase, including future me. They cover how the pieces connect and why some things are done the way they are. Function-level details (what each function expects and what it leaves behind) are in the docstrings, marked `Pre:` and `Post:` where it isn't obvious.

## The short version

It's a PySide6 desktop app. A researcher imports documents into a project folder. BERTopic finds candidate themes across the passages, and the researcher reads, codes, comments and exports. Everything runs on the researcher's machine. The embedding model is bundled with the app. Theme naming uses a private copy of Ollama that the app starts and stops itself.

## Layout

```
src/qualitative_themes/
  app.py          entry point: offline flags, --self-test, --install-model-pack, GUI
  domain/         plain value types (Project, TextCoordinates). No imports from the rest.
  importers/      PDF / DOCX / TXT -> text + page/paragraph spans
  storage/        everything that reads or writes project.sqlite
  analysis/       passages, embeddings, BERTopic, naming, refinement, overview data
  export/         codebook / coded passages / reading guide
  runtime/        installed-app concerns: resource paths, Ollama engine, model packs, health
  gui/            Qt widgets, dialogs and worker threads
    main_window.py      the window and its tabs; its methods are split into
                        project_session.py, documents_page.py, local_setup.py and tasks.py
    workers.py          the worker threads the main window starts
    read_code/          the Read & code tab, one file per area (trees, reader, coding, notes...)
    settings_form.py    keyword and naming controls shared by the Find themes and Adjust themes dialogs
packaging/        PyInstaller spec, Windows installer, macOS DMG script
scripts/          fetch bundled resources, build model packs
tests/            pytest; runs headless (QT_QPA_PLATFORM=offscreen)
```

The one rule I try to keep: **only `gui/` imports Qt.** `storage/`, `analysis/`, `export/` and `runtime/` are plain Python. That's what lets most tests run without a window, and it's why every slow operation takes `progress` and `should_cancel` callbacks instead of touching widgets.

Roughly, the dependencies go:

```
gui  ->  analysis, export, runtime  ->  storage  ->  domain
                      \-> importers
```

`analysis` does import `storage` (it saves runs), and a few `storage` modules borrow from `analysis` (settings defaults in `preferences.py` and `provenance.py`). Neither ever imports `gui`.

## Ideas that everything else depends on

**Offsets are into the stored text, always.** Each document's extracted text is saved once in `documents.canonical_text` and never changes. Every range in the database (segments, codings, comments, reading marks) is a `[start, end)` Python string index into that text. Qt's text widgets use different positions (UTF-16, rewritten line breaks), so the workspace converts at the widget boundary with `domain.text_coordinates.TextCoordinates`. If you find yourself storing a Qt cursor position, convert it first.

**Model input is not source text.** `analysis.segment.model_text()` cleans a passage for the model (removes transcript scaffolding, can drop interviewer turns). That cleaned text is only for embedding. Highlighting and coding always use the source offsets.

**Researcher work is never deleted.** Codings change `state` (active / removed / rejected / merged_duplicate) instead of being deleted. Documents get `extraction_status = 'removed'`. Themes get `status = 'merged'`. Almost every change also writes a `history_events` row. That gives an audit trail and keeps undo simple.

**Analysis runs are snapshots.** A run stores its settings, segments and per-segment model suggestions. Running a new analysis never changes an older one. What the workspace shows for a theme comes from `storage.coding.effective_assignments()`: the researcher's active codings, plus that run's suggestions unless the researcher rejected or removed the same exact range for that theme. Rejections are stored per theme, so they keep applying wherever that theme identity is reused (for example after a refinement).

**Save everything in one transaction, at the end.** Long operations do their work in memory and write in a single `connect()` block at the end (`topics.save_result`, `labels.label_run`, `refinement.apply_proposal`). Cancelling or failing before that point leaves the project exactly as it was.

**Offline by construction.** `runtime.offline.enable_offline_mode()` runs at import time in `app.py`, before any ML library loads. Model loading always passes `local_files_only=True`. The engine binds to 127.0.0.1 with proxies stripped from its environment.

## Main flows

### Startup

```
app.main()
  run_gui()
    MainWindow()              builds Hub (start screen) + project page tabs
    start_local_services()    EngineStartWorker: install bundled packs, start Ollama
    app.exec()
  finally: get_engine().stop()
```

The window is usable while the engine starts. When it's ready, `engine_started()` refreshes the model list and the start screen's setup panel.

### Creating or opening a project

`Hub` only emits signals; `MainWindow.create()` / `open()` / `open_recent()` do the work and end in `_show_project()`. That loads the saved analysis setup and workspace layout (both kept in `history_events`, see `storage/preferences.py`) and points each tab at the project. A project made with one of the 0.2 test versions is refused with a plain message (see the database section); nothing upgrades it.

### Importing

```
ImportWorker (per file) -> storage.documents.import_document()
    hash the file -> duplicate? restore? -> importers.extract_document()
    -> copy to sources/<id>.<ext> -> insert documents + document_pages
```

Each file gets its own `ImportOutcome`; one bad PDF doesn't stop the batch.

### Running an analysis

```
MainWindow.start_analysis()
  AnalysisSetupDialog            InspectionWorker previews passages as options change
  save_setup()                   remember choices even if the run fails
  AnalysisWorker.run()
    analysis.topics.analyze()
      prepare_passages()         segment each document, build cleaned model text
      validate_settings()
      fit_bertopic()             encoder -> cached embeddings -> UMAP/HDBSCAN -> keywords
                                 (zero-shot: match predefined topics, discover the rest)
      save_result()              run + segments + themes + suggestions, one transaction
    result(run_id) -> MainWindow.analysis_ready() -> Overview + Workspace reload
    optional: labels.label_run() -> naming_complete(run_id)
```

Embeddings are cached per model identity under `<project>/models/embeddings/`, so re-running with different clustering settings skips the slow part.

Theme naming runs after the analysis is already saved, so naming failing or being cancelled never loses the analysis. Naming only overwrites `generated_label` on model-created themes. Anything the researcher renamed or merged into is left alone, and that check happens again inside the final write.

### Coding in the workspace

The researcher selects text, then presses a number key, clicks the toolbar or uses Add. `code_selection_as()` (in `gui/read_code/coding_actions.py`) converts the selection to canonical offsets and pushes an `AddCodingCommand` (Ctrl+Z undoes it), which calls `storage.coding.add_coding()`. Then `reload_themes()` redraws from the database. The workspace never keeps its own copy of research decisions, so anything that changes the data just reloads.

Comments work the same way through `storage/comments.py`. Drafts autosave. Anything that leaves the current document calls `CommentsPanel.flush_pending()` first and stops if the draft can't be saved.

### Refining an analysis

```
propose_refinement()   new run, status 'proposed' (source run untouched)
review_proposal()      how proposed groups overlap old themes
apply_proposal()       researcher maps proposed themes to old identities -> 'completed'
cancel_proposal()      'cancelled', proposed themes 'discarded'
```

A fingerprint of the source run and all research decisions is stored with the proposal. If anything changed before Apply, it refuses and asks for a new proposal.

### Exports, backups, recovery

- `export/research.py` reads everything in one transaction (`build_export`), then writes to a temp file and swaps it in.
- `storage/backup.py` zips the project with a manifest of SHA-256 hashes and verifies it before publishing. Restore only ever goes into a new folder.
- `storage/recovery.py` plus `gui/recovery.py` take one verified snapshot a day when the user is idle, beside the project in `.tmt-recovery-<key>/`, and keep the last seven.

## Threads

Anything slow runs in a `gui.worker.WorkerThread` subclass. The pattern is the same everywhere:

- `run()` calls a Qt-free function with `progress=self.progress.emit` and `should_cancel=self.isInterruptionRequested`.
- Results come back through signals, which Qt delivers on the GUI thread. `run()` never touches widgets.
- `AnalysisCancelled` becomes `cancelled`; any other exception becomes `failed(str)`.

`MainWindow` allows one long task at a time. Analysis, naming, visualizations, exports and refinements all share the `_analysis_worker` slot, and `MainWindow.busy` is what every action checks before starting. Actions that need to explain why they can't start call `blocked_by_task("doing X")`, which shows the message and returns True. Cancelling only sets a flag. The worker stops at its next `check_cancel()`, and HDBSCAN in particular can't be interrupted mid-fit.

SQLite connections are short-lived (`storage.database.connect()` opens, commits or rolls back, and closes), so a worker and the GUI never share one.

## The database

One SQLite file per project, `project.sqlite`. The schema version is in `PRAGMA user_version`, currently 3. `storage/migrations.py` creates the whole schema in one step; there are no upgrade steps.

| Table | What it holds |
|---|---|
| `project` | id, name, created date |
| `documents` | original name, path of the source copy, hash, extracted text, status (ready/removed) |
| `document_pages` | page/paragraph spans inside the text |
| `analysis_runs` | settings JSON, model info JSON, status (proposed/completed/cancelled) |
| `segments` | the passages of a run (offsets + text hash) |
| `themes` | generated label, researcher label, status (active/proposed/merged/discarded) |
| `model_suggestions` | segment -> theme for one run, with the BERTopic topic id |
| `codings` | researcher decisions on exact ranges, with state |
| `notes` | memos on a document, theme or coding |
| `comments` | range-anchored comments, replies, drafts |
| `reading_marks` | which passages were read and the relevance verdict |
| `history_events` | audit log, plus a couple of saved preferences |
| `documents_fts` | full-text index for search (only if SQLite has FTS5) |

Projects from the 0.2 test versions (versions 1 and 2) were only ever test projects, so 0.3 doesn't convert them: `migrate()` raises `OLD_PROJECT_MESSAGE` and leaves the file alone, and restore refuses their backups the same way. To change the schema: edit `SCHEMA`, bump `SCHEMA_VERSION`, and decide then whether real projects exist that need an upgrade step.

## Common changes

- **New analysis option:** add a field with a default to `AnalysisSettings` (older runs still load), validate it in `validate_settings()`, use it in `analysis/topics.py`, and add the control in `gui/analysis_setup.py` (or in `gui/settings_form.py` if the Adjust themes dialog needs it too). It's saved with each run automatically.
- **New kind of history event:** write it with the same `INSERT INTO history_events` pattern, then add a readable summary branch in `storage/history.history_events()`.
- **New background task:** subclass `WorkerThread` with the usual signals, start it from `gui/tasks.py` in the `_analysis_worker` slot, and call `track_task()` so it gets the progress strip and Cancel.
- **New tab:** build it in `MainWindow.__init__`, give it a `set_project()` / `set_run()` method, and call that from `_show_project()`.

## Tests

`python -m pytest -q` from the repo root. `tests/conftest.py` runs Qt offscreen and points app data at a temp folder, so tests never touch your real recent-projects list or model store. Most analysis tests pass a fake `backend=` to `analyze()`, so they don't need model weights. `test_model_integration.py` holds the real-model tests (marker `integration`). The full BERTopic run only happens when the embedding model is in `resources/` (run `scripts/fetch_resources.py`, or point `DOC_TOPIC_MODEL_RESOURCES` at a folder that has it); the visualization one just needs BERTopic installed. Tests that fake the encoder use `fake_model_resources()` from `conftest.py`.

GitHub runs the suite on every pull request (`.github/workflows/tests.yml`).

## Packaging

See [BUILDING.md](BUILDING.md). In short: `scripts/fetch_resources.py` puts the embedding model and Ollama into `resources/`, PyInstaller bundles them, and `runtime/paths.py` finds them at run time (`sys._MEIPASS` when frozen, `<repo>/resources` from source). The naming model ships separately as a `.tmtpack` (split into parts for GitHub) and is copied into the app data folder on install.
