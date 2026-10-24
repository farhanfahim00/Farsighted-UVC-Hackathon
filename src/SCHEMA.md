# Evidence index schema

Produced by `python src/indexer.py` into `build/` (this file is copied there as `build/SCHEMA.md`).
All files are UTF-8; `*.jsonl` files hold one JSON object per line, in document order: files in
`regulations/index.json` order (base, then latest), then the sub-goal PDFs per `subgoal/<case>/files.json`.
Same input gives byte-identical output.

## Identifiers

| id | format | example |
|---|---|---|
| document | `<regulation>\|<version>\|<role>\|<file>` | `UN R150\|1.2.1\|base\|UN_R150_base.pdf` |
| paragraph | `<document id>\|<paragraph label>#<n>` | `UN R150\|1.2.1\|base\|UN_R150_base.pdf\|Annex 6 Part 4 1#1` |
| line | `<document id>:p<page>:l<n>` | `UN R150\|1.2.1\|base\|UN_R150_base.pdf:p7:l12` |
| table | `<document id>\|p<page>\|t<n>` | `UN R48\|9.3.0\|base\|UN_R48_base.pdf\|p92\|t1` |
| definition | `<paragraph id>\|def` | `UN R48\|9.3.0\|base\|UN_R48_base.pdf\|2.7.1#1\|def` |

* `role` is `base`, `latest`, `subgoal_old` or `subgoal_new`.
* `file` is the PDF's file name for regulation files (`UN_R150_base.pdf`) and `subgoal/<case>/<name>` for sub-goal files
  (`subgoal/dev/old.pdf`). For sub-goal files, regulation and version are read from the PDF's running header.
* `#<n>` numbers repeated labels within one document (`#1` first). Labels repeat in reprinted communication forms and
  when the regulation itself prints a number twice. The run fails if any id repeats.
* `page` is always the physical page index + 1 in that exact file. This is the page the scorer expects.
* Line `n` is the 1-based position among the visible lines of the page, in pymupdf reading order, running header included.

## paragraphs.jsonl

One record per numbered paragraph, per **title entry** and per **table entry**.

