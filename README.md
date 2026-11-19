# Document Topic Modeling Tool

A desktop application for exploring themes in documents and carrying out qualitative coding. It combines local BERTopic analysis with researcher-controlled themes, highlighted evidence, and notes. Optional Ollama labeling gives generated themes readable names.

The application runs entirely on your computer. Everything it needs is included in the installer, it works without an internet connection, and your documents never leave your computer.

## Install

1. Open the [latest release](../../releases/latest) and download the file for your computer:
   - **Windows 10 or 11:** `TopicModelTool-Setup-<version>.exe`
   - **Mac with Apple silicon (M1 or newer):** `TopicModelTool-<version>-macOS-arm64.dmg`
   - **Mac with an Intel processor:** `TopicModelTool-<version>-macOS-x86_64.dmg`
   - **Optional, for readable theme names:** all three parts of the naming model, `naming-model-llama3.1-8b.tmtpack.001`, `.002` and `.003` (about 4.9 GB together; GitHub limits each file to 2 GB, so it is split). Keep the parts together. On Windows, save them in the same folder as the installer. Naming works best with 16 GB of memory.
2. Double-click the file and follow the steps.

Windows and macOS show a warning the first time, because this free app isn't sold through an app store. [How to open it the first time](docs/FIRST_OPEN.md) walks through the few clicks.

No Python, terminal, or downloads are needed after installing. For labs without internet access, copy the installer (and every model pack part) onto a USB drive.

## Use

1. **Create Project** in an empty folder, or **Open Project** to resume saved work.
2. **Import Documents** to add text-based PDFs, Word `.docx` files, or `.txt` files.
3. Review the extracted text for selected documents or all documents.
4. Select documents individually or use **Select All**, then run an analysis. Minimum evidence counts passages across all selected documents. Adjust passage size, discovery style, minimum evidence, ignored words, and optional theme naming in Analysis Setup. For files containing only one or two sentences, **Whole document** keeps each file as one passage.
5. Inspect and refine the results in **Research Workspace**.

Scanned PDFs need OCR before import. Review extracted text before analyzing: document formatting and reading order may differ from the original file. The default analysis targets English text.

## Run from source (developers)

