# Public datasets: license review

Checked on 2026-11-01 from each dataset's own page. A dataset gets used only if its license clearly
allows this use: evaluating code meant for commercial reuse, in a public repo, with results published.
Its files never get committed here, so redistribution rights don't come into it. Downloaded files are
treated as untrusted, kept outside the repo, and passed by path.

| Dataset | What it is | License found | Decision |
|---|---|---|---|
| [FATURA](https://zenodo.org/records/10371464) (Limam, Dhiaf, Kessentini) | 10,000 invoice images from 50 templates, with bounding boxes, text and 24 classes. 690.7 MB. | CC BY 4.0 on the Zenodo record, v1 and v2 | **Use for evaluation.** Attribute it in the report. |
| [CORD v2](https://github.com/clovaai/cord) ([mirror](https://huggingface.co/datasets/naver-clova-ix/cord-v2)) | 1,000 Indonesian shop and restaurant receipts (800/100/100), with line items and totals labeled. | CC BY 4.0 in the README and the mirror's tag | **Use for evaluation** of the fields it labels. Attribute it in the report. |
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

## Open point

The FATURA paper's [arXiv page](https://arxiv.org/abs/2311.11856) shows CC BY-NC-SA 4.0. That badge covers the paper. The dataset's own
[Zenodo record](https://zenodo.org/records/10371464) lists "Creative Commons Attribution 4.0 International" under Rights, at the
record level with no separate license for `FATURA2.zip`. That's what this review relies on, checked on 2026-11-01. The record
doesn't say in so many words that the license covers the zip file, so anyone reusing the data commercially should read
both pages themselves. Check the record again before downloading: FATURA gets used only if it still says this.