| field | type | meaning |
|---|---|---|
| `id` | str | paragraph id (see above) |
| `doc_id` | str | document id |
| `regulation`, `version`, `role`, `file` | str | source document |
| `page`, `page_end` | int | first / last page the paragraph's lines are on |
| `paragraph` | str | label in expert style: `3.3.1.2`, `Annex 6 Part 4 1`, `Annex 3 Appendix 1 2.1`, `5.1.4.4.1. Table 3`, `Introduction 5`. Matches `tools/score_local.py:_paragraph_key` after normalisation |
| `number` | str | own number without context prefix: `1`, `5.1.4.4.1. Table 3`, `""` for title entries |
| `kind` | str | `paragraph`, `title` (an annex / appendix / part / section heading plus its unnumbered lead-in text, labelled with the prefix alone, e.g. `Annex 6 Part 4`) or `table` (a table caption plus the cell lines that follow it) |
| `section` | str\|null | main-body section that restarts the numbering (`Introduction`, `I`, `B`), else null |
| `annex`, `appendix`, `part` | str\|null | context ids (`6`, `1`, `4`). `appendix` is `""` for an unnumbered appendix. `part` is only set inside annexes |
| `text` | str | clean paragraph text, lines joined (a trailing hyphen glues words). The number is excluded; running headers and footnotes are removed |
| `shall_count` | int | occurrences of "shall" (= `obligation_signals.shall`) |
| `has_table` | bool | the paragraph contains table-like lines (small or indented cells) or owns a table entry |
| `table_ids` | [str] | ids in `tables.jsonl` owned by this paragraph (or extracted under this table entry) |
| `source_line_ids` | [str] | the exact lines (ids in `pages.jsonl`) the record was built from, in order: the number line first |
| `footnote_context_ids` | [str] | footnote lines on the record's pages (`page`..`page_end`). Not part of `text`, but often needed to read it |
| `title` | str\|null | the paragraph's own heading, see `title_source` |
| `title_source` | str\|null | `heading_line`: a short first line with no closing punctuation or obligation word, followed by a clear vertical gap or by nothing (`6.14. Rear retro-reflector, non-triangular`). `inline_label`: `In width: that point ...` gives `In width`. `bold_heading`: a bold `1. Scope` merged with `1. This Regulation ...`. `context_heading`: title entries, from the annex / appendix / part heading. `caption`: table entries, from the caption text |
| `breadcrumb` | [str] | ancestors from the top down, excluding the record itself: context headings first (`Annex 6 Environmental testing`, `Part 4 Resistance to corrosion`), then numeric ancestors as `<number> <title>` or `<number>` when untitled. Example for `Annex 6 Part 4 1.1`: `["Annex 6 Environmental testing", "Part 4 Resistance to corrosion", "1"]`. A table entry's breadcrumb ends with its owning paragraph |
| `parent` | str\|null | label of the parent (when resolved), else the label the parent was expected to have |
| `parent_id` | str\|null | id of the parent record, only when the match is unique (see `parent_status`) |
| `parent_status` | str | `ok`: the immediate parent (`3.3.1` for `3.3.1.2`; the title entry for a top-level annex paragraph; the enclosing title entry for a part / appendix title entry). `skipped_level`: the immediate parent does not exist, so this is the nearest existing ancestor. `table_owner`: a table entry linked to the paragraph that owns the table. `ambiguous`: several candidates, none chosen (see `parent_candidates`). `not_found`: no candidate. `root`: a top-level paragraph of the main body or a top-level title entry |
| `parent_candidates` | [str] | only when `parent_status` is `ambiguous` |
| `parent_chain` | [str] | ids from the parent up to the root, through unique links only |
| `children_ids` | [str] | records whose `parent_id` is this record, in document order |
| `obligation_signals` | {str: int} | counts of `shall`, `shall_not`, `must`, `must_not`, `is_required` ("is/are required"), `need_not`, `may`. `shall_not` is a subset of `shall`, and `must_not` of `must` |
| `has_obligation` | bool | `shall` + `must` + `is_required` > 0. "need not" and "may" are permissions |
| `heading_value_item` | bool | an R48-style label/value item whose obligation is implicit: `6.14.2. Number` followed by `Two, ...`, or `6.14.4.2. In height: Above the ground ...`. Set for `inline_label` titles of at most 5 words, and for `heading_line` titles of at most 5 words followed by body text without its own obligation word |
| `inherits_obligation` | bool | no own obligation, but: the parent has one (`parent_obligation`), or the parent text ends with `:` and introduces a list (`parent_introduces_list`), or the parent itself inherits one (`ancestor_inherits`), or the nearest titled ancestor has one (`titled_ancestor_obligation`) |
| `inherits_obligation_from` | str\|null | id of the record the obligation comes from |
| `inherits_obligation_reason` | str\|null | one of the four reasons above |
| `condition_signals` | [{signal, pos}] | conditions in `text`, with `pos` the character offset. Signals: `if`, `when` (also "whenever"), `where`, `unless`, `except`, `provided_that`, `in_the_case_of`, `if_fitted` ("if (so) fitted / installed"), `if_equipped`, `either_or` ("either" followed by "or") |
| `inherited_condition_ids` | [str] | ids in `parent_chain` that have condition signals themselves |
| `cross_refs` | [object] | references in `text`, see below |
| `annex_kind` | str\|null | for annex records: `communication_form` (fill-in forms: "Communication", "Notification", "Information document", "Concerning:", "issued by", mostly dotted leaders), `marking_example` (approval mark examples) or `content`. Null in the main body. A flag only: nothing is dropped |

### cross_refs entries

