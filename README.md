# Qualitative Themes

Offline desktop workspace for qualitative document coding and candidate theme discovery. This repository is independent of the transcription application. Milestone 10 adds persistent analysis names, paged Analysis History, codebook CSV/JSON exports, and coded-passage CSV exports. Reviewed refinement and transactional Apply/Cancel are included. Analysis Setup, local naming, and saved-run controls remain in Research Workspace.

## Run

Use Python 3.12 on Windows, macOS, or Linux:

```bash
python -m venv .venv
# Activate your virtual environment
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e '.[test]'
doc-topic-model-tool
```

Create Project asks for an empty folder and project name. Open Project selects the folder containing `project.sqlite`. Choose Import Documents to add multiple PDF, DOCX, or TXT files and preview extracted text. Each project keeps immutable source copies in `sources/` and optional saved model files in `models/`. Files with identical source bytes are reported as duplicates.

PDF import handles selectable text and stores page-to-text spans; scanned PDFs need OCR before import. PDF reading order can differ from page layout. DOCX import reads body paragraphs and top-level tables, but not headers, footers, text boxes, or image text. TXT is decoded as UTF-8/UTF-16 when indicated, with Windows-1252 as a fallback and a visible warning. Review the preview before analysis. Exact displayed canonical text is stored without whitespace normalization.

Choose Short, Standard, Long, Paragraphs, or Whole document and click **Preview Passages**. Select a passage to see its exact extracted text and `[start, end)` offsets. Passage runs are saved independently; repeating the preview does not erase older runs. Short targets roughly 100 words (up to 125), Standard 200 (up to 250), and Long 375 (up to 450), subject to sentence boundaries. Very long sentences are split on word boundaries. No passages overlap. The displayed passage preview is exact; a normalized model-input string is derived separately. 

## Tests

```bash
pytest -q
```

After updating from Milestone 1, rerun `pip install -e '.[test]'` in the activated environment to install PyMuPDF and python-docx. Dependencies are bounded but not locked yet. Pin a tested Windows environment before packaging or distributing the application.

## Research Workspace

Open a project, import documents, then select **Research Workspace**. Create a theme, select text in the center document, and click **Add selected text to chosen theme**. The right panel lists every coded occurrence of that theme across documents. Click an occurrence or use Previous/Next to switch documents and jump to its text. Click an existing highlight to select its occurrence. Researcher codings are saved and reappear when you reopen the project. The same text range can have more than one theme; choose a second theme and add it again.

The workspace displays a normalized *view* of CRLF and PDF page separators while retaining exact offsets into the immutable imported text. Python source characters and Qt UTF-16 cursor positions are mapped explicitly, including emoji. If Qt unexpectedly changes the viewer text, coding is disabled for that document rather than saving a wrong range. Select a theme to rename it or merge it into another theme. A merge moves codings and theme notes, preserves passage notes, and collapses duplicate assignments to the same exact range. Select a coded occurrence to remove that assignment; other theme assignments remain. Research notes can target a selected passage, theme, or document. Select a saved note and click Update selected to edit it. The selected passage shows all its manually assigned themes. Operations are recorded in the history table. Model suggestions appear with provenance labels. Accept selected suggestion preserves a passage as researcher coding; removing it records an explicit rejection. A passage note on a suggestion accepts that coding before attaching the note.

Manual GUI check: create two themes and code the same passage under both. Rename a theme, create and edit a passage note, add theme and document notes, remove one coding, and merge themes. Reopen the project to verify the remaining highlights and notes. Also try text with CRLF and emoji. The automated tests cover mapping and persistence, but the GUI must be checked on a machine with PySide6.

## Milestone 6: Analyze documents together

Update source files in your existing application folder; keep your `.venv` and project data folders. In the activated environment, install the analysis extra:

```powershell
python -m pip install -e ".[analysis,test]"
pytest -q
```

Open a project and use **Download model (one-time setup)** while online. This explicitly downloads a pinned revision of `sentence-transformers/all-MiniLM-L6-v2` into that project's `models/weights/` folder. The Python packages also need a one-time installation. **Analyze** loads weights with `local_files_only=True`; ordinary analysis does not download models. Model weights are kept outside SQLite. Other projects currently need their own weights setup.

