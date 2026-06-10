# The Unofficial Guide: Project 1

> RAG over UT Dallas CS class and professor reviews. Ask the system stuff like "is CS 4337 hard with prof X" or "what do reviews say about exams in CS 3345" and get back a grounded answer with the source files cited.

---

## Domain

I chose UT Dallas CS class and professor reviews. The official UTD catalog (galaxy) tells you what a course is *supposed* to be (credit hours, prereqs, the description), but it will never tell you that a prof's exams are basically impossible without going to every lecture, that the curve is what's holding the GPA at a 3.0, or that the workload doubled after the prof swap. That stuff only lives on RMP, r/utdallas, and in Discords, and that's the gap this system fills.

I scoped the corpus to 11 UTD CS courses that show up a lot on the sub: 5 upper-div (CS 3345 data structures, CS 4337 programming languages, CS 4347 database systems, CS 4348 operating systems, CS 4349 advanced algorithms), 1 sophomore-ish (CS 3354 software engineering), 1 theory (CS 4383 theory of computation, which turned out to have basically no recent RMP coverage), and 4 grad (CS 6360 database design, CS 6363 algorithms, CS 6364 AI, CS 6375 machine learning).

---

## Document Sources

5 source types across 11 courses. Per-file manifest with URLs and fetched-at timestamps lives in `documents/sources.json` (35 files total). Files are written by `scripts/fetch_initial.py` so the corpus is re-runnable when reviews change.

| # | Source | Type | URL or file path |
|---|--------|------|-----------------|
| 1 | UTD Galaxy catalog, CS 3345 | catalog (HTML stripped to plain text) | https://catalog.utdallas.edu/now/undergraduate/courses/cs3345 -> `documents/catalog_cs3345.txt` |
| 2 | UTD Galaxy catalog, CS 4337 | catalog | https://catalog.utdallas.edu/now/undergraduate/courses/cs4337 -> `documents/catalog_cs4337.txt` |
| 3 | UTD Galaxy catalog, CS 4347 | catalog | https://catalog.utdallas.edu/now/undergraduate/courses/cs4347 -> `documents/catalog_cs4347.txt` |
| 4 | UTD Galaxy catalog, CS 4348 | catalog | https://catalog.utdallas.edu/now/undergraduate/courses/cs4348 -> `documents/catalog_cs4348.txt` |
| 5 | UTD Galaxy catalog, CS 6363 | catalog | https://catalog.utdallas.edu/now/undergraduate/courses/cs6363 -> `documents/catalog_cs6363.txt` |
| 6 | Rate My Professors, UTD school id 1273 | RMP reviews aggregated into one file, 590 ratings across 50 CS-dept profs (tagged class code, difficulty, clarity, helpfulness, would-take-again, date), fetched via RMP's public GraphQL | https://www.ratemyprofessors.com/school/1273 -> `documents/rmp_utd_cs.txt` |
| 7 | r/utdallas, CS 3345 search threads | reddit threads pasted in (anonymous reddit JSON returns 403, OAuth gated) | https://www.reddit.com/r/utdallas/search/?q=CS+3345&restrict_sr=1 -> `documents/reddit_cs3345.txt` |
| 8 | r/utdallas, CS 4337 search threads | reddit | https://www.reddit.com/r/utdallas/search/?q=CS+4337&restrict_sr=1 -> `documents/reddit_cs4337.txt` |
| 9 | r/utdallas, CS 4348 search threads | reddit | https://www.reddit.com/r/utdallas/search/?q=CS+4348&restrict_sr=1 -> `documents/reddit_cs4348.txt` |
| 10 | r/utdallas, CS 6363 search threads | reddit | https://www.reddit.com/r/utdallas/search/?q=CS+6363&restrict_sr=1 -> `documents/reddit_cs6363.txt` |
| 11 | utdgrades.com per-course pages | grade distributions (SPA, see Failure Case section, currently placeholder stubs that get skipped by ingest) | https://utdgrades.com/results/CS%203345 -> `documents/utdgrades_cs3345.txt` (and 10 more per course) |
| 12 | trends.utdnebula.com | grade trend dashboard (SPA, placeholder, same skip path as utdgrades) | https://trends.utdnebula.com/ -> `documents/trends_placeholder.txt` |

Notes on coverage:
- CS 4383 (theory of computation) came back with zero recent RMP reviews. it's a smaller course that just doesn't get rated much, so the system leans on reddit + catalog for that one.
- Discords are off the table for this version because most UTD Discords need invite + verification flows i can't automate.