| field | meaning |
|---|---|
| `kind` | `paragraph` ("paragraph(s) X", "para. X", "§ X"), `annex` ("Annex N", "Annex N, Appendix M", "Appendix M to Annex N") or `table` ("Table N") |
| `ref` | the referenced number / id: `5.1.3.2.2`, `6`, `3 Appendix 1`, `A6-1` |
| `raw` | the matched text; `pos` is its character offset in `text` |
| `qualifier` | the text after the reference that sets its scope, e.g. `of this Regulation`, `of this annex`, `of Annex 3`, `of Part 1 of Annex 4`, `of UN Regulation No`. Null when there is none. A qualifier naming a Part / Appendix / Annex that cannot be parsed leaves the reference `unresolved` |
| `status` | `resolved` (exactly one target), `ambiguous` (several; `candidates` lists them), `unresolved` (none) or `external` (another regulation or standard) |
| `target_id` | the target record's id when `resolved` |
| `scope` | where the target was looked up: `main_body`, `same_context` (inside an annex, an unqualified "paragraph 2.1" means that annex's own paragraph when it has one, which is the drafting convention), `same_annex`, `named_annex`, `document` |

## pages.jsonl

One record per page of every indexed PDF.

| field | type | meaning |
|---|---|---|
| `doc_id`, `regulation`, `version`, `role`, `file`, `page` | | the page |
| `width`, `height` | float | page size in pt |
| `lines` | [object] | every visible text line on the page, in reading order |

Line objects:

| field | type | meaning |
|---|---|---|
| `id` | str | line id (`<doc id>:p<page>:l<n>`) |
| `text` | str | the line's text, whitespace collapsed |
| `bbox` | [x0, y0, x1, y1] | in pt, origin top-left |
| `size` | float | dominant font size of the line |
| `bold` | bool | the first visible span is bold |
| `kind` | str | `running_header`, `boilerplate`, `heading` (annex / part / section heading), `text`, `caption` (table caption), `skip` (footnote) or `toc` (table-of-contents entry) |
| `running_header` | bool | "UN R150, version 1.2.1 \| page 7 of 54" |
| `boilerplate` | bool | Certivity's page-1 block |
| `footnote_candidate` | bool | a footnote line: a small line starting with a superscript digit, `1/` or `*`, or one of its continuation lines |
| `assigned_to_paragraph` | bool | the line is in some record's `source_line_ids` |
| `paragraph_id` | str\|null | that record's id |

Unassigned content lines are typically form text before an annex's first paragraph that is not part of a heading,
main-body Part headings, and table-of-contents entries. `manifest.json` counts them per file.

## tables.jsonl

One record per table found by pymupdf `find_tables(strategy="lines", use_layout=False)` (ruling lines only; no layout model). It runs on pages with a table caption, and on pages with
table-like paragraph lines that also carry vector drawings.

| field | type | meaning |
|---|---|---|
| `id` | str | table id |
| `doc_id`, `regulation`, `version`, `role`, `file`, `page` | | where |
| `bbox` | [x0, y0, x1, y1]\|null | table frame |
| `n_rows`, `n_cols` | int | shape |
| `caption`, `caption_line_id` | str\|null | the nearest unused caption line just above (or inside) the table |
| `cells` | [[str\|null]] | rows of cell texts. `null` is a merged or missing cell |
| `owner_paragraph_id` | str\|null | the paragraph the table belongs to |
| `owner_status` | str | `caption`: the caption's table entry gives the owner. `lines_in_table`: all lines inside the table belong to one paragraph. `ambiguous`: lines of several paragraphs, none chosen (see `owner_candidates`). `unassigned`: none |
| `owner_candidates` | [str] | only filled when `ambiguous` |
| `table_entry_id` | str\|null | the `kind: table` record of the caption, if any |
| `source_line_ids` | [str] | lines whose centre lies inside the table frame |
| `risk_flags` | [str] | `merged_or_missing_cells`, `empty_cells`, `detached_minus_sign` (a cell that is only "-" or "- 5"), `degenerate_shape` (< 2 rows or columns), `may_continue_on_next_page` / `may_continue_from_previous_page` (frame within 70 pt of the page edge), `no_caption`, `lines_from_other_paragraphs` (caption owner differs from the lines inside), `extraction_failed: <error>` |

## definitions.jsonl

Every paragraph below a paragraph titled (or starting with) "Definitions" / "Terms and definitions" (usually `2.`),
in any context. Rows are kept even when no term is recognised.

| field | type | meaning |
|---|---|---|
| `id` | str | `<paragraph id>\|def` |
| `term` | str\|null | the defined term |
| `term_method` | str\|null | `quoted` (`"Vehicle type" means ...`), `means` (`Lamp means ...`), `colon` (`Lamp: ...`) or null |
| `abbreviation` | str\|null | `"Light emitting diode" (LED) means ...` gives `LED` |
| `paragraph_id`, `paragraph` | str | the defining paragraph |
| `definitions_section_id` | str | the "Definitions" heading paragraph |
| `regulation`, `version`, `role`, `file`, `page`, `page_end`, `text` | | as in paragraphs.jsonl |

## regulation_scopes.json

`{regulation: {title, version, page, page_end, paragraphs, paragraph_ids, text}}` for base versions. It holds the
top-level main-body paragraph whose heading mentions "scope" (falling back to `1`) with all its sub-paragraphs.

## manifest.json

* `files`: one entry per indexed PDF, with `doc_id`, `path`, `sha256`, `sha256_verified`, `pages`, `visible_lines`,
  `running_header_lines`, `boilerplate_lines`, `footnote_lines`, `assigned_lines`, `unassigned_content_lines`
  (content lines in no record, excluding headers, boilerplate and footnotes), `paragraphs`,
  `paragraphs_with_shall`, `tables`, `definitions`.
* `skipped_files`: PDFs skipped with `--allow-missing` (always empty in a normal run, which fails instead).
* `totals`: sums of the counts, plus `cross_refs` (by status), `parent_status` and `table_owner_status` histograms.