Import the three synthetic TXT files under `examples/smoke_corpus/`. Select those three documents with Ctrl/Shift, choose **Paragraphs**, and click **Analyze selected documents**. All 45 passages are fitted together in one model; Preview Passages is optional. With your own sources, select multiple documents or use Short passages. The default clustering requires at least 10 passages; a small corpus gets a message with options. The synthetic corpus tests the integration, not the scientific validity of theme discovery. The clustering is free to produce different numbers of themes and outliers; three topics are not forced.

Analysis opens Research Workspace with the completed run selected. Choose a generated theme and navigate its exact source passages. **Uncategorized** contains topic -1 outliers. Run selection lets you revisit earlier analyses or use Manual coding only. Each numeric BERTopic ID maps to a fresh application theme UUID for that run; no cross-run identity is inferred from matching numbers. Earlier labels, merges, notes, rejections, and researcher codings remain saved. Themes with accepted/manual codings remain visible alongside a newer analysis. Refine Analysis now offers reviewed identity continuity based on actual shared source passages; it does not infer identity from similar names or topic numbers.

**Accept selected suggestion** retains that passage as researcher coding across runs. **Remove selected coding** rejects a suggestion or removes a researcher coding; the same run's suggestion stays suppressed for that theme and exact range. Adding it manually again restores the researcher assignment. New runs create new theme identities; an old rejection is not treated as a rejection of every semantically similar future theme. Notes on a suggestion explicitly accept its coding and attach to that researcher record.

Progress and Cancel are available in Documents & Passages. Cancellation is checked between embedding batches and before saving. UMAP/HDBSCAN fitting and a weights download cannot be interrupted mid-call; cancellation or window closing waits for that stage while keeping the Qt event loop alive. Complete results become visible in one transaction. Failed/cancelled computation leaves the previous run intact; already computed embedding cache files may remain for reuse.

The initial backend runs on CPU with embedding batches of 16 and deterministic UMAP seed 42. `AnalysisSettings` is the internal configuration for embedding identity/revision, seed, and minimum topic size. It uses UMAP, HDBSCAN, English CountVectorizer keywords, and BERTopic. English MiniLM has a token limit: long passages may be truncated for embeddings, while displayed source text and offsets remain complete. Prefer Short or Standard passages for a first trial. Timing, peak memory, GPU acceleration, and a tested Windows dependency lock have not been measured/validated here.

Embeddings are cached in `models/embeddings/`, keyed by pinned model identity, sentence-transformers version, token limit, and normalized passage text hash. Run JSON records parameters, package versions, selected document IDs, keywords, representative segment UUIDs, and original topic-to-theme mapping. Suggestions store their run, segment, numeric topic ID, theme UUID, and score. Scores are clustering membership strengths, not validated confidence in a qualitative interpretation. Merges update current suggestion associations; the original run mapping remains provenance. No fitted BERTopic object is serialized in this milestone; fresh discovery reuses cached embeddings; saved-group refresh/reduction reconstructs keyword representations without an embedding step.

### Optional real-model integration test

The normal suite uses fake outputs and needs no model weights. It includes offscreen Qt checks for generated highlights and notes. One real-model test is skipped until you explicitly point it at downloaded weights:

```powershell
$env:DOC_TOPIC_MODEL_WEIGHTS = "C:\path\to\test_docs\models\weights"
pytest -q -m integration -s
Remove-Item Env:DOC_TOPIC_MODEL_WEIGHTS
```

This test copies the local weights cache to a temporary project and fits the synthetic corpus without downloading. After the automated run, manually check generated highlights, accept/reject a suggestion, leave a note, rename a generated theme, reopen the project, and run a second analysis. Verify earlier work remains accessible and accepted codings remain visible. Ollama labeling is now available as described below. Keyword names remain the default when automatic naming is disabled.

### Milestone 6 Fix 1: Windows SQLite cleanup

The connection helper now explicitly closes every SQLite connection when its scope ends, after commit or rollback. This fixes WinError 32 during test cleanup and also prevents open database handles in normal application use. No project schema or stored data changes are needed. Update `src/qualitative_themes/storage/database.py` and the tests from the corrected bundle, then rerun `pytest -q`. Three added lifecycle regressions check successful closure, rollback and closure on exceptions, and closure after schema validation failure.

