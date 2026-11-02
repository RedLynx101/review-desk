# Public datasets: license review

Checked on 2026-11-01 from each dataset's own page, and FATURA's record again through Zenodo's API on the day it was downloaded. A dataset gets used only if its license clearly
allows this use: evaluating code meant for commercial reuse, in a public repo, with results published.
Its files never get committed here, so redistribution rights don't come into it. Downloaded files are
treated as untrusted, kept outside the repo, and passed by path.

| Dataset | What it is | License found | Decision |
|---|---|---|---|
| [FATURA](https://zenodo.org/records/10371464) (Limam, Dhiaf, Kessentini) | 10,000 invoice images from 50 templates, with bounding boxes, text and 24 classes. 690.7 MB. | CC BY 4.0 on the Zenodo record, v1 and v2 | **Used, on a sample of 50**, one per template. Results in the [report](../results/REPORT.md#documents-this-repo-didnt-generate). |
| [CORD v2](https://github.com/clovaai/cord) ([mirror](https://huggingface.co/datasets/naver-clova-ix/cord-v2)) | 1,000 Indonesian shop and restaurant receipts (800/100/100), with line items and totals labeled. | CC BY 4.0 in the README and the mirror's tag | **Used, on the whole test split of 100**, for the fields it labels. Results in the [report](../results/REPORT.md#documents-this-repo-didnt-generate). |
| [Samples of electronic invoices](https://data.mendeley.com/datasets/tnj49gpmtz/1) (Kozłowski, Weichbroth) | 1,000 auto-generated invoices. | CC BY 4.0 | Allowed but not planned. FATURA has more layouts. |
| [invoices-donut-data-v1](https://huggingface.co/datasets/katanaml-org/invoices-donut-data-v1) | 500 of the invoices above, with annotations added. | Tagged MIT, built on a CC BY 4.0 source | Not planned. Attribution would still follow the source. |
| [SROIE, ICDAR 2019](https://rrc.cvc.uab.es/?ch=13) | 1,000 scanned receipts. | None on the overview or downloads pages. Downloads need registration. A [third-party mirror](https://github.com/zzzDavid/ICDAR-2019-SROIE) is tagged MIT, but a mirror's owner can't grant rights to the data. | **Not used.** The license is unclear. |
| [DocILE](https://docile.rossum.ai/) | Annotated business documents. | No formal license on the page. Access is by request form, "for research purposes". | **Not used.** The terms point to research use, and this is a portfolio repo. |

## What each one can and can't show

Both datasets check the same pipeline against documents this repo's generator didn't produce. Neither
one is a sample of what a particular business receives.

- **FATURA** is also generated, by another team, from templates. It tests layouts written by someone
  else. It doesn't test real paper. Its answers are labeled boxes, so mapping its classes onto this
  repo's schema is part of the work, and fields that don't map get left out and listed.
- **CORD** is the only source of real receipts here. Its README says store and payment fields were
  removed for legal reasons, so vendor names and dates can't be scored on it: only line items, subtotal,
  tax and total. Amounts are in Indonesian rupiah, which is a hard case for parsing. Images never go in
  this repo, the demo or the gallery, which stay clearly synthetic. The report publishes aggregate numbers only.

## Credit

- **CORD**: Park, Shin, Lee, Lee, Park, Seo, Oh, Kim, Lee. "CORD: A Consolidated Receipt Dataset for Post-OCR Parsing." Document Intelligence Workshop at NeurIPS 2019. CC BY 4.0. The v2 test split was read from the [Hugging Face mirror](https://huggingface.co/datasets/naver-clova-ix/cord-v2).
- **FATURA**: Limam, Dhiaf, Kessentini. "FATURA: A Multi-Layout Invoice Image Dataset for Document Analysis and Understanding." [Zenodo](https://zenodo.org/records/10371464). CC BY 4.0.

Nothing from either one is redistributed here: no documents, labels, model answers or examples, only aggregate counts. For the run, each image was wrapped in a one-page PDF with no text layer, and its labels were mapped onto this repo's schema, in [outside.py](../src/review_desk/evaluation/outside.py).

## Open point

The FATURA paper's [arXiv page](https://arxiv.org/abs/2311.11856) shows CC BY-NC-SA 4.0. That badge covers the paper. The dataset's own
[Zenodo record](https://zenodo.org/records/10371464) lists "Creative Commons Attribution 4.0 International" under Rights, at the
record level with no separate license for `FATURA2.zip`. That's what this review relies on, checked on 2026-11-01. The record
doesn't say in so many words that the license covers the zip file, so anyone reusing the data commercially should read
both pages themselves. Check the record again before downloading: FATURA gets used only if it still says this.
