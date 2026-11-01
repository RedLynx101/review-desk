# Accuracy report

Every document here is synthetic: generated from a seed, with invented names and known answers. Nothing here comes from a client, and there's no client deployment yet. These numbers say how the pipeline does on these documents. They don't predict any particular business's paperwork.

**Default model: `prism-ml/bonsai-27b`.** Higher share of values skipping review.

At a 0.9 threshold on the held-out set, bonsai-27b let 96.8% of values on clean PDFs skip review, and 3 of those 1222 were wrong. On light scans it was 87.5%, with 7 of 1104 wrong.

**The weak spot is degraded scans.** Only 8.2% of values skip review there, which is the system working, but 8 of those 106 were wrong (7.55%). A bad scan needs a person's eyes on every value, whatever the score says. On the heaviest damage, OCR could read 15.7% of the printed values, and 1.9% came out right.

Across all conditions bonsai-27b skipped review on 63.7% of values with 0.74% wrong. granite-4.1-8b skipped 60.4% with 1.04% wrong, and took 28 seconds per document against 56.

## Setup

- Held-out set: 30 base documents, each rendered clean, scanned and degraded, 90 renderings per model. The split has its own vendor names and three layouts that were never rendered while tuning. Each model ran it once.
- Models: `prism-ml/bonsai-27b` (Q1_0); `granite-4.1-8b` (Q4_K_M). Through LM Studio, 8192 context, temperature 0.0, max tokens 3500, strict JSON schema, no page images.
- OCR: Tesseract 5.3.3 on image pages, after straightening and denoising. Digital PDFs use their text layer, checked against the rendered page.
- Review threshold 0.9, with hand-set weights. Both were fixed before these runs, and neither was refit per model. They were tuned against Bonsai's answers on dev, which may favor Bonsai.
- Public datasets weren't run. FATURA and CORD passed the license review in [DATASETS.md](../docs/DATASETS.md), but this round used no downloads, so nothing here has been tested on documents this repo's generator didn't make. That's the largest gap in this report.

## What matters most: values that skipped review

Coverage is the share of values at or above the threshold, which a person isn't asked to check. Error is how many of those were wrong. A null counts as a value, so the printed-only row is the stricter reading. The upper bound assumes independence, which renderings of one document don't quite have, so treat it as a floor.

| Model | Set | Values | Skip review | Wrong among skipped | Error | Upper 95% |
|---|---|---|---|---|---|---|
| bonsai-27b | clean | 1262 | 96.8% | 3 of 1222 | 0.25% | 0.72% |
| bonsai-27b | scanned | 1262 | 87.5% | 7 of 1104 | 0.63% | 1.30% |
| bonsai-27b | degraded | 1294 | 8.2% | 8 of 106 | 7.55% | 14.19% |
| bonsai-27b | all | 3818 | 63.7% | 18 of 2432 | 0.74% | 1.17% |
| bonsai-27b | printed values only | 3420 | 64.9% | 16 of 2219 | 0.72% | 1.17% |
| granite-4.1-8b | clean | 1270 | 91.6% | 9 of 1163 | 0.77% | 1.46% |
| granite-4.1-8b | scanned | 1266 | 82.8% | 9 of 1048 | 0.86% | 1.62% |
| granite-4.1-8b | degraded | 1302 | 8.1% | 6 of 106 | 5.66% | 11.80% |
| granite-4.1-8b | all | 3838 | 60.4% | 24 of 2317 | 1.04% | 1.54% |
| granite-4.1-8b | printed values only | 3420 | 61.7% | 16 of 2111 | 0.76% | 1.23% |

Coverage against error for the default model, bonsai-27b:

| Threshold | Skip review | Wrong among skipped | Error |
|---|---|---|---|
| 0.5 | 72.1% | 131 of 2752 | 4.76% |
| 0.7 | 68.7% | 82 of 2622 | 3.13% |
| 0.8 | 66.1% | 48 of 2522 | 1.90% |
| 0.85 | 65.4% | 38 of 2496 | 1.52% |
| 0.9 | 63.7% | 18 of 2432 | 0.74% |
| 0.95 | 60.7% | 12 of 2318 | 0.52% |
| 0.98 | 44.3% | 2 of 1691 | 0.12% |

## By scan quality

Printed values only, so an easy null can't flatter a bad scan. "OCR could read" is the share whose exact characters survive in the extracted words: the ceiling for any text-only model.

