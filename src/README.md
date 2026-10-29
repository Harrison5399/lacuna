# LACUNA

Give Lacuna a research goal ("new treatment avenues for glioblastoma"). It pulls
the literature for that field, turns it into a knowledge graph, looks for holes
in the graph, writes the best holes up as hypotheses backed by real citations,
checks them against outside data, lets a second model attack them, and hands back
a short ranked list of hypothesis cards.

Design background is in the handoff folder:
[lacuna_handoff/docs](../../../Claude%20outputs/lacuna_handoff/docs/).

---

## The big picture

```
goal -> seed IDs -> knowledge graph (database facts only at first) <----+
                         |                                             |
                         v                                             |
                    00 pick topics -> 01 download -> 02 pipeline ------+
                         |            (repeat until the data budget is reached)
                         v
                    the same knowledge graph, now with literature edges
                         -> 03 gaps -> 04 draft + ground -> 05 dry test
                         -> 06 critic -> 07 report -> researcher feedback (back to 03)
```

 - There is **one graph**. The top loop grows it; the bottom line uses it. One
   corpus can be grown once and then reused by many runs of 03-07.
 - The graph is stored as tables: one Parquet file per pipeline stage
   (sentences, mentions, links, claims, edges), queried with DuckDB. Turtle
   (`.ttl`) is only an export, for SPARQL or Oxigraph, made from the tables.
 - Every step reads files and writes files. Any step can be rerun alone.
 - Every setting lives in one run config (JSON), so the whole system can be run
   many times with different settings and compared (see Experiments).
 - Steps 00-03 use no chat LLM (except a small one in 00). The graph does the
   finding; the LLM only writes up and argues about what the graph found.
 - Every step takes a `before_date`. In evaluation runs it is 2024-06-01 and
   nothing newer may be read (see Evaluation at the bottom).

## Where things run

 - **Slate** is storage, not a computer. Both Quartz and Big Red 200 see it.
   Everything lives under `/N/project/ontology_learning/L665/data/<Corpus>/`.
 - **Quartz** runs the jobs: CPU jobs for downloading, NLP and linking; GPU
   (H100) jobs only if we train a model or host our own LLM.
 - **Big Red 200** works the same way and is the fallback if Quartz queues are long.
 - **REALLMS** is IU's free LLM API. Jobs call it over the network, so we need to
   check that compute nodes can reach it (open question).

## Shared pieces (used by more than one step)

 - **The NLP pipeline and its linker** (see The NLP pipeline below). 02 runs the
   whole pipeline over papers. 00 and 04 use its linker to turn the goal and
   hypothesis text into the same IDs.
 - **The paper store.** The Markdown files split into numbered sentences, each
   tagged with its paper, section and date. `cite` and every retrieval step
   read from it.
 - **The LLM client.** One small wrapper around the REALLMS API. Key comes from
   the environment, never written in code.
 - **The paper pool.** Every paper is downloaded and processed **once**, into
   one shared pool per disease area. A run's corpus is just a list of PMIDs
   picked from that pool. A second run that wants more papers only downloads
   the new ones. Rebuilding the graph for a run means re-counting over its PMIDs.
 - **The background store.** Every source below is downloaded once per release
   into a shared folder on Slate and loaded into DuckDB. Runs query it locally
   and never call outside APIs mid-run.

## Traceability

Every result has to be traceable back to where it came from:
card -> chain step -> claim -> sentence -> paper -> topic -> graph node that led to the topic -> seed.

 - Every row in every table (node, edge, claim, curated fact) carries:
   - `source`: pubmed, pmc, umls, chembl, ncbi_gene, reactome, pipeline, ...
   - `source_version`: the release or download date
   - `method`, for anything the pipeline produced: which stage and model
   - for text, the paper ID (`pubmed:<pmid>`, `arxiv:<id>`) and sentence number
 - The manifest records **why each paper is in the corpus**: which round,
   which topic, and which graph node led to that topic.
 - Every run folder holds a copy of its config, the code version (git commit),
   the model names, counts and timings.
 - Every step on a card names its source, so a reader can see straight away
   whether a step rests on a paper, a database or a dataset.

---

## Sources

Example IDs are from memory; each loader checks its examples when it's written.

### Side by side