## Milestone 7: Optional local theme naming

Update `src/`, `tests/`, README and project state from the bundle, preserving your virtual environment and project folders. The existing analysis extra already includes httpx; rerun installation to ensure it is available:

```powershell
python -m pip install -e ".[analysis,test]"
pytest -q
```

Start your local Ollama service with at least one installed text-generation model. In **Research Workspace**, open **Naming options**, click **Refresh installed models**, and choose a model from the local list. The Analysis Setup dialog also offers a local-model selector under Advanced. This application lists locally installed models rather than downloading Ollama models. Its endpoint is fixed to `http://127.0.0.1:11434`; HTTP proxy environment variables and redirects are disabled. Remote/cloud-tagged models are excluded from the selector.

To label your existing saved analysis, select it in Research Workspace and click **Preview new theme names** beside the saved-run selector. Prepare and review the proposal, then Apply or Cancel; this does not rerun discovery. For future analyses, click Analyze selected documents and enable **Automatically name themes** in Analysis Setup. Select the Ollama model under Advanced. Setup choices now persist per project; the model identity used for each naming attempt is also saved with the run.

Naming progress appears in Research Workspace and the Documents & Passages status line. For a saved-run naming preview, the review shows proposed labels before Apply. Initial analyses with automatic naming show completed labels directly. Enable **Theme naming details** to see each result: Ollama label, keyword fallback with failure reason, researcher decision preserved, or Uncategorized retained. These details and labels remain available after reopening. If no local model is selected, the service is unavailable, a request times out, or the model returns invalid JSON/labels, the complete BERTopic analysis stays saved and affected themes use their keyword labels. Renamed and merged themes are protected, including edits made during a labeling request. The generated-label field is updated while the user-label field is never overwritten.

The request uses a JSON schema, low temperature (0.1), seed 42, and bounded output. Only up to 10 keywords and three representative excerpts of up to 800 characters from that run/topic are sent to the local API. Document paths, unrelated files, and the full project are not sent. Labels are validated as short single-line text (1–80 characters, at most 12 words); optional descriptions are limited to 400 characters. Descriptions are stored in theme/run metadata; the current workspace displays labels and labeling status. Topic -1 retains Uncategorized. A labeling attempt records model name/digest/details, template version, parameters, per-theme suggestions, protection states, and failures in run JSON and history.

Cancellation is cooperative between requests. Each local request has a 3-second connection timeout and 90-second read timeout; an in-progress request may need to finish or time out before cancellation/close completes. A cancelled attempt leaves prior labels intact; any completed BERTopic run remains saved. Relabeling is committed atomically and preserves codings, notes and assignments. A failed regeneration proposes keyword fallback for unprotected themes; accepted names change only when you Apply.

Manual acceptance check:

1. Refresh local models, select an installed model, and name the saved synthetic-corpus analysis.
2. Inspect labels and Theme naming details; verify all passage highlights/navigation still work.
3. Rename a generated theme yourself, then name that analysis again. Confirm your name remains unchanged.
4. Reopen the project and check labels, details, and notes persist.
5. Stop Ollama and try naming again. Verify clear fallback reasons, readable keyword labels on unprotected themes, and the same saved analysis/codings/notes.

Eight weight-free unit tests use mocked HTTP responses. Real Ollama labeling and the updated Qt controls still require your local check. Prompts 8 and 9 are implemented below.

## Milestone 8: Analysis Setup and presets

Select documents in Documents & Passages and click **Analyze selected documents**. Analysis Setup lists the selected sources and offers:

- **Analysis style:** Balanced, Broad, Detailed, or Exploratory.
- **Analysis unit:** Short, Standard, Long, Paragraphs, or Whole document, with context/workload explanations.
- **Number of themes:** Let model decide, or Aim for approximately N.
- **Automatically name themes:** optional local Ollama naming after discovery.

The dialog counts passages in a background thread, displays passage/word/unique-text counts, and disables Analyze when settings cannot be supported by the corpus. Shorter units or smaller-group styles may make a small corpus usable. It provides no unmeasured time estimate. Cancel waits safely for an active setup check; ongoing analysis progress and cancellation remain available in both tabs.