| Model | Scan quality | Documents | Printed values right | OCR could read | Right when readable |
|---|---|---|---|---|---|
| bonsai-27b | clean | 30 | 98.8% (1126/1140) | 100.0% | 98.8% (1126/1140) |
| bonsai-27b | light | 30 | 96.0% (1094/1140) | 98.9% | 96.8% (1092/1128) |
| bonsai-27b | moderate | 12 | 51.5% (203/394) | 80.2% | 61.1% (193/316) |
| bonsai-27b | heavy | 18 | 1.9% (14/746) | 15.7% | 5.1% (6/117) |
| granite-4.1-8b | clean | 30 | 97.1% (1107/1140) | 100.0% | 97.1% (1107/1140) |
| granite-4.1-8b | light | 30 | 93.9% (1070/1140) | 98.9% | 94.9% (1070/1128) |
| granite-4.1-8b | moderate | 12 | 53.6% (211/394) | 80.2% | 64.2% (203/316) |
| granite-4.1-8b | heavy | 18 | 2.5% (19/746) | 15.7% | 6.0% (7/117) |

## Accuracy by field and condition

| Field | bonsai-27b clean | bonsai-27b scanned | bonsai-27b degraded | granite-4.1-8b clean | granite-4.1-8b scanned | granite-4.1-8b degraded |
|---|---|---|---|---|---|---|
| document_type | 100% | 100% | 67% | 100% | 100% | 80% |
| vendor_name | 97% | 87% | 40% | 100% | 90% | 43% |
| document_number | 100% | 87% | 30% | 73% | 70% | 23% |
| issue_date | 100% | 93% | 23% | 90% | 83% | 30% |
| due_date | 97% | 97% | 57% | 93% | 90% | 53% |
| purchase_order | 97% | 93% | 73% | 83% | 83% | 73% |
| bill_to_name | 97% | 97% | 53% | 97% | 97% | 53% |
| currency | 100% | 100% | 47% | 100% | 97% | 43% |
| subtotal | 100% | 100% | 23% | 100% | 100% | 23% |
| discount | 100% | 100% | 93% | 100% | 100% | 93% |
| shipping | 100% | 100% | 73% | 100% | 97% | 70% |
| tax | 97% | 97% | 20% | 100% | 100% | 30% |
| total | 100% | 97% | 20% | 100% | 97% | 23% |
| item.description | 100% | 100% | 18% | 98% | 98% | 14% |
| item.quantity | 98% | 94% | 19% | 93% | 85% | 22% |
| item.unit_price | 98% | 94% | 16% | 96% | 94% | 21% |
| item.amount | 100% | 98% | 19% | 98% | 99% | 19% |

## Layouts never seen in tuning

| Model | Layouts | Documents | Values right | Skip review | Wrong among skipped |
|---|---|---|---|---|---|
| bonsai-27b | seen in tuning | 69 | 73.2% (2099/2869) | 64.1% | 11 of 1839 |
| bonsai-27b | never seen in tuning | 21 | 75.0% (712/949) | 62.5% | 7 of 593 |
| granite-4.1-8b | seen in tuning | 69 | 72.5% (2095/2889) | 61.5% | 15 of 1778 |
| granite-4.1-8b | never seen in tuning | 21 | 71.2% (676/949) | 56.8% | 9 of 539 |

## Whole documents

A document nobody needs to touch is the real saving, and a single flagged value sends it to a person. Seconds include any queueing.

| Model | Condition | Documents | Failed | Every value right | Nothing flagged | Wrong though nothing flagged | Rows missed | Mean seconds |
|---|---|---|---|---|---|---|---|---|
| bonsai-27b | clean | 30 | 0 | 22 | 15 | 1 | 0 of 218 | 64 |
| bonsai-27b | scanned | 30 | 0 | 12 | 2 | 1 | 0 of 218 | 64 |
| bonsai-27b | degraded | 30 | 1 | 0 | 0 | 0 | 152 of 218 | 38 |
| granite-4.1-8b | clean | 30 | 0 | 8 | 6 | 1 | 0 of 218 | 33 |
| granite-4.1-8b | scanned | 30 | 0 | 7 | 0 | 0 | 0 of 218 | 33 |
| granite-4.1-8b | degraded | 30 | 0 | 0 | 0 | 0 | 156 of 218 | 19 |

Printed mistakes are built into some documents. A sum that doesn't work should reach a person. A missing number is different: leaving it blank is the right answer, so nothing has to be flagged. No document in this set carries a bad sum, so it doesn't test whether those reach a person.