| source | what it is | what's in it | what it gives the graph | how we use it |
| --- | --- | --- | --- | --- |
| **PubMed / PMC** | the literature | ~37M records: title, abstract, dates, journal, MeSH headings, publication type; full text for open access | the text every literature edge comes from | input to the pipeline |
| **UMLS** | NLM's merged dictionary of ~200 vocabularies | ~3M+ concepts (CUIs), every synonym from every vocabulary, each source's own code, semantic types, is-a and other relations | **one ID per concept** across all vocabularies, synonyms, types, hierarchies | **the backbone of the linker**; node IDs |
| **MeSH** | NLM's vocabulary for indexing PubMed (inside UMLS too) | ~30k headings + ~300k supplementary records (newer drugs, rare diseases), tree numbers, drug classes | the disease tree, drug classes, PubMed's own tags | scope (00), sibling transfer (03), linking context |
| **HGNC** | official human gene names | one row per human gene: symbol, aliases, old symbols, full name, NCBI Gene / UniProt / Ensembl IDs | clean gene nodes | gene names for the linker; ID translation |
| **NCBI Gene** | NCBI's gene database, all species | gene IDs, symbols, names for every species; human <-> mouse matches | mouse and rat studies land on the same gene node as human ones | gene node IDs, species merging |
| **ChEMBL** | EBI's drug and bioactivity database | ~2.4M compounds, lab measurements against targets, curated drug -> target mechanisms, drug -> disease indications with trial phase, synonyms and code names | **drug -> target** and **drug -> disease (phase)** edges | curated layer; newest drug names for the linker; ruling out known gaps |
| **PubChem** | NCBI's chemical database | compound IDs with cross-references (one MeSH mapping file is all we need) | nothing new, just translation | ChEMBL drug -> MeSH / CUI |
| **Cellosaurus** | the cell line registry | every cell line: names, species, disease, DepMap / COSMIC IDs, misidentification flags | cell line nodes; "this result is from U251 cells" | linker; links papers to DepMap / GDSC; doubtful-line flags for the critic |
| **GO + GO annotations** | Gene Ontology | terms for biological processes, functions, cell parts (terms are in UMLS); annotation files list which genes do what | **gene -> process** edges | curated layer; middle terms for ABC bridges |
| **Reactome** | curated pathway database | pathways with member proteins, in a hierarchy | **gene -> pathway** edges, pathway nodes | curated layer; ABC middle terms; grouping targets |
| **STRING** | protein interaction network | protein pairs with confidence scores (0-1000) from experiments, databases, text mining | **gene <-> gene** interaction edges | curated layer, only high-confidence and non-text-mining evidence |
| **ClinicalTrials.gov** | trial registry | every trial: conditions, interventions, phase, status, why it stopped, dates; already MeSH-tagged | **drug -> disease trial** edges with phase, outcome, date | trial layer; stalled leads, novelty, the critic |
| **PubTator3** | NCBI's pre-annotated PubMed | entity spans with IDs for every abstract and open-access text; relations in abstracts | nothing on its own; a second opinion on our mentions and links | a vote in linking; a yardstick to grade the pipeline; its relation model (BioREx) runs inside our pipeline |
| **SemMedDB** | NLM's ready-made literature graph (needs UMLS) | ~130M sentence-level claims from PubMed, up to 2024-05-08 | none (it would count the same papers twice) | comparison only |
| **CTD** | curated chemical-gene-disease links, read from papers | | none (same reason) | comparison only |
| **DepMap, GDSC / PRISM** | cancer cell line screens | gene knockout effects (DepMap); drug sensitivity (GDSC, PRISM) for ~1,000+ lines | measurements, not edges | dataset layer for 05 |

Not used: **DrugBank** (ChEMBL covers it without a license), **Wikidata** (rate
limits at scale).

**Prerequisite:** a UMLS license (free; apply at NLM's UTS site; a few business
days, plus a short yearly usage report).

### How they fit together

```
                  names (the linker's dictionary)
   UMLS (MeSH, HGNC, GO, NCI, RxNorm, ...) + NCBI Gene + ChEMBL names + Cellosaurus
                               |
papers --pipeline--> mentions -> concept IDs -> claims --> LITERATURE layer (counted, dated)
                               |
   ChEMBL, GO annotations, Reactome, STRING, UMLS relations --> CURATED layer
   ClinicalTrials.gov                                       --> TRIAL layer (dated)
   DepMap, GDSC, PRISM                                      --> DATASET layer (05)
```

### What each one contains

**PubMed / PMC.** Every record carries the **MeSH headings** indexers tagged it
with, and its **publication types**.
 - example headings: "Glioblastoma / drug therapy", "Temozolomide / therapeutic
   use", "Humans". Publication type: "Clinical Trial, Phase II".
 - this is free context for the pipeline: what the paper is about, which
   species, and what kind of study, before reading a sentence

**UMLS.** Each CUI gathers one concept's names from every vocabulary.
 - example: C0017636 "Glioblastoma", semantic type T191 Neoplastic Process. Its
   rows give MeSH D005909, NCI Thesaurus C3058, a SNOMED CT code, and all their
   synonyms: "glioblastoma multiforme", "GBM", "grade IV astrocytoma", ...
 - semantic types let the pipeline check a relation makes sense (a drug treats a
   disease, not the reverse)
 - we use a chosen set of vocabularies (MeSH, HGNC, GO, NCI Thesaurus, RxNorm,
   HPO) and the semantic types we need. All 200 would add millions of names
   that only create false matches.

**MeSH.** Comes inside UMLS. We also load NLM's own MeSH files, because tree
numbers and drug classes are simpler to read there.
 - example: Glioblastoma D005909, tree C04.557.465.625.600.380.080.335, parent
   Astrocytoma D001254. Temozolomide D000077204, class "Antineoplastic Agents, Alkylating".

**HGNC + NCBI Gene.**
 - example: EGFR = HGNC:3236 = NCBI Gene 1956, aliases ERBB, ERBB1, HER1,
   UniProt P00533. Mouse Egfr is NCBI Gene 13649, matched to human 1956.

**ChEMBL.**
 - example: imatinib, CHEMBL941. Mechanism: inhibits BCR-ABL, KIT, PDGFR.
   Indications: chronic myeloid leukemia (approved), glioblastoma (reached trials).
 - gives the graph the drug -> target facts papers rarely state outright, and
   tells 03 which drug-disease pairs are already in trials