Use Python 3.12. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[analysis,test]"
python scripts/fetch_resources.py --skip-engine   # once, while online: bundles the theme-finding model
doc-topic-model-tool
```

Without a bundled engine, theme naming uses an Ollama already running on this computer. Building installers is described in [docs/BUILDING.md](docs/BUILDING.md).

New to the code? Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). It covers how the layers fit together, the main flows (import, analysis, coding, refinement), threading, and the database.

## Choose an analysis method

In **Analysis Setup**, choose **Auto** for passages sized to the local embedding model.
This uses the exact cleaned model input and includes special tokens in the count. It prefers
sentence and paragraph boundaries, splits oversized sentences when necessary, and preserves
original source offsets for highlighting and coding. The preview reports the token range,
model limit, oversized passage count, and extra splits within sentences. The same splitting
routine is used for preview and analysis. Auto is also selected by Restore Recommended Defaults;
existing saved passage choices are retained.

Short, Standard, Long, Paragraphs, and Whole document retain their existing boundaries.
If their model inputs exceed the limit, choose Auto or explicitly check **Allow only the start
of oversized passages**. With that permission, text beyond the limit does not contribute to
the embedding even though the full passage remains visible. Auto never allows truncation.
Old analyses are not resegmented, and researcher coding remains anchored to its saved source
ranges. New runs record the splitting method, model/tokenizer identity, token diagnostics,
and truncation choice in Analysis History. Tokenizer loading is local-only and does not load
embedding weights just to preview boundaries. Missing tokenizer resources produce repair
guidance rather than a guessed token limit.

Move through the guide and setup controls with Tab/Shift+Tab and activate focused buttons or checkboxes
with Space. Tab leaves the topic-definition and ignored-word editors instead of inserting
a tab character. The start screen scrolls and stacks its columns when space is limited;
the setup preview follows the application font size.

The start screen shows topic-finding readiness separately from optional naming. Open
**Advanced local setup** for repair and model-pack controls. Analysis Setup keeps naming
model selection under **Advanced analysis and local naming setup**; keyword names work
without an optional naming model.

When you close or switch projects, the app remembers your Research Workspace document,
selected text, analysis, code/theme, view options, scroll position, and active tab. These
preferences stay with the project and its backups. Unavailable saved items use the normal
opening view; removed documents that remain available as evidence retain their [removed]
label. Position saving occurs on normal close/switch, not continuously or after a crash.

New to the tool? Choose **Try a practice project** on the start screen. It creates three
clearly marked fictional interviews in a separate project under the app's data folder.
The **Project guide** shows the location and a short exercise: check the text, code a
passage, add a comment, compare another account, export, and reopen your work. The manual
coding exercise needs no AI model. Optional topic discovery still needs the bundled model.
Use **Start a fresh practice copy** to begin again; earlier practice copies are kept and
remain available in Recent projects. This does not reset or delete a research project.
The Project guide is also available in ordinary projects, alongside the existing tabs.

Completed analyses are available for reading, coding, and commenting while optional
local theme naming runs. Names initially use keywords, then update in place without
resetting your passage selection or comment draft. Cancelling naming keeps the saved
analysis and does not publish partial names. Researcher renames and changes made during
the naming request are preserved. Switching projects and starting another analysis remain
unavailable until the current task finishes or cancellation completes.

Automatic naming is skipped when an analysis produces more than 50 topics; its keyword
names remain available. Review or reduce the topics before explicitly requesting naming.
The naming action asks before processing a large run. Turning off **Automatically name
themes** also avoids Ollama requests for smaller new analyses.

Cancellation is checked while preparing names and saving passages/names. An in-flight
Ollama request still waits for its response or timeout (up to the configured 90-second
read timeout). A database-busy message means another operation holds a lock; wait for
cancellation, close other app copies using the project, and retry. If it persists, save
drafts and restart the app. Do not delete database journals or lock files.

Embedding respects the configured batch size and automatically retries recognized memory
failures using smaller batches. If even one passage cannot fit, the task reports a memory
error. Completed cached work remains available for a later run. Progress counts cover the
current stage; cancellation waits for the current model call or clustering operation.

Choose **Semantic*** to try experimental boundaries based on changes between adjacent
sentences. A **Minimum passage target** of 128 tokens prevents similarity changes from
splitting every short sentence into a separate passage. Increase the target or lower
sensitivity for fewer boundaries; the target cannot override the model's maximum token
limit. Short final passages and forced size splits remain possible. A value of zero
restores unrestricted similarity splitting. Existing saved analyses retain their ranges;
new analyses record the revised method and minimum target. The preview warns above 5,000
passages; this is guidance, not a corpus-size limit, and no source text is dropped.

Semantic splitting uses the local embedding model and preserves source coordinates and token
limits. The first preview loads model weights and can be slower than Auto; subsequent
previews reuse cached sentence vectors in the project. Higher boundary sensitivity splits
more often; it is not a confidence score. Compare the preview with Auto and review passages
in context. Similarity boundaries can cross paragraphs and speakers: choose Interview when
speaker boundaries matter. Final passages are embedded normally for topic analysis, rather
than replacing their embeddings with averages of sentence vectors. This option is not the
default and has not been validated as improving qualitative research results.

Choose **Interview** for transcripts with recurring speaker labels. Different speakers stay
in separate passages; consecutive turns from the same speaker can be combined, and long
answers split within the model's token limit. Short answers such as “Yes” are retained
rather than discarded or merged into another speaker's words. Questions and surrounding
dialogue remain available in the source reader; excluding interviewer turns affects model
input, not the original document or saved source coordinates.

This mode deliberately requires at least two distinct labels, each occurring twice, and
no unconfirmed one-off label candidates. It supports the existing colon, bracketed-label,
timestamped, and Otter-style layouts. If boundaries are uncertain or labels are absent,
that document uses Auto and keeps all turns in the model input, even if interviewer
exclusion was requested. The preview explains the fallback and does not grey out retained
text. History records detected turns and affected document IDs. Review these previews:
repeated labels are a practical heuristic, not a guarantee of correct speaker identification.

Analysis Setup offers three methods, all using the local embedding model:

- **General discovery:** Find recurring groups without predefined topics.
- **Guided discovery:** Provide one seed group per line, such as `Healthcare access | clinic, medication, insurance`. Separate seed words or phrases with commas. Seeds steer clustering; they do not guarantee a topic or its final name. Without automatic naming, themes use keywords; with it, Ollama proposes readable names.
- **Zero-shot topics + discovery:** Provide one category per line, such as `Healthcare access | Barriers to obtaining medical care and medication`. Text before `|` is the category name; text after it is embedded for matching. Each passage is assigned to its best matching category if its cosine similarity reaches the threshold. This is semantic similarity, not a confidence probability. Review excerpts and calibrate the threshold for your data. Supplied category names are preserved by automatic naming; researchers can rename them manually.

In zero-shot mode, unmatched passages go through ordinary discovery if at least `max(3, minimum evidence)` remain; smaller residual sets stay Uncategorized. Minimum evidence governs discovered clusters, not predefined matches. Categories without matches do not create empty themes. Additional discovered groups use keyword names or optional Ollama naming. Automatic reduction is disabled for zero-shot analyses; explicit researcher merges remain available.

Research Workspace shows the method and selected theme's origin. Hover over the method for saved inputs and matching details. Analysis History records method, seed words/descriptions, and initial matching counts. Origin remains distinct from a researcher's acceptance or rejection of an assignment. Merged groups can retain multiple origins. Renames do not change how a group originated.

Codebook JSON/CSV and coded-passage CSV exports include the method and configuration. Codebook rows and passage assignment details identify topic origins and original supplied category names. Export format version 2 adds these fields while retaining existing evidence, labels, notes, and source offsets. Initial matching counts describe the original model output, not subsequent researcher edits; refinements link back to their initial matching run.

## Theme naming

Analysis works without a naming model and uses keyword-based theme names. With the optional naming model pack installed, a local language model (Llama 3.1 8B by default) proposes readable names. It runs in a private local AI engine (Ollama) that the app starts and stops itself, reachable only from this computer. No cloud model is used.

To add the model later: on the start screen, choose **Add naming model…** and pick the `.tmtpack` file, or the `.tmtpack.001` file of a split pack (the other parts must be in the same folder).

Llama 3.1 is licensed under the Llama 3.1 Community License, Copyright © Meta Platforms, Inc. Built with Llama.

## What you can do

- **Start quickly:** the start screen lists recent projects and shows whether the local models are ready (**Check & repair** fixes most problems). **New project** names the project, creates its folder, and imports dropped files or whole folders, flagging scanned PDFs that need OCR.
- **Set up an analysis:** choose a passage size and see exactly how documents are split before running. For interview transcripts, **Leave out interviewer turns** keeps interviewer prompts from forming their own themes (they stay visible in the documents).
- **See where to read:** after an analysis, the **Overview** tab shows a card per theme (keywords, two representative quotes, how many passages and documents) and a heatmap of which documents mention which theme. **Unusual / ungrouped passages** are shown as worth reading, not leftovers, and a coverage bar shows how much you have reviewed.
- **Read in a guided order:** **Guided reading** walks through a theme's passages, best-fitting first, each in its surrounding context. Mark each one relevant or not relevant, or open it in the full document. Fit strength is cosine similarity to the theme's centre, not a confidence score.
- **Read and annotate documents:** coded passages are coloured by theme with marks beside the scrollbar. Select text to code it (keys 1–9) or add a comment in the margin; comments have replies, can be resolved, and drafts save as you type. **Ctrl+F** finds text in the open document; **Ctrl+Z** undoes coding.
- **Search everything:** **Ctrl+Shift+F** searches all documents (and optionally comments), with snippets, page numbers, and theme tags.
- **Code and refine:** create, rename, and merge themes; accept or reject model suggestions; refresh keywords, preview new names, reduce themes, or run fresh discovery.
- **Visualize results:** passage map, theme similarity heatmap, theme hierarchy, and topic tree.
- **Export:** a printable **reading guide** (Word) per theme, a codebook (CSV/JSON), or coded passages (CSV) with evidence, notes, and provenance.

Generated themes and names are preliminary pointers for researcher review. Results depend on the text, passage size, and analysis settings; small datasets may yield few themes or only ungrouped passages.

## Your project data

A research project is a folder containing `project.sqlite`, `sources/`, and `models/`. It stores documents, coding, comments, reading progress, saved analyses, and cached passage embeddings. Projects from earlier versions upgrade automatically when opened; a copy of the old database is kept beside it (`project.sqlite.v1-backup`). The theme-finding and naming models are installed with the app and shared by every project. Reopen that folder to continue working. Back up the whole project folder with the application closed.

Keep private research projects and exports out of your Git repository. For example, ignore `/test_docs/` and `/outputs/` in `.gitignore`. Ignoring a folder does not remove files already tracked or committed to Git.

Use **Project → Back up project…** to create a verified `.tmtbackup` file outside the project folder.
Finish running tasks first. The backup includes the database, source documents (including removed
documents), saved analyses, comments, existing notes, and project-local model files. Pending comment
drafts are saved before backup. Choose a new filename for each backup; existing backups are not replaced.

Choose **Restore a project backup…** on the start screen or in the Project menu, select the backup,
and choose a parent folder and a new folder name. Restore verifies the archive and source documents,
never overwrites an existing folder, and offers to open the restored copy. Backups are not encrypted;
store them according to your research data requirements. The current archive limit is 20 GiB of
expanded data and 100,000 project files. Copies on the same disk do not protect against disk failure.

**Automatic recovery snapshots** are enabled by default for each project location. While
the project is open, the app checks once a minute and creates one verified snapshot per
local calendar day after at least 30 seconds without keyboard, click, or scroll activity.
It waits while a task, dialog, or menu is active. A progress dialog pauses editing during
capture. Pending comment drafts are saved first; a failure leaves normal saving available
and is shown in the status bar, with automatic retry no sooner than 15 minutes later.

Snapshots live beside the project in a separate `.tmt-recovery-…` folder; hover over the
recovery status to see its exact location. The latest seven successful snapshots are kept.
Old copies are pruned only after a new verified copy is recorded. If cleanup fails, extra
copies are retained with a status message. Manual backups and unrelated files are not pruned.
These copies are not encrypted and may be synchronized by other software if their parent
folder is synced. They protect against some accidental changes, not loss of the same disk.

Use **Project → Automatic recovery snapshots** to turn scheduling off for that project
location, **Create recovery snapshot now…** for an extra copy, or **Recover from snapshot…**
to restore into a new folder. The start screen's **Recover a recent project…** works without
opening the original project. Moving or renaming a project starts a new recovery history;
older archives remain in their original folder and can also be opened through manual restore.

## Check the installation

The installer runs a self-test. To repeat it, run the app with `--self-test`. Developers can run the test suite with `python -m pytest -q`.

The sample documents in `examples/smoke_corpus/` provide a small corpus for trying import and analysis without using private research data.