---

## Chunking Strategy

**Chunk size:** ~400 characters (roughly 80-100 tokens) for catalog and reddit. Per-review for RMP (no fixed character window).

**Overlap:** ~50 characters on the sliding window for catalog and reddit. No overlap for RMP because the review boundary is the chunk boundary.

**Why these choices fit your documents:**

Most of what i'm working with is short, opinion-dense text. RMP reviews are usually 1-3 sentences ("exams are nothing like the homework, curve saved me"). Reddit comments are a paragraph or two. The official catalog entries are short structured blocks. If i went with one giant chunker at 1000+ chars i'd jam multiple unrelated RMP reviews into one chunk, or merge a reddit thread's top comment with replies that are off on a tangent, and the embedding gets diluted: a query like "what do students say about this prof's exams" stops matching because the chunk is about five different things at once.

400 chars + 50 overlap on the sliding window is small enough to keep individual reddit comments roughly intact but big enough to carry real meaning (not just a fragment like "exams are heavily"). The overlap is there so that if a single key sentence lands right on the cut (e.g., "the midterm is curved"), it still shows up in one of the two neighboring chunks. Window edges are aligned to paragraph, sentence, or word boundaries so chunks don't start or end mid-word.

**Why per-review for RMP:** RMP reviews are already 200-500 char self-contained units with the prof name, class code, and difficulty / clarity / helpfulness / would-take-again tags. A blind 400-char window would either split a single review in half or merge two unrelated reviews into one chunk. Per-review preserves "who said what about which class" naturally, and the difficulty / class-code metadata can be attached to each chunk so the retriever can filter on course code later.

**Preprocessing before chunking:**
- catalog: leftover HTML / nav text sliced out by regex matching the "CS NNNN ... semester credit hours" line. everything before that line is page chrome.
- RMP: the `# Rate My Professors UTD CS faculty (school id 1273)` header is stripped so it doesn't end up as a chunk.
- reddit: UI cruft removed (`Upvote`, `Downvote`, `Reply`, `5y ago`, `u/<username> avatar`, the placeholder body markers from when threads were copy-pasted). cp1252 encoding fallback is used because smart quotes paste in weird on Windows.

**Final chunk count:** 810 chunks total. 556 RMP (one per review), 245 reddit (sliding window), 18 catalog (sliding window over 16 cleaned course-description blocks).

### Sample chunks

**Sample 1, source: rmp_utd_cs.txt (CS 4347, Prof Kamran Khan, difficulty=5, would_take_again=None, 205 chars)**

Review of Prof Kamran Khan for CS 4347: ik it says I have an A but please dont take him Ive had him for CS2 but he got 10x worse and his curve is essentially non existent now better off taking someone else

**Sample 2, source: rmp_utd_cs.txt (CS 3354, Prof Priya Narayanasami, difficulty=4, would_take_again=None, 392 chars)**

Review of Prof Priya Narayanasami for CS 3354: The content of the course itself isn't exciting, but she takes it to a whole new level of boring and draining with monotonous power points on top of mind numbing homework every week. Lots of work, little learning. A project is assigned at the beginning of the semester, and all the work she assigns gets in the way working on the project. Avoid.

**Sample 3, source: reddit_cs3345.txt (CS 3345, r/utdallas, 366 chars)**

r/utdallas discussion of CS 3345: took cs3345 like 2 years ago and got a D. was still able to take classes that need 3345 and had no problem with those. then i tried registering for 4384 like a month ago during registration day and nope i needed a C or better in 3345. but then i emailed my advisor and he was able to override it, said i could still enroll for 4384.

**Sample 4, source: reddit_cs4348.txt (CS 4348, r/utdallas, 430 chars)**

