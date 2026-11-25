# Assignment 3 Report

- **Name**: Justo Ruiz Cano 
- **Student ID**:20317
- **Group**: [PPLEDBA 5A]

## Use case

The aim of this project is to provide a way to search for key information inside an IPO prospectus without needing relying on key word searches. It should be able to answer questions such as: 
"What is the main goal of the company", "what does it need the financing for", "what are the company key risks".
The project should also be able to extract tables with the correct units when prompted. 

The key thing here is that if the information is not provided, or not explicitly named, the model should not hallucinate. Instead it should answer it is not sure. 

When returning the information, the model must also give the whole sentence so that the user is able to find that information themselves when verifying the information. 

An incorrect answer could cause an analyst to misunderstand the company's finances or risks. The same can be said about incorrect units, which could materially misrepresent a financial figure. 

## Corpus

For this assignement, I am using IPO prospectuses directly from SEC.gov 
Uber: https://www.sec.gov/Archives/edgar/data/1543151/000119312519144716/d647752d424b4.htm
DoorDash: https://www.sec.gov/Archives/edgar/data/1792789/000119312520313884/d752207d424b4.htm
Airbnb: https://www.sec.gov/Archives/edgar/data/1559720/000119312520315318/d81668d424b4.htm

Word count:

Airbnb: 212,208 raw words 

DoorDash: 183,396 raw words

Uber: 208,098 raw words 

Total:603,702 raw words

## Ingestion

*How you extract, clean and chunk; one extraction problem you fixed; chunk metadata; number of chunks.*
The first stage is in prepare_corpus.py. It reads the three url's from corpus_sources.json and saves them. 
Then in extract_corpus.py, beautifulSoup is used to identify paragraphs, links and tables. This is done first because if we would have just extracted all teh text, it would have mixed financial figures their heading tables which could have made their relationships unclear. 

The script also removes repeated labels (such as table of contents), page lables, white space and formating characters. In order for chunking to not break up the order and to keep things referenced inside of multiple blocks, each block has the company, filing date, document id, section and source  url attached to them. there are also ancors used to track down sections such as "risk factors". 

Tables are extracted separately from paragraphs. Importantly, it preserves unit statements rather than interpreting them or converting them. 

For footnotes, they are appended to the text rather than retrived separately. This simplifies the retrival process and adds granularity to when sentences are referenced since they will include the footnote.  

To create the chunks, there are 3 approaches that I implemented with the help of AI. 
1) Fixed size: this is the simplest one. It takes 400 word windows with 80 words of overlap and simply creates each chunk. This solution is easy to implement, and while there is still some sentence truncation in the first/last sentence of the chunk, the added overlap helps add more context to the chunk. 
2) A paragraph aware approach does not break sentences compared to the approach above. This method combines complete paragraphs if their total stays within 400 count. If paragraphs are longer than 400 words then have the same boundary conditions as the fixed size approach.  
3) This last solution creates heading paths, with the same structure as the fixed size approach. The only difference here is that it keeps the extractors main section and identifies headings to create nested subsections. The idea behind this is that it could offer a more efficient search. 

Something to note here is 400 words is a target not a hard limit. Headers and footnotes can make a chunk longer, with the largest being 1,128 words due to the appended footnotes. 

With regards to the questions, there are 3 types: 
1) Single: these are questions which are answerable just from one ipo 
2) multi: combines 2+ different passages or IPOs into a single response 
3) Unanswerable: questions where the AI system should respond that there is no information about the prospectuses available. 

Note the questions were designed by me, but the expected response was written by an AI system. 

So, for each of the prospectuses, there are aprox ~3150 chunks each. 

One extraction mistake that was done by the ai but upon review was that the paragraph aware approch broke down chunks into 400 words or less, but if a paragraph was more than 400 words, then it would cut the words without the chunk overlapp that the fixed sized approach has. 

## Test set

*How you wrote it, counts by kind, two examples.*

So the questions were written based on what an analyst may look for in an IPO prospectus. These are things such as revenue, risks, ipo pricing, use of proceeds etc. 

There are 18 single passage questions, 7 multi and 5 unanswerable questions to test if the guardrails later are implemented correctly. They are based on realised future growth, and as such, a model cannot answer those questions. 

I wrote all the questions, but I did use ai to format them when I put them in the file and to provide reference answers and their expected locations. I checked the answers since they are easily findable using a keyword search. 

The questions are saved inside the eval folder, under questions. The format is as follows: ID, question, category, expected source, expected location and reference answer. Note that the expected location is the sections and tables preserved during extraction. 

Example 1: What revenue did DoorDash report for the year ended December 31, 2019? 
Answer: $885m. This question asses whether the model can accurately identify correct annual column and preserve the units. 

Example 2: What revenue did Uber report for the year ended December 31, 2019?
Answer: Unanswerable. The IPO was published before the full year results were available.

## Retrieval

*Hit rate at 1/3/5 per design, words added to the prompt, the design you chose.*

