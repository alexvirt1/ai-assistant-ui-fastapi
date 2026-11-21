# Self-judgment eval: qwen3:8b

Run with `backend/evals/self_judgment` (59 questions, temperature 0, thinking off as in the app, num_ctx 24576, fully on the GPU). See the module docstrings for how questions are built and graded.


2026-11-21 21:29 UTC. Higher is better unless noted.

| Model | Accuracy (easy / hard) | Escalation rate | Recall | Precision | Wasted | Confidently wrong (lower) | Said UNKNOWN when unknowable | Conf. right / wrong | Conf. AUROC | s/question |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen3:8b | 75% (88% / 64%) | 29% | 58% | 65% | 6 | 9 of 59 | 4 of 4 | 95 / 97 | 0.53 | 3.7 |

**Recall**: of the questions the model got wrong (or could not know), the share it escalated. **Precision**: of its escalations, the share that were needed. **Confidently wrong**: answered itself, with the tool available, and was wrong. **Conf. AUROC**: 0.5 is no better than chance at telling its right answers from its wrong ones, 1.0 is perfect.

## qwen3:8b by category

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

## Findings

- Escalates on surface cues: all wrong arithmetic and date answers, but none of its wrong code-tracing or letter-counting answers.
- Self-rated confidence carries no signal (95 when right, 97 when wrong; AUROC 0.53).
- 9 of 59 answers were wrong although the escalate tool was available.
- Implication for hybrid mode: route by rules and the user's choice, not by the model's judgement; give it local calculator and date tools for the failures that need no cloud model.
