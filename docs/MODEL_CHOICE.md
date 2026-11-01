# How the default model gets picked

Written and committed before any Granite output existed, and before any held-out run.

**Candidates.** `prism-ml/bonsai-27b` (1-bit quantization, 3.5 GB) and `granite-4.1-8b` (Q4_K_M, Apache-2.0).
Both run through LM Studio on the same machine, one at a time, at 8192 context, temperature 0, strict JSON schema,
no page images.

**Frozen for both runs.** The prompt, the repairs in `tidy.py`, the hand-set confidence weights and the 0.90 review
threshold. Weights aren't refit per model. They were tuned against Bonsai's answers on dev, which may favor Bonsai.

**Data.** The default is chosen from the dev split only: the first 15 base documents in all three conditions, 45
renderings. The held-out split gets run once per model after the choice, on its first 30 base documents, 90
renderings, and doesn't influence the choice.

**The bar, in order:**

1. **It has to finish.** At most 2 of the 45 documents may have no usable answer after retries. A server crash
   that a retry gets past doesn't count here, but it gets reported.
2. **It has to be trustworthy.** Of the values that skip review at 0.90, at most 1.0% may be wrong, with all three
   conditions counted together.
3. **Then the most work saved wins.** The model with the higher share of values skipping review is the default. A
   difference under 1 percentage point is a tie, and the lower mean seconds per document breaks it.

If neither model clears step 2, the one with the lower error is the default, and the report says the bar wasn't met.