Presets map to one explicit configuration table:

| Style | Minimum group evidence | HDBSCAN min_samples | UMAP neighbors | Selection |
| --- | ---: | ---: | ---: | --- |
| Balanced | 5 passages | 2 | 15 | eom |
| Broad | 10 passages | 3 | 25 | eom |
| Detailed | 3 passages | 1 | 10 | eom |
| Exploratory | 2 passages | 1 | 5 | leaf |

These presets express discovery preferences, not guarantees about output counts. UMAP neighbors are capped at passage count minus one; its dimensions are capped at five or passage count minus two. The application requires at least max(3, minimum evidence) passages and displays a non-blocking small-corpus warning when appropriate. The deterministic seed remains 42. Actual effective parameters are recorded with each run.

**Aim for approximately N** is a reduction goal excluding Uncategorized. BERTopic first discovers themes. If discovery yields more than N, it reduces them toward the goal; if discovery yields fewer, the application keeps that result. It cannot generate missing themes by increasing the reduction target. Use Detailed or Exploratory and launch a new discovery run to seek smaller groups. The completion message reports the actual discovered theme count and the number of Uncategorized passages.

Under **Advanced**, adjust minimum evidence, words to ignore, word versus word/two-word keywords, and the installed local Ollama model. Ignored words are normalized, deduplicated single tokens and added to English stop words for keyword extraction. They do not remove words from immutable source text or from embedding inputs. If every usable keyword is filtered out, analysis fails with a helpful message before loading weights. **Restore Recommended Defaults** resets Balanced, Standard passages, automatic theme count, no extra ignored words, words/two-word phrases, and optional naming off.

User choices are saved project-locally in existing history events when Analyze is confirmed, even if a later model computation fails. Each completed run stores the chosen style, overrides, unit, theme goal, naming/model selection and effective algorithm settings. Reopening the project restores the last confirmed setup; older projects use their latest run or recommended defaults. No schema migration is required.

Saved-run naming no longer requires switching tabs: choose the run, choose a model under Naming options, click Name themes in selected analysis, follow progress, and cancel if needed, all in Research Workspace. Choosing a model there also supplies it to the next Analysis Setup dialog.

Manual acceptance check:

1. Use the three synthetic documents, open Analysis Setup, choose Balanced and Paragraphs, and analyze. Confirm the actual theme count appears.
2. Run Detailed with an approximate goal below the first run's discovered count; inspect the result rather than expecting an exact count.
3. Choose Whole document for only three selected sources: confirm Analyze is disabled with a helpful explanation. Return to Paragraphs.
4. Try ignored words and Words only, then inspect saved settings and keyword labels. Restore Recommended Defaults and check the controls reset.
5. Choose optional Ollama naming and verify names, navigation, notes and previous analysis runs remain available.
6. Reopen the project and check last confirmed setup choices persist. Name an existing run entirely from Research Workspace.

Six new configuration/storage/reduction tests run without model weights. Two additional offscreen Qt tests check setup validation and the placement of saved-run naming controls. Qt and actual preset-driven BERTopic runs require your Windows check. Prompt 9 now implements reviewed Refine Analysis while preserving researcher decisions.


## Milestone 8 scope fix
Saved analysis views show manual themes and coding counts only for that run’s selected documents. The document chooser uses the same corpus. Manual coding only retains project-wide access to all saved themes, codings, and notes. Newly created empty themes stay visible during the current coding session; switching analyses clears this temporary visibility. No schema migration or data deletion.
Validation: 26 repository/settings/labeling/database tests passed locally; Python syntax checked. Added an offscreen Qt regression for scope switching and empty-theme creation; PySide6 is unavailable here, so run the full pytest suite on Windows.


## Milestone 8 manual-only list fix
Manual coding only hides generated themes with no active researcher codings. Manually created themes remain available even when empty. Generated themes appear in this view after accepting a passage or adding a manual coding; renaming alone does not accept model suggestions. Saved analysis views retain generated themes. All data and labels are preserved. User verified the full suite for the preceding corpus-scope fix. This update passed 27 locally runnable tests; the added Qt regression awaits Windows execution.


