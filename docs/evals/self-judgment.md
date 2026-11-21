# Self-judgment eval: can a local model tell when to escalate?

Run on 2026-11-21 for the [hybrid mode plan](../hybrid-mode-plan.md), on AI-1's RTX 3060 (12 GB).
Code and method: `backend/evals/self_judgment/` (re-grade saved runs with `--report`).

## Summary

| | qwen3:8b (as the app runs it) | qwen3.8 27B dense (IQ3_XXS) | qwen3.6 35B-A3B MoE (IQ2_M) |
|---|---|---|---|
| Size on GPU / fits fully | 7.0 GB at 24K ctx / yes | 10.7 GB at 8K ctx / yes | 11.4 GB at 8K ctx / yes |
| Accuracy (easy / hard) | 75% (88% / 64%) | 78% (100% / 61%) | **80%** (96% / 67%) |
| Escalated its wrong answers (recall) | 58% | 35% | **75%** |
| Escalations that were needed (precision) | **65%** | 55% | 43% |
| Confidently wrong (answered itself, wrongly) | 7 of 59 | 9 of 59 | **1 of 59** |
| Ran away (hit the 1024-token output cap) | 0 | 13 | 11 |
| Confidence: right / wrong (AUROC) | 95 / 97 (0.53) | 100 / 98 (0.65) | 100 / 90 (0.67) |
| Seconds per question (3 calls) | **3.7** | 30.6 | 11.9 |

`qwen3:8b` with thinking on is still running; its row will be added.

## Findings

1. **No model judges its own limits well.** The best recall is 75%, and every model's
   self-rated confidence is close to useless (AUROC 0.53-0.67; 0.5 is chance). All rate
   themselves 95-100 whether right or wrong.
2. **Escalation follows surface cues, not real self-knowledge.** All three escalate arithmetic and
   dates, often including questions they would have got right. None reliably escalates its wrong
   code-tracing or letter-counting answers.
3. **Bigger is not better at judging.** The 27B dense model is slightly more accurate than the 8B
   but escalates fewer of its mistakes (35%), and is 8× slower.
4. **The 35B-A3B MoE is the safest judge,** with 1 confidently wrong answer in 59, but it gets
   there by escalating every arithmetic and date question (16 needless escalations), and it runs
   at 3× the 8B's time.
5. **Offering the escalate tool changes answers.** With the tool's prompt, the models sometimes
   gave a different, wrong answer to a question they had answered correctly without it (8B: 4,
   27B: 7, 35B: 1 questions).
6. **Big models at temperature 0 loop on hard questions.** Without an output cap, single calls ran
   for up to 7 minutes and to the end of the context. Capped at 1024 tokens, 11-13 of 59 questions
   hit the cap, mostly arithmetic and dates.
7. **All handle unknowable questions:** every model said UNKNOWN for all 4 when asked plainly.
   With the tool, they either escalated or honestly declined.

## What this means for hybrid mode

- **Route by rules and the user's choice, not the model's judgement.** The model's own `escalate`
  call is one signal among several; its confidence score should not be used at all.
- **Give the local model tools for what it can't do,** instead of escalating: a calculator and a
  date tool would remove most arithmetic and date errors locally, for free.
- **Cap output length in the app** (num_predict), and treat hitting the cap as a reason to escalate.
- **Keep `qwen3:8b` as the local model.** Neither bigger model is a better judge, and both are
  much slower. The 35B-A3B MoE could be an optional "careful local" mode later.

## Method in brief

59 questions in 8 categories (arithmetic, word problems, code tracing, letter counting, dates,
logic, facts, unknowable), each with one machine-checkable answer computed by the generator. Each
question is asked three ways at temperature 0: plainly (accuracy), with an `escalate` tool
(does the model escalate what it gets wrong?), and for a 0-100 confidence. "Should have escalated"
means the model got the plain question wrong, or it is unknowable. Grading is exact, with no
second model. Thinking was off for the first three runs, as in the app. An honest refusal counts
as the right answer to an unknowable question.

Limits: 59 questions per model, one run each at temperature 0, so differences of a few points
are noise. The categories favour exact answers; open-ended quality (writing, analysis) is not
measured.

## Full results (regraded)

| Model | Accuracy (easy / hard) | Escalation rate | Recall | Precision | Wasted | Confidently wrong (lower) | Ran away (lower) | Said UNKNOWN when unknowable | Conf. right / wrong | Conf. AUROC | s/question |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3_8b | 75% (88% / 64%) | 29% | 58% | 65% | 6 | 7 of 59 | 0 | 4 of 4 | 95 / 97 | 0.53 | 3.7 |
| qwen3.8-ud_27b-iq3_xxs | 78% (100% / 61%) | 19% | 35% | 55% | 5 | 9 of 59 | 13 | 4 of 4 | 100 / 98 | 0.65 | 30.6 |
| qwen3.6-ud_35b-a3b-iq2_m | 80% (96% / 67%) | 47% | 75% | 43% | 16 | 1 of 59 | 11 | 4 of 4 | 100 / 90 | 0.67 | 11.9 |

**Recall**: of the questions the model got wrong (or could not know), the share it escalated. **Precision**: of its escalations, the share that were needed. **Confidently wrong**: answered itself, with the tool available, and was wrong. **Ran away**: questions where an answer hit the output cap (looping or endless reasoning); such answers count as wrong. **Conf. AUROC**: 0.5 is no better than chance at telling its right answers from its wrong ones, 1.0 is perfect.

## qwen3_8b by category

| Category | Questions | Accuracy | Escalated | Escalated when wrong |
|---|---|---|---|---|
| arithmetic | 12 | 6/12 | 9 | 6/6 |
| code_trace | 11 | 8/11 | 0 | 0/3 |
| dates | 8 | 5/8 | 6 | 3/3 |
| facts | 5 | 5/5 | 0 | 0/0 |
| letters | 7 | 4/7 | 0 | 0/3 |
| logic | 6 | 6/6 | 0 | 0/0 |
| unknowable | 4 | 4/4 | 2 | 0/0 |
| word_problem | 6 | 6/6 | 0 | 0/0 |

## qwen3.8-ud_27b-iq3_xxs by category

| Category | Questions | Accuracy | Escalated | Escalated when wrong |
|---|---|---|---|---|
| arithmetic | 12 | 6/12 | 2 | 2/6 |
| code_trace | 11 | 9/11 | 1 | 0/2 |
| dates | 8 | 4/8 | 3 | 2/4 |
| facts | 5 | 5/5 | 3 | 0/0 |
| letters | 7 | 7/7 | 0 | 0/0 |
| logic | 6 | 6/6 | 0 | 0/0 |
| unknowable | 4 | 4/4 | 2 | 0/0 |
| word_problem | 6 | 5/6 | 0 | 0/1 |

## qwen3.6-ud_35b-a3b-iq2_m by category

| Category | Questions | Accuracy | Escalated | Escalated when wrong |
|---|---|---|---|---|
| arithmetic | 12 | 6/12 | 12 | 6/6 |
| code_trace | 11 | 10/11 | 0 | 0/1 |
| dates | 8 | 5/8 | 8 | 3/3 |
| facts | 5 | 5/5 | 0 | 0/0 |
| letters | 7 | 6/7 | 0 | 0/1 |
| logic | 6 | 6/6 | 2 | 0/0 |
| unknowable | 4 | 4/4 | 3 | 0/0 |
| word_problem | 6 | 5/6 | 3 | 0/1 |

