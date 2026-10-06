# Document Topic Modeling Tool

A desktop application for exploring themes in documents and carrying out qualitative coding. It combines local BERTopic analysis with researcher-controlled themes, highlighted evidence, and notes. Optional Ollama labeling gives generated themes readable names.

The application runs locally. An internet connection is needed to install dependencies and download models initially; normal analysis and coding work offline.

## Install

Use **Python 3.12**. Download or clone this repository, then open a terminal in the `doc_topic_model_tool` folder containing `pyproject.toml`.

### Windows — PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[analysis,test]"
```

If PowerShell blocks activation, you can install and launch directly through the environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[analysis,test]"
.\.venv\Scripts\doc-topic-model-tool.exe
```

### macOS / Linux

With Python 3.12 available as `python3.12`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[analysis,test]"
```

If you move the repository to another location, recreate `.venv` there and reinstall the application. Keep your project data folders.

## Run

Activate `.venv` in a terminal opened at the repository root, then launch:

```bash
doc-topic-model-tool
```

1. **Create Project** in an empty folder, or **Open Project** to resume saved work.
2. **Import Documents** to add text-based PDFs, Word `.docx` files, or `.txt` files.
3. Review the extracted text and preview passages for selected documents or all documents.
4. Before your first model analysis, use **Download model** while online. This downloads the local sentence-embedding model into the current project's model folder. Each project currently maintains its own model cache.
5. Select documents and run an analysis. Adjust passage size, discovery style, minimum evidence, ignored words, and optional theme naming in Analysis Setup.
6. Inspect and refine the results in **Research Workspace**.

Scanned PDFs need OCR before import. Review extracted text before analyzing: document formatting and reading order may differ from the original file. The default analysis targets English text.

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
- **Inspect evidence:** Select a theme to view its matching passages across documents, highlights, keywords, and keyword weights. Weights displayed as percentages are relevance scores, not confidence probabilities.
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