r/utdallas discussion of CS 4348: to make sure students understand the concepts. As long as you pay attention in the class, his class should be a breeze. (Projects can be hard, but he's understanding and open to extending deadlines).

---

Go to utdallas
Own_Stage_758

What is the passing grade for (OS) CS/SE 4348?
Question: Academics
What is the passing grade for (OS) CS/SE 4348 is a C or above or D?

**Sample 5, source: catalog_cs6363.txt (CS 6363, UTD catalog, 417 chars)**

UTD official catalog description for CS 6363: CS 6363 Design and Analysis of Computer Algorithms (3 semester credit hours) The study of efficient algorithms for various computational problems. Algorithm design techniques. Sorting, manipulation of data structures, graphs, matrix multiplication, and pattern matching. Complexity of algorithms, lower bounds, NP completeness. Prerequisites: CS 5333 and CS 5343. (3-0) S

---

## Embedding Model

**Model used:** `all-MiniLM-L6-v2` via sentence-transformers, running locally.

Why this one: no API key, no rate limits, fast on CPU (810 chunks embedded in 12.4 sec on this machine). 256 token context window, 384-dim output, cosine-normalized. Good enough for short opinion-dense text like RMP reviews.

**Production tradeoff reflection:**

If i were deploying this for real UTD students and cost wasn't the limit, here's what i'd actually weigh:

- **Domain accuracy:** MiniLM is generic. It doesn't know that "Dr K" and "Karra" are the same person, or that "data structures" and "CS 3345" are the same thing. A model fine-tuned on UTD-specific text (course codes, prof nicknames, building names) would retrieve way better. Worth the cost if i had the training data.
- **Context length:** MiniLM caps at 256 tokens. For longer chunks (whole reddit threads, syllabi PDFs), something like `bge-large-en-v1.5` or OpenAI's `text-embedding-3-large` handles bigger windows without truncating.
- **Cost vs. latency:** Local MiniLM is free and fast once loaded. A hosted embedding API (OpenAI, Voyage) would have a faster cold start and be more accurate, but i'd be paying per query. For a student project, local wins. For production with thousands of queries a day the API cost might be worth the quality bump.
- **Multilingual:** Not needed here. UTD reviews are basically all English. Wouldn't pay for it.

### Retrieval test results

3 of the 5 eval-plan queries run through `scripts/retrieve.py`. Distance scores are ChromaDB cosine distance (lower = closer).

**Query A (Q1): "What do students say about exams in CS 3345, and how do reviews differ by professor?"**

| # | distance | source | course | prof |
|---|----------|--------|--------|------|
| 1 | 0.366 | rmp | CS 3345 | Omar Hamdy |
| 2 | 0.386 | rmp | CS 3345 | Omar Hamdy |
| 3 | 0.388 | rmp | CS 3345 | Ziaullah Khan |
| 4 | 0.388 | rmp | CS 3345 | Yi Zhao |
| 5 | 0.406 | rmp | CS 3345 | Beiyu Lin |

Why these are relevant: all 5 came from RMP, all 5 are tagged CS 3345, and they cover 4 different professors. The question asks how reviews differ by prof and the retrieval handed back exactly that mix. The course-code metadata filter forced the CS 3345 scope so reviews from other courses with stronger "exams" word overlap couldn't crowd these out. Distances 0.37 to 0.41 are solidly under the 0.5 threshold i set in planning.md.

**Query B (Q2): "Which professor teaches CS 4337 most often, and what's the general consensus on the workload?"**

| # | distance | source | course | prof |
|---|----------|--------|--------|------|
| 1 | 0.379 | rmp | CS 4337 | Yi Zhao |
| 2 | 0.387 | rmp | CS 4337 | Elmer Salazar |
| 3 | 0.406 | reddit | CS 4337 | (n/a) |
| 4 | 0.412 | reddit | CS 4337 | (n/a) |
| 5 | 0.413 | rmp | CS 4337 | Gity Karami |

Why these are relevant: mix of RMP and reddit, all 5 tagged CS 4337, three different profs covered. The reddit chunks specifically discuss workload and prof comparisons (one of them says "found the class to be very doable"), so the workload half of the question gets real signal from reddit while the prof half gets it from RMP. Course filter forced the right course again, then semantic ranking picked the workload-focused chunks.

**Query C (Q4): "How do the top-reviewed CS 4337 professors compare on difficulty and teaching style?"**

| # | distance | source | course | prof |
|---|----------|--------|--------|------|
| 1 | 0.403 | rmp | CS 4337 | Elmer Salazar |
| 2 | 0.416 | rmp | CS 4337 | Yi Zhao |
| 3 | 0.417 | rmp | CS 4337 | Chris Davis |
| 4 | 0.418 | rmp | CS 4337 | Elmer Salazar |
| 5 | 0.427 | reddit | CS 4337 | (n/a) |

---

## Grounded Generation

**System prompt grounding instruction:**

The system prompt in `scripts/query.py` tells the LLM to answer using ONLY the provided context excerpts and to refuse with the exact string `"I don't have enough information on that based on the documents I have access to."` if the context doesn't cover the question. It also tells the model to cite excerpts inline like `[1]` or `[3]`, and to surface disagreement when excerpts disagree (e.g. "reviews are split: [1] says X, [4] says Y"). Temperature is set to 0.0 so the LLM behavior is deterministic across runs.

LLM: Groq `llama-3.3-70b-versatile`.

**Two-layer refusal:** there's prompt-side refusal (the LLM follows the instruction above) PLUS a pipeline-side refusal in `ask()` that short-circuits before the LLM is even called if the top retrieval distance is above 0.85. That way a wildly off-topic query (like "what's a good restaurant near campus") can't burn an LLM call at all, the pipeline just returns the refusal text directly.

**How source attribution is surfaced in the response:**

Every retrieved chunk has a `source_file` field attached at chunk time (set in `chunk.py`). After the LLM response comes back, `query.py` calls `_summarize_sources()` which dedupes the retrieved chunks by source file and returns a list of `{filename, url, chunks_used}` entries. The UI (Gradio app) renders that list as a separate `Retrieved from` box under the answer. So even if the LLM forgets to cite, the source list is still there because it's programmatic, not LLM-generated. If the LLM does refuse, the source list gets cleared so the UI doesn't suggest the answer was drawn from any of the retrieved chunks.

### Example responses

**Example 1 (grounded, Q1):**

> Students say that exams in CS 3345 can be challenging, with some reviewers finding them difficult [2], "the worst exams I've ever taken" [3], or "HARD" [4]. However, others found that understanding the concepts thoroughly and paying attention to lectures can be enough to do well [1] or "very well" [5]. Reviews differ by professor: Prof Omar Hamdy's exams are considered "not difficult but definitely time consuming" [1] and curved [1], but another reviewer found them "too difficult" [2]. Prof Ziaullah Khan's exams are described as having "ambiguous questions" [3], while Prof Yi Zhao's tests are "HARD" and do not curve [4]. Prof Beiyu Lin's exams are considered manageable if one pays attention in class or puts in effort outside of it [5].
>
> Sources:
> - rmp_utd_cs.txt (excerpts 1, 2, 3, 4, 5), https://www.ratemyprofessors.com/school/1273

**Example 2 (grounded, Q4):**

> Reviews are split:
> - Prof Elmer Salazar's class is described as "brutal" and "absolutely brutal" with "insanely difficult projects" [1, 4], but another review rates his class as a 5/10 in difficulty [5].
> - Prof Yi Zhao's class is "definitely difficult" [2].
> - Prof Chris Davis's class is described as "pretty easy" as long as you study enough for the exams [3].
>
> Teaching styles also vary:
> - Prof Elmer Salazar's teaching style is described as "strange" [4] and "experimental" [1].
> - Prof Yi Zhao is a "solid lecturer" [2].
> - Prof Chris Davis is "pretty disorganized" but "very caring" [3].
>
> Sources:
> - rmp_utd_cs.txt (excerpts 1, 2, 3, 4), https://www.ratemyprofessors.com/school/1273
> - reddit_cs4337.txt (excerpts 5), https://www.reddit.com/r/utdallas/search/?q=CS+4337&restrict_sr=1

**Example 3 (out-of-scope refusal, Q5):**

> Query: "What's the best off-campus restaurant near UTD?"
>
> Response: "I don't have enough information on that based on the documents I have access to."
>
> Sources: (none, the LLM refused on prompt-side)

Note on which refusal layer fired: for Q5 the top retrieval distance was 0.591, which is BELOW the 0.85 pipeline-side threshold, so the pipeline did call the LLM. The LLM itself then refused based on the system prompt's grounding instruction (no chunks in context were about restaurants). `_summarize_sources` detects the refusal text in the response and clears the source list before returning. So this is a prompt-side refusal, not a pipeline-side short-circuit.

---

## Query Interface

The interface is a Gradio web UI (`scripts/app.py`), launched at `http://localhost:7860`. Three text fields plus a debug accordion:

- **Input, "Your question"** (textbox, 2 lines, with a placeholder example). Submit on Enter or by clicking the "Ask" button.
- **Output, "Answer"** (textbox, 10 lines). The LLM response, with inline `[N]` citations referring to context excerpts.
- **Output, "Retrieved from"** (textbox, 4 lines). The deduped source list (filename + URL + which excerpt numbers came from that file). Shows `(no sources, system declined to answer)` on a refusal.
- **Output, "Retrieval debug"** (collapsible accordion). Shows the top-5 chunks' distance, course, and prof so retrieval quality can be eyeballed.
- A `gr.Examples` block lets the user click one of the eval-plan questions to populate the input box.

**Sample interaction transcript:**

```
Your question: What do students say about exams in CS 3345, and how do reviews differ by professor?

Answer: Students say that exams in CS 3345 can be challenging, with some reviewers
finding them difficult [2], "the worst exams I've ever taken" [3], or "HARD" [4].
However, others found that understanding the concepts thoroughly and paying attention
to lectures can be enough to do well [1] or "very well" [5]. Reviews differ by
professor: Prof Omar Hamdy's exams are considered "not difficult but definitely time
consuming" [1] and curved [1], but another reviewer found them "too difficult" [2].
Prof Ziaullah Khan's exams are described as having "ambiguous questions" [3], while
Prof Yi Zhao's tests are "HARD" and do not curve [4]. Prof Beiyu Lin's exams are
considered manageable if one pays attention in class or puts in effort outside of it [5].

Retrieved from:
• rmp_utd_cs.txt  (#1, #2, #3, #4, #5)
   https://www.ratemyprofessors.com/school/1273

Retrieval debug:
[1] distance=0.366  course=CS 3345 prof=Omar Hamdy
[2] distance=0.386  course=CS 3345 prof=Omar Hamdy
[3] distance=0.388  course=CS 3345 prof=Ziaullah Khan
[4] distance=0.388  course=CS 3345 prof=Yi Zhao
[5] distance=0.406  course=CS 3345 prof=Beiyu Lin
```

---

## Evaluation Report

| # | Question | Expected answer | System response (summarized) | Retrieval quality | Response accuracy |
|---|----------|-----------------|------------------------------|-------------------|-------------------|
| 1 | What do students say about exams in CS 3345, and how do reviews differ by professor? | Summary of recurring exam themes (curve, exam style, prof-by-prof differences) cited to RMP / reddit. | Got 5/5 CS 3345 RMP chunks across 4 profs (Omar Hamdy, Ziaullah Khan, Yi Zhao, Beiyu Lin), surfaced "split reviews" framing, called out Hamdy curve, Zhao no-curve, Khan ambiguous questions. | Relevant | Accurate |
| 2 | Which professor teaches CS 4337 most often, and what's the general consensus on the workload? | Most-reviewed CS 4337 prof at query time plus a workload summary, cited. | 5/5 CS 4337 chunks (3 profs + 2 reddit), correctly summarized workload as "moderate, projects time-consuming but doable", but the answer honestly noted "it's not clear which prof teaches CS 4337 most often" because RMP review counts aren't a teaches-most-often signal. | Relevant | Partially accurate |
| 3 | What's the average GPA in CS 3345 over the last 3 semesters? | A specific number / range from utdgrades or trends.utdnebula. | Refused: "I don't have enough information on that based on the documents I have access to." | Off-target | Inaccurate (no GPA data in corpus, see Failure Case below) |
| 4 | How do the top-reviewed CS 4337 professors compare on difficulty and teaching style? | Side-by-side comparison (difficulty + teaching style) of the most-reviewed CS 4337 profs at query time. | Compared Salazar ("brutal", "experimental"), Zhao ("definitely difficult", "solid lecturer"), Davis ("pretty easy", "disorganized but caring") with inline citations and a side-by-side structure. | Relevant | Accurate |
| 5 | What's the best off-campus restaurant near UTD? | System should refuse (out of domain). | Refused with the exact required string. | Off-target (intentionally) | Accurate (correctly refused) |

**Retrieval quality:** Relevant / Partially relevant / Off-target
**Response accuracy:** Accurate / Partially accurate / Inaccurate

---

## Failure Case Analysis

**Question that failed:** Q3, "What's the average GPA in CS 3345 over the last 3 semesters?"

**What the system returned:** "I don't have enough information on that based on the documents I have access to."

**Root cause (tied to a specific pipeline stage):** the **document collection stage** never actually populated the utdgrades source files. Each `documents/utdgrades_cs<num>.txt` is still the placeholder stub the M1 fetcher dropped in. utdgrades.com is a Next.js SPA that returns a JS shell to plain GETs, the actual grade tables only load client-side after JS execution, so `requests.get` got nothing useful. `scripts/ingest.py` detects these stubs via the `is_empty_placeholder()` check and skips them entirely, so zero utdgrades chunks ever made it into the ChromaDB index. The retriever for Q3 then fell back to course-filtered reddit + RMP chunks (all distances above 0.47), none of which contain actual A/B/C/D/F counts or GPA numbers. The LLM correctly refused because no real grade data sat in the context excerpts.

So the failure isn't a retrieval bug or a generation bug, it's a missing-data bug from the ingestion stage: the source the answer needed never made it through the pipeline.

**What you would change to fix it:** two options. (1) manually paste the per-section A/B/C/D/F tables from each utdgrades.com page into the placeholder files and re-run `embed.py`, which keeps the pipeline simple but breaks the "re-runnable corpus" story. (2) build a small headless-browser scraper (playwright) that drives the SPA and exports the grade tables to plain text. Option 2 keeps the dynamic-refresh story but adds a heavy browser dependency to the project. For now i'd go with option 1 because the corpus only has 11 courses, but for a real production version with hundreds of courses option 2 is the only sustainable path.

---

## Spec Reflection

**One way the spec helped you during implementation:**

The Chunking Strategy section in planning.md made me think about RMP reviews and reddit threads having very different structures BEFORE i wrote any chunking code. That's what drove the per-review chunking decision for RMP instead of a uniform 400-char window. If i'd skipped the spec i would have written one chunker for everything and almost certainly gotten worse retrieval, specifically i would have ended up with chunks that merged two unrelated RMP reviews together (so the prof / class metadata would be wrong on half the chunk), and queries asking about a specific prof would have pulled back garbage. The act of writing the spec section forced me to look at the raw documents first and notice the structural difference, which is exactly what the spec is for.

**One way your implementation diverged from the spec, and why:**

planning.md originally said 400 chars + 50 overlap uniformly across all sources. In M3 i ended up splitting the chunker per source (per-review for RMP, sliding window for catalog and reddit). I updated planning.md after the fact with an "Update after M3 implementation" block so the spec stays honest. The other divergence was the course-code metadata filter in `retrieve()`, which wasn't in the original Retrieval Approach section. I needed it once i saw the first retrieval pass returning wrong-course chunks: a query like "what's the workload for CS 4337" was returning zero CS 4337 chunks in top-5 because MiniLM weighted "workload" / "exams" tokens more than the literal "CS 4337" tokens, and reviews from CS 4348 / 3345 with stronger word overlap won out. Added the filter, updated planning.md with the "Update after M4" block, moved on.

---

## AI Usage

**Instance 1: fetcher iteration on RMP coverage**

- *What I gave the AI:* my Documents section from planning.md, plus the observation that ranking RMP profs by `numRatings` was giving me historical heavyweights (Dollinger, 219 reviews) who don't currently teach my 11 target courses. Asked for a fetcher that paginates UTD CS-dept profs and filters by class tag + recency instead of by raw review count.
- *What it produced:* a `scripts/fetch_initial.py` that paginates the `newSearch.teachers` GraphQL query for school 1273, filters to the "Computer Science" department, fetches per-prof ratings, normalizes class tags (so SE 3345 == CS 3345 cross-listings get counted), and applies a `date >= 2023-01-01` recency filter.
- *What I changed or overrode:* the first pass had a 600 second wall-clock budget cap that ran out after only 32 prof detail fetches, which dropped CS 4337 coverage. Made the AI add a sort-by-numRatings-descending step BEFORE the date filter so the highest-volume profs get inspected first, then bumped the budget to 1800 sec and the per-course prof cap from 20 to 50 so grad-only profs (Huynh, Raichel for CS 6363) survived the trim. After that change CS 6363 coverage went from 0 to 22 ratings.

**Instance 2: retrieval debugging (course-code filter)**

- *What I gave the AI:* the actual debug output from running my 3 eval queries through the first version of `retrieve()`. Q2 ("CS 4337 workload") was returning ZERO chunks tagged CS 4337 in top-5, instead surfacing CS 4348 and CS 3345 chunks that had stronger raw word overlap on "workload" and "exams". Pasted the chunk-by-chunk distance + course metadata so the AI could see exactly what was failing.
- *What it produced:* a course-code extraction step that pulls "CS NNNN" patterns (and common course-name keywords like "data structures", "machine learning") out of the query, applies a ChromaDB `where={"course": ...}` filter on the chunk metadata, and falls back to unfiltered semantic search if the filter starves the result set below k=5.
- *What I changed or overrode:* the initial version only handled literal "CS NNNN" mentions in the query string. Made the AI extend it with a keyword map so queries like "is data structures hard" still apply the filter for CS 3345 even without the course code spelled out. I also explicitly kept the unfiltered fallback path even after the keyword map landed, because aggressive filtering on a deliberately out-of-scope question (like Q5) could starve retrieval below k=5 and produce a confusing partial answer instead of letting the LLM refuse cleanly.