The embeddign model I used was OpenAi's text-embedding-3-small model, which is the same model I used for the document chunks. The reason I used the OpenAi one instead of the free key is because I did not want to get rate limited and this asignment likely only costed 30 cents. 

The way the retrival works is by comparing the question vector with the saved chunked vectors. Then you just find the cosine similarity and return the closet matches. This is the same approach in all 3 ipo documents.

Comparing retrival at k=1,3,5 there was very different results. The largest improvement was seen in the fixed size, which increased around 24%, from 56% to 80%. The other two models, paragraph-aware and heading paths, were very similar in performance, 68 -> 88 and 64 --> 88 % for paragraph-aware and heading paths respectively. A hit is defined as if at least one retrive chunk matches both an expected document and section. This was measured over the 25 questions, excluding the 5 that were not answerable. The results can be found under eval\retrival.summary.json. The average retrived text per question can also be found in that document ~1300 words over the 25 questions. 

I chose the paragraph aware design and the five passages per search moving forward. The reason for this is it achieved the highest hitrate, when just looking at the first result, and also tied when looking at the 5th result. It also did this while on average returning less text, although the difference was very minimal. 

Just a quick reference: 88% hit rate != 88% answers correct. it just is how well the model identified the chunks that it needed to return. 

## Tools and subagent

*Your tools' descriptions, one search result, the subagent's instructions, one transcript from your editor.*

The retrival system worked through an mcp server (server.py). There are 2 main tools which the agent can use: 
1) search(query): this embeds the question and returns the 5 closest paragraph-aware chunks. The cosine similarity score ranks the results but importantly it does not establish whether they give an answer. 

2) add_document(path): This processes the html document. The tool then reads the file, extracts the text and its tables. Then it creates the chunks, embeds them the same way as before and then adds them to the searchable index. 

The corpus-researcher subagent can be found under: .claude/agents/corpus-researcher.md
It only uses the corpus search tool and it has strict instructions to ONLY answer from the retrived evidence. it also links the chunk IDs and the source URLs. It also quotes the supporting sentence. If there is tabular data that it must retrive, it has a clar mandate to check teh relevant row, year and units. The skill also checks the footnotes attached to the chunk to make sure that there is full context (for instance sometimes there are some assumptions or revisions in the footnotes that could be necessary to answer the question completly).

Transcript: 
Use the corpus-researcher subagent to answer this question:

What final IPO offer price per share is stated in Airbnb’s prospectus?

Have the subagent use only the corpus MCP search tool as its evidence.
Include the supporting quotation, chunk ID, and original source URL.
Do not read the evaluation files or reference answers.
Do not modify any project files.


Airbnb's final IPO offer price is **$68.00 per share** of Class A common stock.

**Evidence**

- **Quotation:** "The initial public offering price is $68.00 per share."
- **Chunk ID:** `b_paragraph-01c6400b19fce3e6f720`
- **Source URL:** https://www.sec.gov/Archives/edgar/data/1559720/000119312520315318/d81668d424b4.htm

The pricing table on the cover page (chunk `b_paragraph-2966e29f834f5e357bd7`, same URL) repeats the sentence above and gives the same figure. The table quotes below are separate lines, kept exactly as they appear in the source:

- Column header row: " |  | Per Share | Per Share |  |  | Total | Total | "
- Price row: "Initial public offering price |  | $ | 68.0000 |  |  | $ | 3,490,000,108 | "

**Why it's the final price, not an estimate:** the document is the final prospectus. Its heading reads "Filed Pursuant to Rule 424(b)(4) Registration No. 333-250118", and the price is stated as fixed. No estimated price range or assumed midpoint came up in the results.

The subagent used only the corpus search tool and needed one search. I haven't re-checked its quotations myself, so treat them as reported by the subagent. No evaluation files or reference answers were read, and no project files were changed.


## Adding a document

*What happens when a document is already indexed. The unanswerable question, the document you added, the answer before and after.*

So for this section I wanted an easy way to evaluate one of the questions that was unanswerable. I chose the following question: 

What revenue did Uber report for the year ended December 31, 2019?

The original IPO document could not answer this question because it was published before the end of the year. To make the ingestion cheaper, I added an excerpt of the 2020 earnings release. I didn't want to use unecessary tokens, but a full document would have worked with the exact same methodlogy. 

Then after adding it, the agent answered $14,147 million and cited the correct document.  

## Harness and guardrails

*Your AGENTS.md rules, the approval step, and your extra guardrail with one case where it fired.*

The harness cna be found in agent.py. It uses LangChain and OpenAI's gpt-5-nano model. What it does is loads the file as the system instructions and connects to the MCP server. Every simply when the agent gets asked a question, it searches corpus. If it doesnt find an answer, it can make further searches, until it hits it's limit of 6 model calls and tool calls. 

