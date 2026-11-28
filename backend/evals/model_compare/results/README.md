# Model comparison results

## qwen3:8b vs two larger models on AI-1 (2026-11-28)

The models compared are all in AI-1's Ollama already. Test conditions:
- the same harness and settings for each: `num_ctx` 8192, thinking off, temperature 0;
- run one after another in a reserved GPU window (03:58–04:21 UTC);
- each model was 100% on the GPU at `num_ctx` 8192.

| Measure | qwen3:8b (app today) | qwen3.6-ud:35b-a3b-iq2_m | qwen3.8-ud:27b-iq3_xxs |
|---|---:|---:|---:|
| Kind | dense 8B, Q4_K_M | MoE 35B, 3B active, IQ2_M | dense 27B, IQ3_XXS |
| Size on disk | 5.2 GB | 11.5 GB | 10.9 GB |
| Writing speed (tok/s) | **57.2** | 55.4 | 20.1 |
| Reading, ~2K prompt (tok/s) | **1,993** | 1,350 | 488 |
| Reading, ~6K prompt (tok/s) | **2,652** | 1,831 | 659 |
| Graded questions | 38/59 | 47/59 | **48/59** |
| ... easy / hard | 21/26, 17/33 | 25/26, 22/33 | 26/26, 22/33 |
| Hit the 700-token cap | 0 | 4 | 11 |
| Time for the 59 questions | **55 s** | 177 s | 803 s |
| Right tool / right arguments | 14/14, 13/14 | 14/14, **14/14** | 14/14, **14/14** |

**By category** (qwen3:8b / qwen3.6 / qwen3.8):

| Category | qwen3:8b | qwen3.6 | qwen3.8 |
|---|---:|---:|---:|
| arithmetic | 6/12 | 6/12 | 6/12 |
| code_trace | 7/11 | 10/11 | 10/11 |
| dates | 2/8 | 5/8 | 5/8 |
| letters | 4/7 | 6/7 | 7/7 |
| logic | 5/6 | 6/6 | 6/6 |
| unknowable | 3/4 | 4/4 | 4/4 |
| word_problem | 6/6 | 5/6 | 5/6 |
| facts | 5/5 | 5/5 | 5/5 |

**Russian image request:** both larger models wrote an English image prompt for "Нарисуй кота в космическом шлеме". qwen3:8b kept it in Russian.

**What we haven't measured yet:**
- How long a cold load takes after an image job.
- Whether the app's 9.5 GB hand-back threshold is enough for an 11.5 GB model. It isn't, by arithmetic: the idle card has about 11.7 GB free.
- Behaviour at contexts above 8K, which won't fit.

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
