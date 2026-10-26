# Document Topic Modeling Tool

A desktop application for exploring themes in documents and carrying out qualitative coding. It combines local BERTopic analysis with researcher-controlled themes, highlighted evidence, and notes. Optional Ollama labeling gives generated themes readable names.

The application runs locally. An internet connection is needed to install dependencies and download models initially; normal analysis and coding work offline.

## Install

Use **Python 3.12.1**, the version used for development.

1. Install Python 3.12.1 from:
   https://www.python.org/downloads/release/python-3121/
   - **Windows:** Choose the Windows installer (64-bit).
   - **Mac:** Choose the macOS 64-bit universal2 installer.
2. Download this repository using **Code → Download ZIP** on GitHub, then extract it, or clone the repository.
3. Open PowerShell on Windows or Terminal on Mac, and navigate to the extracted folder containing `pyproject.toml`.

### Windows — PowerShell

Check the installed Python version:

```powershell
py -3.12 --version
```

For these instructions, it should report `Python 3.12.1`. Then run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[analysis,test]"
python -m pip check
```

If PowerShell blocks activation, use these commands instead:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[analysis,test]"
.\.venv\Scripts\python.exe -m pip check
```

### macOS — Terminal

Check the installed Python version:

```bash
python3.12 --version
```

For these instructions, it should report `Python 3.12.1`. Then run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[analysis,test]"
python -m pip check
```

Installation downloads the application dependencies and may take several minutes.

### If you already use pyenv

You can select Python 3.12.1 with:

```bash
pyenv install 3.12.1
pyenv local 3.12.1
```

Skip the installation command if that version is already installed. Then use `python -m venv .venv` and follow the activation and installation commands for your operating system above.

If you move the repository, recreate `.venv` in the new location and reinstall the application. Keep your research project folders.

## Run

Activate `.venv` in a terminal opened at the repository root, then launch:

```bash
doc-topic-model-tool
```

1. **Create Project** in an empty folder, or **Open Project** to resume saved work.
2. **Import Documents** to add text-based PDFs, Word `.docx` files, or `.txt` files.
3. Review the extracted text and preview passages for selected documents or all documents.
4. Before your first model analysis, use **Download model** while online. This downloads the local sentence-embedding model into the current project's model folder. Each project currently maintains its own model cache.
5. Select documents individually or use **Select All**, then run an analysis. Minimum evidence counts passages across all selected documents. Adjust passage size, discovery style, minimum evidence, ignored words, and optional theme naming in Analysis Setup. For files containing only one or two sentences, **Whole document** keeps each file as one passage.
6. Inspect and refine the results in **Research Workspace**.

Scanned PDFs need OCR before import. Review extracted text before analyzing: document formatting and reading order may differ from the original file. The default analysis targets English text.

## Choose an analysis method

Analysis Setup offers three methods, all using the local embedding model:

- **General discovery:** Find recurring groups without predefined topics.
- **Guided discovery:** Provide one seed group per line, such as `Healthcare access | clinic, medication, insurance`. Separate seed words or phrases with commas. Seeds steer clustering; they do not guarantee a topic or its final name. Without automatic naming, themes use keywords; with it, Ollama proposes readable names.
- **Zero-shot topics + discovery:** Provide one category per line, such as `Healthcare access | Barriers to obtaining medical care and medication`. Text before `|` is the category name; text after it is embedded for matching. Each passage is assigned to its best matching category if its cosine similarity reaches the threshold. This is semantic similarity, not a confidence probability. Review excerpts and calibrate the threshold for your data. Supplied category names are preserved by automatic naming; researchers can rename them manually.

In zero-shot mode, unmatched passages go through ordinary discovery if at least `max(3, minimum evidence)` remain; smaller residual sets stay Uncategorized. Minimum evidence governs discovered clusters, not predefined matches. Categories without matches do not create empty themes. Additional discovered groups use keyword names or optional Ollama naming. Automatic reduction is disabled for zero-shot analyses; explicit researcher merges remain available.

Research Workspace shows the method and selected theme's origin. Hover over the method for saved inputs and matching details. Analysis History records method, seed words/descriptions, and initial matching counts. Origin remains distinct from a researcher's acceptance or rejection of an assignment. Merged groups can retain multiple origins. Renames do not change how a group originated.

Codebook JSON/CSV and coded-passage CSV exports include the method and configuration. Codebook rows and passage assignment details identify topic origins and original supplied category names. Export format version 2 adds these fields while retaining existing evidence, labels, notes, and source offsets. Initial matching counts describe the original model output, not subsequent researcher edits; refinements link back to their initial matching run.

## Optional local theme naming with Ollama

BERTopic analysis works without Ollama and uses keyword-based theme names. To generate names with a local language model:

1. Install [Ollama](https://ollama.com/) and make sure it is running.
2. Download a model, for example:

   ```bash
   ollama pull llama3.1
   ```

3. In the application, refresh the installed model list, choose your model, and enable automatic naming or preview new names for a saved analysis.

Ollama must be available locally at `http://127.0.0.1:11434`. Initial model downloads require internet access and may be large. No cloud model is required.

## What you can do

- **Explore documents:** Import multiple sources, inspect extracted text, and choose how text is divided into passages. Remove documents from the active corpus or restore them while retaining historical research.
- **Discover themes:** Analyze selected documents together using BERTopic. Revisit and rename saved analyses. Passages without a discovered group appear as **Uncategorized**.
- **Code and annotate:** Create themes, assign highlighted text to one or more themes, accept or reject model suggestions, rename or merge themes, and save notes for passages, documents, or themes. **Manual coding only** is available for researcher-led work.
- **Inspect evidence:** Select a theme to view its matching passages across documents, highlights, keywords, and keyword weights. Keyword weights are c-TF-IDF weights displayed as percentages; they are not confidence probabilities and do not necessarily sum to 100%.
- **Refine an analysis:** Refresh keywords, preview new names, reduce themes, or run fresh discovery. Review proposed changes before applying them.
- **Visualize results:** Explore a passage-based document map, theme similarity heatmap, theme hierarchy, and text topic tree. Choose PCA or UMAP for the map, adjust UMAP settings, and compare original model groups with current coding. Hierarchy branches are exploratory groupings rather than saved parent themes.
- **Review and export:** Browse Analysis History and export a codebook as CSV/JSON or coded passages as CSV, including evidence, notes, and provenance.

Generated themes and names are suggestions for researcher review. Results depend on the text, passage size, and analysis settings; small datasets may yield few themes or only Uncategorized passages.

## Your project data

A research project is a folder containing `project.sqlite`, `sources/`, and `models/`. It stores documents, coding, notes, saved analyses, and available model/visualization artifacts. Reopen that folder to continue working. Back up the whole project folder with the application closed.

Keep private research projects and exports out of your Git repository. For example, ignore `/test_docs/` and `/outputs/` in `.gitignore`. Ignoring a folder does not remove files already tracked or committed to Git.

## Check the installation

With `.venv` activated:

```bash
python -m pytest -q
```

The sample documents in `examples/smoke_corpus/` provide a small corpus for trying import and analysis without using private research data.