## Milestone 9 — Refine Analysis
In Research Workspace, select a saved analysis and click **Refine Analysis…**. Choose regenerated names, refreshed words, fewer themes, or fresh discovery. Read the operation explanation, prepare a proposal, inspect the evidence and identity choices, acknowledge review, then Apply or Cancel. **Preview new theme names** uses the same proposal review.

Names and keyword-only refresh keep saved groups; keyword refresh retains existing names unless naming was requested. Fewer themes combines saved groups by BERTopic keyword similarity. Fresh discovery opens Analysis Setup, can change passage size/settings, reuses compatible cached embeddings, and does not promise a larger or exact theme count.

Identical source-passage groups recommend the earlier theme identity. For a combined or split group, explicitly choose whether to reuse one earlier identity. Reusing identity preserves its researcher name, notes, accepted/manual codings, exact-range rejections, and merge history. Earlier identities can be selected once; all earlier analyses and decisions remain saved. Names on reused machine themes change globally after Apply, while researcher names stay protected. Different passage sizes do not automatically relocate old rejection ranges. Independent **Analyze selected documents** remains a separate analysis; use Refine Analysis when you want reviewed continuity.

Cancel leaves the selected analysis and research decisions unchanged. Saved proposals are hidden from normal analysis/theme lists and can be reopened with **Review pending refinement…**. If research decisions changed after a proposal was prepared, Cancel and prepare a new proposal.

See `MILESTONE9_CHECKLIST.md` for installation and Windows checks. No new dependencies or database migration are needed.


## Milestone 9 Fix 1: Selected-document passage previews
Preview Passages now snapshots the selected document IDs instead of segmenting every imported document. It opens the first passage for the currently viewed selected document, preserves multi-document selection during passage navigation, and clears stale preview rows/excerpts when the document selection changes. No selection prompts the user to choose documents. Document and passage-size controls are disabled during preview computation, then restored. The long-document warning checks only the selected corpus. Existing analyses and coding are unchanged.
Validation: 48 Qt-free tests passed locally, including repeat-selection and selected-corpus regressions. Added offscreen Qt checks for alternating documents, clearing old previews, multi-selection focus/navigation, and no selection. The prior Milestone 9 full suite was reported passed by the user; its manual refinement workflow check is still pending. PySide6 is unavailable in this build environment, so the new GUI tests require Windows execution.


## Milestone 9 Fix 2: Document management, preview all, and Windows fonts
- **Remove selected documents** hides the chosen documents from Documents & Passages and excludes them from future previews/discovery. A confirmation lists the documents and explains preservation. Removal is reversible: source copies, canonical text, saved analyses, themes, codings, notes, and history remain stored.
- **Restore removed documents…** returns one or several documents to the import list. Reimporting the same file also restores its original document identity instead of making a duplicate. Research Workspace retains access to removed documents in saved analyses and manual coding, with a `[removed]` marker.
- **Preview all documents** previews every available imported document with the chosen passage size, even if only one or none is selected. It opens the currently viewed document first. **Preview Passages** continues to use only the selection.
- Fresh discovery on a saved corpus containing removed documents asks you to restore them or analyze a new selection. Keyword/name refinements can still use the saved corpus. Removing/restoring documents invalidates an already prepared proposal, so prepare it again before Apply.
- Startup explicitly chooses an installed scalable interface font, preferring Segoe UI/Arial on Windows. This addresses the legacy-font selection implicated by the reported DirectWrite/8514oem warning; disappearance of the Windows warning is not verified in this environment. No font files or new dependencies are downloaded.

Close the app and replace `src` and `tests` from this bundle, preserving `.venv` and your project/data folders. Run `pytest -q`. Try removing a test document, checking its saved analysis, restoring it, and comparing selected/all previews. No database migration is required.