**Cellosaurus.**
 - example: "U251", "U-251 MG" and "U-251MG" are one entry, with its DepMap
   ID. U-87 MG's ATCC version is flagged as not matching the original patient line.

**GO + annotations, Reactome, STRING.** The mechanism layer.
 - example chain they make possible: drug -inhibits-> EGFR (ChEMBL), EGFR -part
   of-> "Signaling by EGFR" (Reactome), EGFR -interacts with-> GRB2 (STRING),
   EGFR -involved in-> cell proliferation (GO)

**ClinicalTrials.gov.**
 - example: condition Glioblastoma, intervention temozolomide + Drug X, phase 2,
   status Terminated, why stopped "lack of efficacy", first posted 2019-03-04

**PubTator3.**
 - example: in one abstract, "TMZ" at characters 210-213 = Chemical
   MESH:D000077204, with relation `treat` to MESH:D005909
 - inside our pipeline it's a second opinion on mentions and links, and the
   free training labels for our context models

**DepMap / GDSC / PRISM.**
 - DepMap example: EGFR's knockout effect in each glioma line, where 0 = no effect
   and about -1 = as harmful as known essential genes
 - GDSC / PRISM: how much drug it takes to kill each line

---

## The NLP pipeline (papers -> knowledge graph)

The goal: run over any paper and get the most complete, most correct claims out
of it, each tied to concept IDs, qualified, and traceable to its sentence.

### Node IDs

| type | node ID | other IDs kept on the node |
| --- | --- | --- |
| disease, chemical, drug, process, anatomy, procedure | UMLS CUI | MeSH, NCI Thesaurus, RxNorm, GO, ChEMBL, PubChem |
| gene / protein | NCBI Gene (human) | HGNC, UniProt, Ensembl. UMLS has separate CUIs for "EGFR gene" and "EGFR protein"; papers use "EGFR" for both, so both map to the one gene node. |
| pathway | Reactome ID | |
| cell line | Cellosaurus ID | DepMap, COSMIC |
| species | NCBI Taxonomy | kept on claims, not as nodes |
| variant | kept on the gene node (IDH1 R132H) | dbSNP, HGVS |

Anything UMLS doesn't have yet (the newest drugs) keeps its source ID (ChEMBL)
until a UMLS release covers it.

### Stages

