# Model comparison results

## qwen3:8b baseline (2026-11-27)

The app's model, through Ollama on AI-1 (RTX 3060 12 GB). Settings:
- `num_ctx` 8192, thinking off, temperature 0;
- fully on the GPU, checked before measuring;
- no other GPU work, no reservation.

| Measure | qwen3:8b |
|---|---:|
| Writing speed (~400-token answer, tok/s) | 57.2 |
| Reading speed, ~2K-token prompt (tok/s) | 1,993 |
| Reading speed, ~6K-token prompt (tok/s) | 2,652 |
| Graded questions | 38/59 (easy 21/26, hard 17/33) |
| Tool chosen correctly | 14/14 |
| Tool arguments as expected | 13/14 |

**Questions by category:**

| Category | Score |
|---|---:|
| arithmetic | 6/12 |
| word_problem | 6/6 |
| code_trace | 7/11 |
| letters | 4/7 |
| dates | 2/8 |
| logic | 5/6 |
| facts | 5/5 |
| unknowable | 3/4 |

**The one tool miss:** for "Нарисуй кота в космическом шлеме", the image prompt was written in Russian ("кот в космическом шлеме"). The tool asks for an English description.

**Notes on the method:**
- The first run's reading speeds were wrong, because runs 2 and 3 reused Ollama's prompt cache. Each run's prompt now starts differently.
- The first attempt was stopped: ComfyUI still held a model, and qwen3:8b loaded only 3.3 of its 6.0 GB on the GPU. The harness now refuses to measure a model that isn't fully on the GPU.

**Not compared:** Qwen3.8-Flash-Next on PolyStrata (from the 8rapid-strata repo). PolyStrata's ready-made engine needs AVX2, and AI-1's Xeon E5-4657L v2 has only AVX. The experimental AVX build is compiled on the machine, and PolyStrata measured it at 11–17 tok/s even on a Ryzen 5 7600, with slower expected on an old Xeon.