## Milestone 9 Fix 3: Five-passage analysis
Removed the arbitrary six-passage/two-full-groups cutoff. Shared setup/backend validation requires at least max(3, minimum evidence) passages, while smaller corpora receive a non-blocking warning. Minimum evidence 2 with five passages can run using Balanced, Short passages, automatic naming, and word/bigram keywords. UMAP uses bounded neighbors/dimensions and random initialization; HDBSCAN density neighbors are now bounded by corpus size. No passages are duplicated or forced into themes. Results may legitimately be few themes or entirely Uncategorized. One/two-passage discovery and minimum evidence exceeding the corpus remain blocked with clear guidance.
Validation: 59 locally runnable Qt-free tests passed, including five-passage results with themes and all-outliers results. Added a five-passage DOCX Analysis Setup GUI regression and extended the opt-in real-model test to five-passage discovery. PySide6 and real model dependencies are unavailable here, so Windows GUI and genuine model execution remain to verify. No new dependencies or schema migration.


## Milestone 10 — Analysis History and exports
In **Research Workspace**, choose a saved analysis and click **Rename analysis…**. Names persist when you reopen the project and appear in exports and Analysis History. Use at most 120 characters on one line; custom names must be unique. Clear the name to restore its automatic timestamp/passage-size title. Research data, settings, and theme identities are retained. Renaming a source analysis while a refinement proposal is pending invalidates that proposal's saved fingerprint; prepare a fresh proposal before applying.

**Analysis History…** lists saved analyses, previews, and proposals with readable names, settings, model details, and UTC timestamps. **Project activity** shows researcher coding, removals, rejections, theme renames/merges, notes, naming attempts, and refinement decisions. It pages 50 runs or 100 events at a time. Technical identifiers and full JSON are available through the optional Technical details checkbox.

**Export codebook…** saves CSV or JSON containing current labels, descriptions, keywords, unique coded-range counts, document coverage, representative exact evidence, and theme/passage/document notes. JSON includes full settings and model metadata. **Export coded passages…** saves CSV with exact source text, source character offsets, page/paragraph locations where recorded, all active themes on the range, researcher/model assignment sources, notes, and run provenance. CSV uses UTF-8 with a BOM and standard quoting so Unicode and multiline fields round-trip in Excel-compatible readers. List/nested fields use JSON inside CSV cells.

Exports follow the selected saved analysis's document corpus. **Manual coding only** exports active researcher coding across the project and excludes unaccepted suggestions. Saved-run exports include current active researcher coding and suggestions from that run unless rejected or removed. Human coding takes precedence at an identical theme/range. Merged themes are exported under their current target. Retained notes on removed coding records are included if that exact range/theme is currently active; JSON records show the earlier coding state. Removed documents remain available in historical analyses and are marked in exports.

Counting: a coded-passage CSV row is one unique `(document, start, end)` range, with all its active themes grouped together. Overlapping but different ranges occupy separate rows. Theme counts count unique ranges within each theme, so their sum can exceed the number of passage CSV rows. Coverage counts unique document identities. Offsets are zero-based Python character positions into immutable extracted text, with start included and end excluded. Unaccepted model suggestions remain explicitly marked as model assignments. Counts are also explained beside the controls and in export metadata/CSV columns.

Exports read one consistent database snapshot, run in a cancellable worker, and never update project research or history. Files are written atomically; cancellation or failure before replacement preserves an existing export. Database/source/model paths cannot be export destinations. CSV carries run/model identifiers; codebook JSON is the richer format for complete reproducibility metadata.

### End-to-end acceptance check
1. Create a new project folder, import PDF/DOCX/TXT documents, and preview selected or all document passages.
2. Select documents and analyze; inspect candidate themes, exact evidence, and Uncategorized passages.
3. Create a manual theme, code a highlighted range, accept some model passages, reject another, and add passage/document/theme notes.
4. Rename and merge themes. Rename the saved analysis to a descriptive name.
5. Refine an analysis, inspect the proposed evidence and identity choices, and Apply or Cancel.
6. Close and reopen the project. Confirm the saved analysis name, researcher labels, coding, notes, and refinement decisions remain.
7. Open Analysis History and inspect settings/model details plus researcher activity.
8. Export codebook CSV and JSON and coded passages CSV. Open the CSVs in Excel or a CSV reader; verify exact Unicode/multiline quotations, notes, current merged labels, rejected passages excluded, and the count definitions above.
9. Switch to Manual coding only and export again; verify unaccepted model suggestions are excluded.