There are strict instrutions for the agent to only answer from retrieved passages and to search them before answering. There are other rules such as for any factual claim to include a chunk ID and to add a supproting quotation. This way we can try to ensure that the ai does not hallucinate. There are also a few other rules such as the distinction beetween gross proceeds from other proceeds since this was a question that initially the AI got wrong so I wanted to add some further specification to it. Furthermore, there are additional checks for instance, the cited IDs actually exist and more importantly, that the quotation exists inside those chunks! If this fails, the answer is blocked. The response is able to be saved if the quote can be found in another chunk, but I think the curret way this is implemented, although it works, may be too slow so some alternative approach could be useful.

To minimise token usage, when any document is added, there is an explicit approval needed. 

One case where the guardrail fired was actually with the added Uber results. The agent did retrieve the new document, but in a later attempt the answer failed the checks and was blocked. So the data was available, but the answer was not verified well enough to return it. 

## Evaluation

*Code checks, the judge's rubric, your agreement with the judge, and results: correct, unsupported, correct refusals, wrong refusals.*

To evaluate the test set of questions, I ran the agent on all the questions and saved the answers, retrieved passages, citations and what tool the agent used. The first step in the evaluation is just basically checking that all the chunks exist and if the quotations match the evidence. If they pass, then they are assessed by an llm judge using all the information available in the context of this project (the question, reference answer and the retrived passages). 

I then used the following rubric: correct and supported. A correct answer (marked as 1) is one that either fully answers the question or refused an unanswerable question. If the answer is incomplete, even if the data is all correct and relevant, it recieves a 0. For the supported, it simply means that the claims all follow from the retrived evidence. 

for the following data please run: uv run python -m evaluation.answer_evaluation --offline

The metrics are as followed (formatted by ai agent)

| Measure | Result |
|---|---:|
| Questions evaluated | 30 |
| Answers blocked by the guardrail | 5 |
| Judge assessments flagged for review | 5 |
| Valid judge assessments | 20 |
| Correct among valid assessments | 17/20 — 85% |
| Unsupported among valid assessments | 1/20 — 5% |
| Refusals on unanswerable questions | 4/5 |
| Refusals on answerable questions | 4/25 |
| Human–judge agreement on correctness | 18/20 — 90% |
| Human–judge agreement on support | 19/20 — 95% |

A few things to mention here. The Correct among valid assesments is 85%. This is done out of 20 ai deemed valid judge assessments, not all 30 questions. This is because 5 answers were blocked by the guardrail and 5 assessments from the llm judge failed the checks. The unanswerable questions are still part of the evaluation. This is important since this could bias the result becuse the excluded cases could have been difficult outputs. 

I then checked the answers myself (it can be found inside eval/human_labels.csv). I agreed with the judge 18/20 answers (correctness), and 19/20 (with regards to the suppot). Note this is not proof that I am correct or the AI is correct. 

## Failures

*Failed questions by cause, with counts, and what to fix first.*
(again formatted by ai)
| Observed problem | Questions | Count |
|---|---|---:|
| Guardrail blocked the answer | q05, q19, q27, q30, q31 | 5 |
| Refused an answerable question | q02, q10, q20, q29 | 4 |
| Gave one risk when three were requested | q04, q06 | 2 |
| Misinterpreted financial figures or measures | q15, q21 | 2 |
| Gave planned spending instead of establishing actual spending | q26 | 1 |

So these are some of the failed questions I had. 
q15: agent correctly retreived table but incorrectly labelled proceeds after underwriting discounds as gross proceeds and then further deducted underwriting discounts again. Correct evidence, but wrong financial interpretation. Here, it's complicated, but likely would need some sort of more developed skill which states how to calculate things correctly. 

q4/6: these questions asked for three risks for one company, but the answer only gave one. A way this could be fixed is to check that the answer actually covers all three risks before returning it.

q21: this question compared revenue growth between Uber and DoorDash, but the answer used Uber's ridesharing growth instead of total revenue growth and left DoorDash's growth missing. This could be improved by searching for each company separately and checking that the same measure is being compared.

q26: semantic difficulty/edge case. The question asked how the money was used, not how they planned to use them. This could be fixed by asking the user to clarify their question. 

## Decision

*Would you let users rely on it, for what, with which safeguard? Cost and latency per question.*

Yes and no. It definitely would help search for information faster and ask questions about specific documents + have a corpus that you can just search over without having to dig out a specific document. At the same time, currently as it stands, it doesnt really work for multiple documents being analyised at the same time, so that would need to be improved. It also failed at some financial analysis (see the part of gross proceeds before), it found the data correctly, but did not interpret it correctly. This to me means that it isnt really at the level necessary for an analyst yet. 

| Measure | Average per question |
|---|---:|
| Agent input tokens across model calls | 37,612 |
| Agent output tokens, including reasoning | 2,384 |
| Answer-generation model cost | $0.00219 |
| Answer generation plus evaluation judging cost | $0.00348 |
| Agent response time | 22.6 seconds |

the agent response time is quite slow currently. this is more of a prototype showing that it could work, but not currently as a standalone product. 
## AI use

Ai was heavily used in this assingment compared to the previous. All the code was written by the AI and the agent harnesses were also written by the agent. However, at all points I guided the agent, not the other way round. The idea and the approach was all mine, with influence from what we saw in class. 