| Model | Printed mistake | Documents | Flagged for review |
|---|---|---|---|
| bonsai-27b | No document number printed | 12 | 10 |
| granite-4.1-8b | No document number printed | 12 | 12 |

## Wrong values that skipped review

### bonsai-27b

| Document | Field | Truth | Read | Score |
|---|---|---|---|---|
| heldout-0003-invoice-sidebar.clean | bill_to_name | Sample Q. Customer | Works Co. | 0.95 |
| heldout-0003-invoice-sidebar.scanned | bill_to_name | Sample Q. Customer | Works Co. | 0.95 |
| heldout-0006-invoice-boxed.scanned | document_number | None | 077276 | 0.98 |
| heldout-0006-invoice-boxed.scanned | purchase_order | 077276 | None | 0.94 |
| heldout-0011-receipt-kiosk.degraded | description | Herb bundle | Herb bundie | 0.96 |
| heldout-0012-invoice-banner.degraded | vendor_name | Snugglebrook Sign Painters S.A.R.L. | Sign Painters | 0.97 |
| heldout-0013-invoice-sidebar.scanned | vendor_name | Figgleton Cartage & Freight LLC | Figgleton Cartage Bill & Freight LLC | 0.99 |
| heldout-0017-receipt-pos_wide.degraded | currency | CAD | USD | 0.95 |
| heldout-0019-invoice-summary_first.clean | due_date | 2024-12-28 | None | 0.95 |
| heldout-0019-invoice-summary_first.scanned | due_date | 2024-12-28 | None | 0.95 |
| heldout-0021-invoice-classic.degraded | description | Debris haul-away | haul-away | 0.96 |
| heldout-0023-receipt-kiosk.clean | description | Pork dumplings, 6 | Pork dumplings | 0.96 |
| 6 more in `scored.jsonl` | | | | |

### granite-4.1-8b

| Document | Field | Truth | Read | Score |
|---|---|---|---|---|
| heldout-0003-invoice-sidebar.clean | bill_to_name | Sample Q. Customer | Ravelstitch Gasket | 0.95 |
| heldout-0003-invoice-sidebar.scanned | vendor_name | Ravelstitch Gasket Works Co. | Ravelstitch Works Co. | 0.93 |
| heldout-0003-invoice-sidebar.scanned | bill_to_name | Sample Q. Customer | Customer | 0.95 |
| heldout-0006-invoice-boxed.scanned | document_number | None | 077276 | 0.98 |
| heldout-0007-invoice-letterhead.degraded | currency | GBP | USD | 0.95 |
| heldout-0011-receipt-kiosk.clean | description | Terracotta pot | 2x Terracotta pot | 0.96 |
| heldout-0011-receipt-kiosk.scanned | unit_price | None | 19.06 | 0.99 |
| heldout-0012-invoice-banner.degraded | vendor_name | Snugglebrook Sign Painters S.A.R.L. | Sign Painters | 0.97 |
| heldout-0012-invoice-banner.degraded | currency | EUR | USD | 0.95 |
| heldout-0013-invoice-sidebar.clean | quantity | None | 15 | 0.99 |
| heldout-0013-invoice-sidebar.clean | unit_price | None | 88.71 | 0.99 |
| heldout-0013-invoice-sidebar.degraded | currency | USD | GBP | 0.95 |
| 12 more in `scored.jsonl` | | | | |

## Model failures during the held-out runs

Counts every attempt, including ones a retry got past.

- bonsai-27b: truncated 1.
- granite-4.1-8b: none.

## How the default was chosen

From dev only, by the bar committed before any Granite run in [MODEL_CHOICE.md](../docs/MODEL_CHOICE.md).

| Model | Documents | No answer | Skip review | Wrong among skipped | Error | Mean seconds | Clears the bar |
|---|---|---|---|---|---|---|---|
| prism-ml/bonsai-27b | 45 | 0 | 65.3% | 5 of 1191 | 0.42% | 61 | yes |
| granite-4.1-8b | 45 | 0 | 58.6% | 5 of 1068 | 0.47% | 35 | yes |

## Where these numbers come from

`python -m review_desk.evaluation.compare` wrote this file from the runs below. Each folder holds every model answer, every scored value and the commit it ran at.

- [dev-bonsai](runs/dev-bonsai/summary.md)
- [dev-granite](runs/dev-granite/summary.md)
- [heldout-bonsai](runs/heldout-bonsai/summary.md)
- [heldout-granite](runs/heldout-granite/summary.md)