Update an existing installation: close the app, copy the bundle's `src` and `tests` into your existing repository, replacing matching files. Preserve `.venv` and all project/data folders. No dependencies or database migration are added. Run `pytest -q`, then follow the checks above. Local validation: 72 Qt-free tests passed; every Python file parsed successfully. Two new offscreen Qt tests cover persistent names/history and threaded CSV export; PySide6 is unavailable in this environment, so those require Windows execution.

Prompt 11 is usability, packaging, and release validation. Its release plan should distinguish a standard installer with Python/dependencies/embedding weights from an optional larger offline bundle with Ollama and a default local naming model. Dependency and model redistribution, installer size, model locations, updates, and clean-machine verification still need implementation and validation.


## Milestone 10 Fix 1 — Windows newline regression
The export test now locates paragraph boundaries in both LF and CRLF text. The TXT importer and export preserve immutable original line endings and character offsets. Added explicit LF/CRLF exact-quote CSV round-trip coverage. Validation: 73 Qt-free tests passed; all 10 export tests also passed with Windows-style newline translation simulated for fixture writes. This is a test-only correction; no application or project data change. User reported 106 passed, one skipped, and 19 subtests passed before this fix, with only the newline-dependent export test failing.


## Milestone 10 Fix 2 — Visible theme keywords
Research Workspace now shows selectable, wrapping Keywords text beneath the Themes list for the selected theme. Keywords are saved BERTopic evidence-group descriptors, separate from the LLM/researcher label. A shared storage query supplies both the GUI and codebook: selected-run keywords are preferred, merged numeric groups combine keywords ranked by score, and earlier accepted themes fall back to their most recent completed run. Manual themes without model metadata show an explicit no-keywords message; clearing selection resets the text. Labels, coding, and notes are unchanged. No dependency or schema change. Validation: 74 Qt-free tests passed, including GUI/export keyword-query consistency after rename/merge; all Python syntax checked. Added an offscreen Qt selection/reset check, requiring Windows execution. User confirmed the codebook export appears to work.


## Milestone 10 Fix 3 — Keyword scores
Selected theme keywords now display their saved BERTopic relevance weights, e.g. research (0.1234), using four significant digits so small weights remain visible. These are keyword representation scores, not confidence percentages or probabilities. The tooltip explains this and that merged themes retain the maximum saved score for a repeated keyword across combined groups; no recomputation is implied. Shared scored storage query preserves selected-run/fallback ordering and the existing keyword-only codebook format. No schema/dependency change. Validation: 75 Qt-free tests passed, including score preservation after renaming, maximum-score aggregation after merging, and small weights. Updated the offscreen GUI assertion; full Windows pytest still required.


## Milestone 10 Fix 4 — Percentage display
At user request, keyword weights now display as percentages to two decimal places: 0.1234 becomes 12.34%. The tooltip explains that this is weight × 100 and does not imply confidence or probability. Stored weights and exports are unchanged. This supersedes the four-significant-digit GUI format above; very small weights may round to 0.00%. Validation: 75 Qt-free tests passed; all Python files parsed. Updated the GUI assertion for percentage formatting; Windows GUI verification remains required.


## Milestone 10 Fix 5 — Transcript marker filtering
Derived model input now removes recognizable speaker identifiers (SPEAKER_00, speaker 01, bracketed identifiers), bracketed timestamps/ranges, line-leading bare timestamps/ranges, and line-leading Interviewer/Interviewee/Participant turn labels with colons. It leaves ordinary speaker-related prose, substantive numbers, and times within sentences intact. Original imported text, source copies, preview ranges, exact quotes and highlighting offsets remain immutable. Marker-only passages are excluded from new analysis input; passage previews still show source text. New discovery and fixed-partition keyword refinement use the same cleaner. Cleaned text already determines embedding cache keys, preventing stale embeddings from being reused for changed input. Saved analysis metadata records model_text_filter=transcript-markers-v1. Existing saved keywords are not silently changed: run a new analysis or Refine Analysis → Refresh theme words, review and Apply, then preview updated names if desired. Unrecognized transcript formats may still need an example to extend the filter. No schema/dependency change. Validation: 77 Qt-free tests passed, including common LF/CRLF transcript formats, preservation of meaningful text, marker-only passage exclusion, source hash/offset preservation, and refinement input consistency. Syntax checked. Actual transcript/model behavior must be checked on Windows.