**1. Prepare.** Split each paper into sentences. Tag every sentence with paper ID,
section, sentence number and date. Docling writes every heading as `##`, so the
section comes from the heading text and its number ("3.1" belongs to "3.
Results"). Skip back matter (acknowledgments, funding, conflicts, license lines).

**2. Paper context**, read once per paper before any sentence:
 - MeSH headings: the paper's main concepts
 - publication type and MeSH check tags: study type ("Randomized Controlled
   Trial", "Case Reports"), species ("Humans", "Mice"), and "Cell Line, Tumor"
   for lab studies
 - the abbreviations defined in the paper ("temozolomide (TMZ)")

**3. Find mentions.** Take every span any of these find:
 - scispaCy NER (`en_core_sci_scibert`), which labels everything `ENTITY`
 - exact matches against the concept dictionary (spaCy PhraseMatcher), which
   catches gene symbols, drug codes and cell lines the NER misses
 - PubTator3's spans, for papers PubTator has
 - tmVar3 for variants ("IDH1 R132H")

**4. Link** every mention to a node ID.
 1. **Dictionary**: UMLS (chosen vocabularies) + NCBI Gene + ChEMBL names +
    Cellosaurus + Reactome names, loaded as one custom scispaCy knowledge base.
 2. **Exact match** after normalizing (case, hyphens, Greek letters). Gene
    symbols match case.
 3. **Candidates** otherwise: SapBERT (an embedding model trained on UMLS
    synonyms, so it knows "heart attack" = "myocardial infarction") plus
    scispaCy's character n-gram search. Top ~20.
 4. **Choose by context**:
    - string similarity
    - the paper's MeSH headings
    - the paper's species (human vs mouse gene)
    - the semantic type the sentence calls for
    - other linked entities nearby
    - PubTator's ID where it has one
    - one sense per paper
 5. **Ambiguous strings** ("MET", "CAT", "ALL", "PD") get the same treatment,
    plus part of speech ("met" as a verb is not a gene). The list of ambiguous
    strings is computed from the dictionary.
 6. **Below a threshold, unlinked.** A wrong ID is worse than no ID.
 7. Mentions whose concept type we don't use ("patients", "study") are dropped.
 - The context weights are learned from PubTator's links on PubMed papers, so
   the linker learns from PubTator and still works on text PubTator never saw.

**5. Find relations** in every sentence that has two or more linked entities.
Two extractors, both kept:
 - **BioREx** (PubTator's relation model, open source): the relation type and
   direction for the pairs it was trained on (chemical, disease, gene, variant;
   12 relation types).
 - **An LLM** (gpt-oss-120b on REALLMS): reads the sentence with its two
   neighbours and the linked entities, and returns JSON. It covers what BioREx
   can't: pairs involving processes, pathways, cell lines and non-drug therapies,
   and conditions like "in combination with" or "in IDH-mutant tumors".
 - Each claim records which extractor made it. Where both agree, confidence goes up.
 - **One relation vocabulary**: the UMLS Semantic Network's relation names
   (treats, inhibits, causes, interacts_with, part_of, ...). BioREx's types are
   mapped onto them, and the LLM is told to use them. The Semantic Network also
   says which entity types each relation may connect, so a claim like "glioblastoma
   treats temozolomide" is rejected.
 - Relations already in UMLS and MeSH (is-a, drug class, a few curated "may
   treat" links) go into the curated layer. They are settled background facts
   with no paper, date, count or polarity, so they can't replace extraction.

**6. Qualify** each relation:
 - **negation**: rules over the dependency parse (cue words like "not",
   "failed to", "no significant", and what they scope over), checked against
   the LLM's reading
 - **hedging**: cue words ("may", "suggest", "potential", "likely") the same way
 - **section** and **primary or not** (Results, Conclusions and abstract result
   sentences count as evidence; Introductions don't)
 - **study type, species, model**: from the paper context and the sentence
   ("in U251 cells", "in a mouse xenograft")

**7. Write claims.** One row per relation per sentence: subject, relation,
object, all qualifiers, paper ID, sentence number, the exact quote, date, method,
confidence.

**8. Aggregate** claims into literature edges (02, step 6).

**9. Add the other layers** (02, step 7): curated (ChEMBL, GO annotations,
Reactome, STRING, chosen UMLS relations), trials (ClinicalTrials.gov), datasets.

**10. Grade every stage**, so we know which parts of the graph to trust:
 - mentions and links: agreement with PubTator per type, plus hand checks where
   they disagree
 - relations: BioRED's hand-labelled test set
 - negation and hedging: a hand-labelled sample of sentences
 - the per-type and per-stage numbers go in every run's folder

### Runs on
 - stages 1-4, 6-8: Quartz CPU array jobs (one task per chunk of papers)
 - BioREx, SapBERT, tmVar3: Quartz GPU jobs
 - the LLM extractor: jobs calling REALLMS
 - a clean venv with pinned scispaCy and model versions (not the laptop's
   `--no-deps` install)

### Questions
 - is every step that needs an NLP pipeline going to use the same one, is that one going to be one that is similar in the scispacy_NER_test.ipynb?
   - 02 runs the whole pipeline. 00 and 04 use only its linker (stages 3-4), so
     every step speaks the same IDs.
   - The notebook has the right order. What changes: Wikidata linking becomes
     the UMLS-based linker above (Wikidata rate-limits at a few hundred calls),
     and dependency-rule relations become BioREx + the LLM extractor.
   - Coreference (Coreferee) can be added as a stage between 4 and 5 if
     sentences like "It inhibited growth" turn out to lose many claims. The
     graded numbers will show whether that's worth it.

---

## 00-query_scope.py
**INPUT** - User's plaintext wording on what they would like to research, plus the run config
**OUTPUT** - `scope.json` (seed IDs, the kind of answer wanted) and one topics file per round (`src/02-topics`, `round_<n>.json`). The graph itself is grown by 02.

**Runs on** - login node for picking topics; each round's download and extraction are Quartz jobs (01, 02)

### Algorithm
 1. **Seed.** An LLM (gpt-oss-120b) reads the goal and pulls out the target
    disease, the kind of answer wanted, and any limits the user stated ("only
    approved drugs", "pediatric"). The linker turns the disease into its node ID
    (Glioblastoma = C0017636, MeSH D005909). These are the seeds.
 2. **Start from database facts (no papers yet).** Before any paper is read, the
    graph holds only the curated layer. Start at the seeds and take what it
    says is nearby. Every node and edge records which source it came from.
    - MeSH tree: subtypes, parent, siblings (how far up = `tree_up`)
    - ChEMBL: drugs approved for or tested in those diseases, and their targets
    - NCBI Gene: those targets, their aliases, and mouse <-> human gene matches
 3. **Pick topics.** Score every node by how close it is to the seeds, with the
    hub penalty so vague nodes don't win. Turn the top nodes into topics:
    - disease nodes -> MeSH topics. A `"X"[MeSH]` query already includes
      everything under X.
    - drug and gene nodes -> "X outside our disease tree" topics, for example
      `"Drug X"[MeSH] NOT (<our diseases>)`, with a smaller cap
    - **split rule** (granularity): any topic over `split_cap` papers is split into
      one topic per MeSH child, plus a "rest" topic (`"X"[MeSH:noexp]`) for papers
      tagged only with X itself. Repeat until every topic fits.
 4. **Download and process** (01, then the full pipeline in 02). Each paper is
    processed once, whichever round brings it in. `--plan` prints counts first.
    The user approves before the first round, and later rounds run on their own
    within the budget.
 5. **Grow.** The new papers' claims go into the same graph. Entities the
    papers keep linking to our disease now score high and become the next
    round's topics, even though no ontology put them there.
 6. **Stop** when any of these happens:
    - the paper budget is reached
    - a round adds few new edges near the seeds (the area is saturated)
    - `max_rounds` is reached
    The pool keeps everything, so a later run can pick up where this one stopped.

 - Siblings matter: a drug that works in a sibling disease is one of the main
   kinds of gap (see 03). The ontology step puts them in before any paper is read.
 - **Evaluation runs**: step 5 grows from edges *before 2024-06-01* only.
   Otherwise a drug first linked to the disease in 2025 would get picked because
   of those 2025 papers, and the download itself would leak the answer. The pool
   can still hold newer papers, because those are the answer key.

### Correction
 - Don't expand by "X hops" over a general ontology. Two hops up from
   glioblastoma already reaches "Neoplasms", which is every cancer. Hops along
   the tree stay small (`tree_up`). Everything else is added because the papers
   point at it, not because it's nearby in an ontology.

---

## 01-topic_download.py
**INPUT** - Every round's topics file
**OUTPUT** - PubMed records and PMC full text (JATS XML), ClinicalTrials.gov study records, as downloaded

**Runs on** - Quartz CPU jobs (`src/03-download`)

### Algorithm
 - `src/03-download` downloads PubMed records and PMC full text to `data/` (the
   Brain corpus layout), with a saved selection per topic. The papers are parsed
   straight from the XML: PMC's JATS keeps real sections and paragraphs, so no
   Markdown conversion is needed.
 - New in this step: download the **PubTator3 annotations** for every PMID in
   the manifest (entities with IDs, and relations). Save the raw responses to
   disk, parse later.
 - Download the other "now" sources from Sources once per release (not per run),
   into the background store: MeSH, HGNC + NCBI Gene, ChEMBL, the PubChem
   mapping file, and ClinicalTrials.gov records for the in-scope diseases.

### Questions
 - do we need to additionally clean up these documents such that references or copy right things are all removed and stuff?
   - Reference lists are already dropped by the Quartz converter
     (`--keep-references` is off by default). The test PDFs in `test_data/` still
     have them because they were converted differently.
   - Leftover back matter (Acknowledgments, Funding, Author Contributions,
     Conflicts of Interest, license lines like "© 2023 by the authors") is still
     in the files. **Don't edit the .md files.** Skip those sections when 02
     splits the papers into sentences. The files stay as downloaded, and the skip
     list can change without redownloading.
   - Copyright: these are open-access papers, some under non-commercial
     licenses. Research use and quoting single sentences with a citation on a
     card are fine. Just don't republish whole papers.
 - right now we are only looking at pubmed data, are there any other datasources that we should also generate topics for?
   - Only PubMed needs topics. Everything else is downloaded whole or filtered
     by ID. The decided list is in **Sources** above.
   - **Evaluation runs**: use background releases from **before the cutoff**.
     Curated databases keep adding facts learned from newer papers, so a 2026
     ChEMBL would leak the answer. ChEMBL, MeSH and DepMap keep their old releases.

---

## 02-graph.py
**INPUT** - `.md` files, `manifest.csv`, the PubMed records (MeSH headings, publication types), PubTator annotations, the background store
**OUTPUT** - One knowledge graph for the whole corpus: a table of edges, with counts and dates on every edge

**Runs on** - see The NLP pipeline: Runs on

### Algorithm
 1-5. **Run the NLP pipeline** (stages 1-7 in The NLP pipeline): sentences ->
    paper context -> mentions -> links -> relations -> qualifiers -> one
    **claim** row per relation per sentence: what *this paper* says.
    Negation and hedging are our own: PubTator and BioREx have neither. This
    layer is what makes Lacuna's graph different.
 6. **Combine** claims into **edges**: group by (subject ID, relation, object
    ID) and count papers, positive vs negative, hedged, first-seen date, papers
    per year, and the list of PMIDs.
 7. Add the **curated facts** from databases as a second layer of the same graph.
    Every edge has a `layer`:
    - `literature`: claims from papers, with counts, polarity, hedging and
      dates. What the field has *said*, and when.
    - `curated`: database facts (UMLS / MeSH hierarchy, ChEMBL drug -> target
      and drug -> disease with trial phase, GO annotations, Reactome, STRING).
      Mostly how biology is wired. Papers often state these only in passing, in
      introductions, which we don't count, so databases are the better source for them.
    - `trial`: ClinicalTrials.gov, drug -> disease with phase, status and date.
    - `dataset`: numbers from DepMap / GDSC / PRISM (05).

### How the layers are used
 - **Gap finders walk every layer to find paths.** Drug -inhibits-> T comes from
   ChEMBL, T -part of-> pathway P from Reactome, P -dysregulated in-> glioblastoma
   from papers. Mechanism completion and most ABC bridges need the curated layer.
 - **A gap is still a missing *literature* edge.** The curated layer also rules
   gaps out: if ChEMBL says the drug already reached a glioblastoma trial,
   it isn't a gap.
 - **Evidence counts only papers.** A database row isn't a paper, so mixing them
   would inflate the counts. Curated facts are weighted on their own terms (for
   example, ChEMBL's measured binding strength).
 - **Databases built from papers (CTD, SemMedDB) are not loaded into the graph.**
   They'd count the same papers twice. Use them for comparison only.
 - **Evaluation runs**: curated layer from a release before the cutoff. Many
   curated facts have no date of their own, so they can't be filtered row by row.
 - Experiment: run with and without the curated layer (`sources`). That measures
   how much the databases add.

### Correction
 - Only claims from Results, Conclusions and abstract result sentences count as
   evidence. Introductions repeat other people's claims, and counting them would
   inflate whatever is already famous.
 - A graph built from PubTator's annotations alone is a baseline in Evaluation.
   It shows how much our pipeline adds.

### Questions
 - do we do one document in one graph out, then combine all the graphs together into one?
   - Almost. One paper in -> one file of **claims** out (rows, not a graph).
     That splits nicely into Slurm array jobs. The combine step is then just
     "group by and count", which works because linking gave every paper the
     same IDs. "GBM" and "glioblastoma" both become C0017636, so they merge.
   - Keep the graph as a table (DuckDB over Parquet), not as many RDF files.
     The gap finders need counts and dates, and tables are much faster for
     that. Export to RDF/Turtle at the end if we want SPARQL or Oxigraph.

---

## 03-gaps.py
**INPUT** - Edge table + background (MeSH tree, drug -> target) + `scope.json`
**OUTPUT** - `candidates.jsonl` (every gap found, scored) and `shortlist.jsonl` (the top ~50 that go on to 04)

**Runs on** - Quartz CPU job (DuckDB queries), plus REALLMS for the reranker

### Algorithm
 - Run each **gap finder** over the edges, limited to the scope from 00:
   - **ABC bridge**: A is linked to B, and B is linked to C, but nobody has linked A to C.
   - **Sibling transfer**: a drug works in a sibling disease, but nobody has tried it in ours.
     Claims about a sibling's subtypes count toward the sibling (match on MeSH
     tree number prefix). Record the distance (sibling = 1, sibling's subtype = 2)
     and score distance 2 lower.
   - **Mechanism completion**: a drug hits target T, T matters in our disease, but nobody has tried the drug in our disease.
   - **Contested edge**: several papers say yes and several say no. Which experiment would settle it?
   - **Stalled lead**: good lab results, no trials, and nothing new in ~4 years.
   - **Rising edge**: a link whose paper count jumped recently but has no trial or review yet.
 - Give each candidate a score from graph numbers: how many supporting paths,
   papers per edge, results-section papers only, how recent.
   - **Hub penalty**: push down candidates whose middle term is something vague
     that connects to everything ("apoptosis", "inflammation"). Without this,
     the top of the list is all junk.
 - Narrow down, cheap to expensive:
   1. graph score -> keep ~500
   2. reranker (Qwen3-Reranker-8B on REALLMS) scores each candidate, written as
      one sentence, against the goal. This removes things that are true but off
      topic -> keep ~100
   3. diversity: group by drug class / target and take the best of each group,
      so the list isn't ten versions of one idea -> keep ~50

### Questions
 - Are there anything else other then sparql queries that we can use to find holes?
   - The gap finders above are just patterns, and SPARQL is one way to write
     them. Plain SQL in DuckDB is easier for these because they need counts and
     dates. Write them in SQL first.
   - Other ways to find holes:
     - **link prediction**: train a graph embedding (PyKEEN) and ask which
       missing edges it thinks should exist
     - **co-occurrence**: pairs mentioned together a lot but never in an actual claim
   - Use both of these as **baselines** in the evaluation. If our finders can't
     beat them, that's important to know.
   - Keep the chat LLM out of this step. If the LLM brainstorms gaps, we can't
     tell whether it found them in the graph or remembered them.
 - what does the actual output of this step look like
   - One line per candidate, for example:
     ```json
     {"id": "C-0412", "finder": "sibling_transfer",
      "s": "<drug X CUI>", "r": "treats", "o": "umls:C0017636",
      "why": [{"edge": "drug X -treats-> astrocytoma", "layer": "literature", "n_papers": 7, "n_negative": 0},
              {"edge": "astrocytoma shares a MeSH parent with glioblastoma", "layer": "curated"}],
      "missing": "no claim links drug X and glioblastoma before 2024-06-01",
      "features": {"n_paths": 2, "n_primary": 5, "hub_penalty": 0.1, "last_seen": "2023-11-02"},
      "score": 0.74, "sentence": "Drug X, used in astrocytoma, may treat glioblastoma"}
     ```
   - The `why` list is the evidence trail that 04 turns into a cited chain.

---

## 04-draft_ground.py
**INPUT** - `shortlist.jsonl` + edge table + paper store
**OUTPUT** - `hypotheses.jsonl`: each one a testable claim, the mechanism as a chain of steps, a citation for every step, assumptions, and a novelty result

**Runs on** - a Quartz CPU job calling REALLMS

### Algorithm
 1. **Gather evidence** for each candidate: start from the edges in its `why`
    list, pull the claims and quoted sentences behind them (by ID, not by
    wording), then add a search over the paper store (keyword + embedding,
    reranked) for anything the graph missed. Pass the sentence plus one
    neighbour on each side, not whole papers.
 2. **Draft** (qwen3.8-27b live, gpt-oss-120b in evaluation): one testable claim,
    the mechanism step by step, the assumptions, and the cheapest experiment
    that would test it.
 3. **Ground**: every step must cite a PMID and an exact sentence. Code checks
    that the sentence is really in that paper. A step that fails the check is
    cut or moved to "assumptions". It is never shown as evidence.
 4. **Novelty check**: search all of PubMed and ClinicalTrials.gov (not just our
    corpus) for the drug + disease pair, before `before_date`. Already studied
    -> drop it or mark it "known".

---

## 05-dry_test.py
**INPUT** - `hypotheses.jsonl`
**OUTPUT** - the same hypotheses with outside-data results added: support / against / not applicable, with the numbers

**Runs on** - Quartz CPU job; plain statistics, no LLM

### Algorithm
 - Download the datasets once per corpus, then query them by ID:
   - **DepMap** (CRISPR): is the target gene essential in this disease's cell
     lines, compared with other cell lines?
   - **GDSC / PRISM**: are this disease's cell lines sensitive to the drug?
   - Expression (TCGA): is the target actually expressed in patient tumors?
 - Each check returns a number and a plain verdict, which become part of the score.

### Correction
 - Swapped with the critic. This step is cheap code, so run it first: the critic
   then gets to see the dry-lab numbers, and we spend fewer LLM calls on weak
   hypotheses.
 - It's a score, not a pass/fail gate. Plenty of good hypotheses have no
   matching cell-line data, and "not applicable" is a normal result.
 - DepMap and GDSC only cover **cancer** cell lines. That's fine for brain
   tumors or AML. For Alzheimer's or stroke we'd need different sources (for
   example Open Targets for genetics, GTEx for expression).

---

## 06-critic.py
**INPUT** - Hypotheses with dry-test results
**OUTPUT** - Each hypothesis with a list of objections and a tournament rank

**Runs on** - a Quartz CPU job calling REALLMS

### Algorithm
 - The **critic** is an LLM from a different family than the drafter
   (gemma-4-31B-it live, gpt-oss-120b on high effort in evaluation), so it
   doesn't share the drafter's blind spots.
 - The critic gets **its own search, aimed at finding problems**. It never sees
   the drafter's evidence. It looks for:
   - negative and hedged edges for the same pair in the graph
   - failed, terminated or withdrawn trials (ClinicalTrials.gov)
   - safety problems (for example, the drug doesn't cross the blood-brain barrier)
 - It writes objections. Each one has a severity and, where possible, a citation
   (checked the same way as in 04).
 - **Tournament**: show an LLM judge two hypotheses at a time and ask which is
   more promising. Turn the wins and losses into an Elo rating. Comparing two at
   a time is more reliable than asking for a 1-10 score.

### Correction
 - The reranker doesn't belong here. It answers "is this relevant to the goal?",
   which is why it sits in 03. The critic answers "is this wrong?", and that
   needs an LLM that reads evidence against the hypothesis.

---

## 07-report.py
**INPUT** - Hypotheses with dry-test results, objections and ranks
**OUTPUT** - Ranked hypothesis cards (JSON) and one readable report per run (Markdown or HTML)

**Runs on** - anywhere

### Algorithm
 - Final score = evidence + novelty + testability + dry test + critic/tournament.
   Start with weights set by hand.
 - Write each card: the claim, the cited chain, assumptions, objections, the
   cheapest test, and the run settings (models, cutoff).
 - Write the cards as hypotheses for researchers, never as findings or medical advice.
 - The researcher marks each card **pursue / known / flawed**. Save the marks.
   Later they become training labels for the ranking in 03 (learned weights,
   then a fine-tuned reranker).

---

## Evaluation (runs beside the pipeline, not a step in it)

The main question: does a graph with counts, dates, negation and hedging make an
LLM a better hypothesis generator?

 - **Retrodiction**: freeze everything at **2024-06-01**, which is gpt-oss-120b's
   knowledge cutoff. Run Lacuna, then check whether its hypotheses show up in
   papers published after that date. We have over two years of papers to check against.
 - This is why every step takes `before_date`, and why 02 keeps a date on every claim.
 - **Hit** = no claim linking the pair before the cutoff, and at least one
   positive claim after it.
 - Compare against: co-occurrence, PyKEEN link prediction, an LLM with search
   but no graph, and Lacuna without negation/hedging.
 - **Masking experiment**: rerun with every drug, gene and disease name replaced
   by a code (DRUG_0412). This tells us whether the model is reasoning from the
   graph or just remembering.
 - Sanity check on the gap finders alone: run 00-03 and count the hits. If hits
   are near zero, change the field or the
   finder before trusting 04-07.

---

## Experiments (the whole system under different settings)

 - A **run config** is one JSON file holding the goal, `before_date` and every
   setting below. Many configs go to Quartz at once as a Slurm array (one task
   per config), or one after another.
 - Because of the paper pool, the expensive part (download + extract) happens
   once per paper. Most configs only re-select and re-count.

```json
{"run": "gbm_budget20k_up1_r2", "goal": "new treatment avenues for glioblastoma",
 "before_date": "2024-06-01",
 "sources": [
   {"name": "pubmed",         "release": "latest_before_cutoff"},
   {"name": "clinicaltrials", "release": "latest_before_cutoff"},
   {"name": "umls",           "release": "latest_before_cutoff"},
   {"name": "mesh",           "release": "latest_before_cutoff"},
   {"name": "chembl",         "release": "latest_before_cutoff"},
   {"name": "ncbi_gene",      "release": "latest_before_cutoff"}],
 "scope": {"paper_budget": 20000, "tree_up": 1, "max_rounds": 2, "expand_top_n": 200,
           "split_cap": 2000, "years_back": null, "sample_order": "random"},
 "graph": {"extractor": "pubtator", "min_papers": 2},
 "gaps":  {"finders": ["sibling", "mechanism", "abc"], "hub_penalty": 1.0, "shortlist": 50},
 "llm":   {"drafter": "gpt-oss-120b", "critic": "gpt-oss-120b", "masked": false},
 "ablations": [],
 "seed": 0}
```

 - **`sources`** is a list of source objects. Adding a data source means adding
   an object, and each one carries its own options (release, caps, filters).
   Only sources listed here may be read in that run, which also makes the run
   traceable.
 - **`ablations`** lists things that are normally always on, switched off to
   measure what they're worth. It's empty in real runs. Possible entries:
   `qualifiers` (negation/hedging), `section_weighting`, `critic`, `hub_penalty`.
   We can't claim negation and hedging help unless one run shows the result
   without them.

### Settings to vary

| setting | controls | try | what we expect |
| --- | --- | --- | --- |
| `sources` | which data sources a run may use | pubmed only; + clinicaltrials; + reactome/string | trials help stalled leads and novelty; pathway databases add middle terms for ABC bridges |
| `paper_budget` | amount of data | 5k, 20k, 50k, all | hits rise, then flatten; more noise and more hubs |
| `tree_up` | exploration along the tree | 0, 1, 2 | 1 beats 0 (siblings); 2 mostly adds noise |
| `max_rounds`, `expand_top_n` | exploration away from the disease, led by the papers | 0-3 rounds; 50 / 200 / 500 | helps ABC and mechanism gaps; flattens fast |
| `split_cap` | topic granularity (see below) | 9999, 2000, 500 | finer = more even coverage of small subtypes |
| `years_back` | how far back | all, 15, 10 | older papers help stalled leads but carry outdated claims |
| `sample_order` | which papers when a topic is capped (see below) | relevance, random, by year | relevance leans toward famous papers |
| `extractor` | who builds edges | pubtator, ours | the class comparison |
| `min_papers` | weakest edge kept | 1, 2, 3 | trades noise against missed leads |
| `hub_penalty` | how hard vague middle terms are pushed down (see below) | 0, 1, 2 | 0 should be clearly worse |
| `finders` | which gap finders | each alone | which ones actually produce hits |
| `ablations` | what is switched off | one at a time | `qualifiers` is **the paper's main question** |
| `masked`, models | the LLM half | | only on a few configs (expensive) |

### Three settings explained

**`split_cap`: when a topic gets broken into smaller topics.** Say "Glioma" has
40,000 papers and each topic is capped at 5,000. Without splitting, we get 5,000
glioma papers chosen by PubMed's ordering, and a rare subtype might get almost none.
With `split_cap: 5000`, any topic over 5,000 papers becomes one topic per MeSH
child (astrocytoma and the other glioma subtypes) plus a "glioma, rest" topic.
Each gets its own cap, and anything still too big splits again. A small
`split_cap` means many narrow topics; a large one means a few broad topics. It
also gets us past PubMed's limit of 9,999 results per query.

**`sample_order`: which papers we keep when a topic has more than its cap.**
 - `relevance` is what the puller does now (PubMed's "Best Match"). It favors
   well-known, often-cited papers, so the graph leans toward established findings.
 - `random` (with a fixed seed) gives a fair sample, and the curves are easiest
   to read.
 - `by year` takes an equal share from each year, so old history (needed for
   stalled leads) isn't crowded out by recent papers.
 - In evaluation runs, sampling happens only among pre-cutoff papers, so the
   budget isn't spent on papers the graph can't use.

**`hub_penalty`: stopping vague terms from making everything look connected.**
An ABC bridge says "Drug A is linked to B, B is linked to glioblastoma, so maybe
A and glioblastoma". If B is "apoptosis", almost every drug and every cancer is
linked to it, and every drug becomes a "bridge". That's thousands of useless
candidates. The penalty works like IDF in TF-IDF: a middle term is worth
`log(total nodes / nodes it connects to)`.
 - B connected to 5 of 10,000 nodes: `log(2000)`, about 7.6. Specific, a strong bridge.
 - B connected to 5,000 of 10,000: `log(2)`, about 0.7. Generic, nearly worthless.

The setting is how strongly that weight counts (0 = off). The same weight is
used in 00, so "apoptosis" never becomes a download topic either.

### What we measure (the y-axes)
 - **size**: papers, entities, edges, candidates
 - **cost**: CPU hours, LLM calls
 - **reachable**: share of answer-key pairs whose two ends are in the graph at
   all. This is the ceiling: what the run *could* have found. It shows best
   how much data and exploration we need.
 - **hit@k** and **lead time**, measured twice: on the 03 shortlist (no LLM)
   and on the final cards
 - **novelty rate**, **citation validity**

Example charts: hit@k against paper budget, one line per `tree_up`;
reachable against budget; hit@k against cost.

### Rules that keep comparisons fair
 - **One fixed answer key for every config.** Build it once from every
   post-cutoff paper on the target disease (PubTator relations over all of
   PubMed), not from each run's own corpus. Otherwise a bigger corpus also gets
   a bigger answer key, and its scores move for the wrong reason.
 - Change one setting at a time from a baseline config. Run a full grid only
   for the two that matter most (`paper_budget` x exploration).
 - Sweep widely on 00-03, which is cheap and uses no LLM. Run 04-07 on only a
   handful of configs.
 - Anything sampled at random: run 3 seeds and show the spread.

---

## Open questions to settle before coding

 - **Which disease first?** Glioblastoma uses the Brain corpus already on Slate
   and the test data in `test_data/`. AML moves faster (more hits), but it needs
   a new pull, which also tests 00-01 end to end.
 - **REALLMS**: can Quartz/Big Red 200 compute nodes reach it? Does it support
   tool calling (OpenAI `tools=`)? What are the rate limits?
 - **NCBI / PubTator from compute nodes**: if compute nodes have no internet,
   downloads run on a login node or the data transfer nodes.
 - **REALLMS throughput** decides how many sentences the LLM extractor can read.
   Measure it once tool calling is confirmed.
 - **Hit definition details**: any relation or the same relation? Minimum number
   of papers? Results sections only?
