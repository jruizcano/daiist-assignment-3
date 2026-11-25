# Evaluation files



## questions.csv



30+ questions written by you from the documents, before tuning anything.



| Column | Content |

|---|---|

| `id` | `q01`, `q02`… |

| `question` | As a user would ask it. |

| `kind` | `single`, `multi` (two passages) or `unanswerable` (5+ of each of the last two). |

| `expected_source` | Document that answers it, as in your chunk metadata; `;` between several. Empty if unanswerable. |

| `expected_location` | Page, section or article, as in your metadata. Empty if unanswerable. |

| `reference_answer` | Short correct answer; for unanswerable, what the assistant should say. |



Make unanswerable questions close to the corpus's topic, not obviously off-topic.



## human_labels.csv



Your own grades of 20+ agent answers, done before reading the judge's verdicts.



| Column | Content |

|---|---|

| `id` | Question id. |

| `correct` | `1`/`0`: right against the reference (or a proper refusal). |

| `supported` | `1`/`0`: every claim is in the retrieved text. |

| `notes` | Why, for every `0`. |



Commit what your evaluation stage writes here too (answers, verdicts, summary).


## Location format used by this corpus

`expected_source` contains exact extracted `document_id` values. `expected_location`
contains exact extracted `section` values, preserving spelling, capitalization and
Unicode punctuation. For example, Airbnb uses `Prospectus Summary` while DoorDash
uses `PROSPECTUS SUMMARY`. Do not invent a subheading path that is absent from the
metadata. Separate multiple documents or sections with `;` (no padding spaces).
These are lists of expected documents and sections, not positionally paired lists:
one document may require several sections and several documents may share a section
name. Retrieval evaluation must check the returned chunk's document AND section and
must require evidence for all necessary sources for multi-document questions.
Leave both fields empty for `unanswerable` questions; the expected response is that
the corpus cannot establish the answer, not an unsupported yes/no conclusion.

Question q22 remains reserved for the pending EV/revenue question. The 30 active
questions have unique IDs q01-q21 and q23-q31; the gap does not represent a missing
active question.


## Full answer evaluation

Run `uv run python main.py evaluate` from the project directory. See [ANSWER_EVALUATION.md](../docs/ANSWER_EVALUATION.md) for the approved rubric, saved outputs, caching and blind human grading. `answer_evaluation/latest.json` points to the current results folder. Its `human_review.html` hides judge verdicts. Export grades from that page and import with `uv run python -m evaluation.answer_evaluation --import-human "PATH_TO_EXPORT"`. Recount with `uv run python -m evaluation.answer_evaluation --offline`; this makes no API calls. Previous passage reviews do not count as human answer grades.