## Visualizations milestone
Added a dedicated **Visualizations** tab with a named saved-analysis selector and **Build / Refresh visualizations**. Four views: **Document map**, **Heatmap**, **Theme hierarchy**, and **Text topic tree**. The sidebar lists current theme names and evidence counts; select a theme and click **Open evidence in Research Workspace** to open the matching run/theme. Views invalidate when the selected analysis/project changes. Refresh after researcher edits.

The app does not persist fitted BERTopic objects. For display, it rebuilds a temporary BERTopic manual model from the same read-snapshot effective coding used by exports: current active human ranges and unsuppressed selected-run suggestions, current merge targets, notes/labels, and the selected corpus. Theme memberships are checked after BERTopic renumbers its local topic IDs; local plot numbers never replace stable research UUIDs. Empty/marker-only evidence is excluded. Multi-theme ranges contribute to each theme, so map points can overlap at the same location and theme counts can exceed unique range counts. This is a current-coding view rather than a reconstruction of original discovery clustering.

Uses actual BERTopic `visualize_documents`, `visualize_heatmap`, `hierarchical_topics`, `visualize_hierarchy`, and `get_topic_tree` APIs. Document map uses cached local passage embeddings where available, otherwise local-only SentenceTransformer weights; missing local weights produce a map-specific message while other views remain available. The map plots passages, with document names and source excerpts in hover details, using deterministic 2D PCA coordinates supplied to BERTopic. This projection is exploratory and is not the original UMAP clustering layout. No random fallback coordinates or internet downloads are used.

Heatmap and hierarchy use rebuilt c-TF-IDF representation of current evidence, not placeholder semantic embeddings. Uncategorized is excluded from similarity/hierarchy and listed separately in the text tree; a hierarchy needs two regular nonempty themes. Internal branch names are exploratory keyword summaries, not new saved parent themes. Existing human/LLM labels appear on leaves. A zero/one-theme or all-Uncategorized analysis shows clear explanations for unavailable views. Rebuilding c-TF-IDF can differ from the original saved keyword representation, particularly after coding/removal/merges.

Plotly JavaScript is embedded in local temporary HTML files and displayed through an off-the-record Qt WebEngine profile. External URL requests are blocked and a content-security policy disallows connections. No cloud/CDN calls. WebEngine initializes shared OpenGL contexts at app startup; plot viewers are loaded lazily. Visualization cancellation and shutdown use the shared task lifecycle and wait for the current model stage. Research tables/history are never updated, and the temporary fitted model is discarded. Current limits are 5,000 coded ranges and 100 nonempty themes per visualization build to bound interactive/matrix work.

No new dependency or database migration. The full PySide6 distribution supplies Qt WebEngine; BERTopic analysis dependencies supply Plotly/sklearn. A missing WebEngine module is explained in the plot panels rather than crashing the app. Update by closing the app and replacing `src` and `tests`, preserving `.venv` and all data/project folders; run `pytest -q` and `doc-topic-model-tool`.

Acceptance check: choose a saved analysis in Visualizations, Build, pan/zoom the map and hover passages, inspect similarity cells and hierarchy leaves, read the text topic tree, then select a sidebar theme and Open evidence. Rename/merge/code/reject in Research Workspace, return and rebuild to confirm current names/memberships. Reopen the project and rebuild the saved analysis. Try an all-Uncategorized result, cancellation, and closing during a build. No visualization should change saved coding, labels, notes or history.

Validation: 86 locally runnable Qt-free tests passed, including visualization snapshot scoping and effective coding, restored topic-number mapping, deterministic finite PCA with tiny inputs, all-outlier/missing-weight messages, cached embeddings without model loading, cancellation, read-only behavior, embedded-JS/CSP contracts, and fake-model calls to the built-in BERTopic APIs. Python syntax checked. Added two offscreen GUI checks for worker/build/navigation and empty projects, plus a real BERTopic plot API test using supplied vectors and no embedding download. PySide6/BERTopic are unavailable in this executor; those GUI/actual plot tests require Windows. The user reported 113 tests passed, one skipped and 28 subtests passed before this milestone; transcript filtering helped. Prompt 11 release packaging remains next after visualization verification.
