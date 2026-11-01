# Run summary: dev split (synthetic documents)

Model: `prism-ml/bonsai-27b`, quantization Q1_0, loaded context 8192. Temperature 0.0, strict JSON schema, images for scans: False.

## Documents

Model seconds include time queued behind other requests on a shared server.

| Condition | Documents | Failed | All values right | No field flagged | Wrong though unflagged | Rows missed / truth | Mean model seconds |
|---|---|---|---|---|---|---|---|
| clean | 30 | 0 | 26 (86.7%) | 17 | 0 | 0 / 239 | 100.1867 |
| scanned | 15 | 0 | 4 (26.7%) | 0 | 0 | 0 / 101 | 63.5533 |
| degraded | 15 | 0 | 0 (0.0%) | 0 | 0 | 40 / 101 | 50.0867 |
| all | 60 | 0 | 30 (50.0%) | 17 | 0 | 40 / 441 | 78.5033 |

Model failures during this run, counting every attempt, including ones a retry got past: bad_request 1, model_crashed 3, unreachable 4.

## By scan quality

Values that are actually printed, so an easy null can't flatter a bad scan.

| Quality | Documents | Printed values right | Printed values OCR could read |
|---|---|---|---|
| clean | 30 | 99.7% (1213/1217) | 100.0% |
| light | 15 | 93.8% (502/535) | 97.4% |
| moderate | 9 | 52.6% (191/363) | 74.7% |
| heavy | 6 | 2.9% (5/172) | 12.2% |

## Accuracy by field

| Field | Clean | Scanned | Degraded | All |
|---|---|---|---|---|
| document_type | 100.0% (30/30) | 100.0% (15/15) | 86.7% (13/15) | 96.7% (58/60) |
| vendor_name | 100.0% (30/30) | 93.3% (14/15) | 33.3% (5/15) | 81.7% (49/60) |
| document_number | 93.3% (28/30) | 86.7% (13/15) | 6.7% (1/15) | 70.0% (42/60) |
| issue_date | 96.7% (29/30) | 93.3% (14/15) | 20.0% (3/15) | 76.7% (46/60) |
| due_date | 96.7% (29/30) | 86.7% (13/15) | 60.0% (9/15) | 85.0% (51/60) |
| purchase_order | 100.0% (30/30) | 100.0% (15/15) | 80.0% (12/15) | 95.0% (57/60) |
| bill_to_name | 100.0% (30/30) | 100.0% (15/15) | 53.3% (8/15) | 88.3% (53/60) |
| currency | 100.0% (30/30) | 100.0% (15/15) | 53.3% (8/15) | 88.3% (53/60) |
| subtotal | 100.0% (30/30) | 93.3% (14/15) | 13.3% (2/15) | 76.7% (46/60) |
| discount | 100.0% (30/30) | 100.0% (15/15) | 86.7% (13/15) | 96.7% (58/60) |
| shipping | 100.0% (30/30) | 93.3% (14/15) | 93.3% (14/15) | 96.7% (58/60) |
| tax | 100.0% (30/30) | 93.3% (14/15) | 40.0% (6/15) | 83.3% (50/60) |
| total | 100.0% (30/30) | 93.3% (14/15) | 33.3% (5/15) | 81.7% (49/60) |
| item.description | 100.0% (239/239) | 98.0% (99/101) | 37.0% (40/108) | 84.4% (378/448) |
| item.quantity | 100.0% (239/239) | 92.1% (93/101) | 48.1% (52/108) | 85.7% (384/448) |
| item.unit_price | 99.2% (237/239) | 86.1% (87/101) | 38.0% (41/108) | 81.5% (365/448) |
| item.amount | 100.0% (239/239) | 99.0% (100/101) | 40.7% (44/108) | 85.5% (383/448) |

## Coverage against error, all values

Coverage is the share of values above the threshold, which skip review. Error is how many of those were wrong.

| Threshold | Skip review | Coverage | Wrong among skipped | Error |
|---|---|---|---|---|
| 0.0 | 2572 / 2572 | 100.0% | 392 | 15.2% |
| 0.5 | 2085 / 2572 | 81.1% | 57 | 2.7% |
| 0.6 | 2068 / 2572 | 80.4% | 50 | 2.4% |
| 0.7 | 2025 / 2572 | 78.7% | 41 | 2.0% |
| 0.75 | 2019 / 2572 | 78.5% | 41 | 2.0% |
| 0.8 | 1983 / 2572 | 77.1% | 25 | 1.3% |
| 0.85 | 1914 / 2572 | 74.4% | 8 | 0.4% |
| 0.9 | 1891 / 2572 | 73.5% | 5 | 0.3% |
| 0.92 | 1890 / 2572 | 73.5% | 5 | 0.3% |
| 0.95 | 1783 / 2572 | 69.3% | 4 | 0.2% |
| 0.97 | 1380 / 2572 | 53.6% | 1 | 0.1% |
| 0.98 | 1318 / 2572 | 51.2% | 1 | 0.1% |
| 0.99 | 499 / 2572 | 19.4% | 1 | 0.2% |

## OCR losses against model errors

Printed values whose exact characters survive in the extracted words, and how often the model got those right.

| Condition | Readable | Right when readable |
|---|---|---|
| clean | 100.0% (1157/1157) | 99.7% (1153/1157) |
| scanned | 97.6% (493/505) | 95.3% (470/493) |
| degraded | 55.0% (278/505) | 62.6% (174/278) |
| all | 89.0% (1928/2167) | 93.2% (1797/1928) |

## Source highlight

Of right values that are printed, how often the highlight landed on the ink.

| Condition | Hit rate |
|---|---|
| clean | 99.7% (1150/1153) |
| scanned | 98.7% (466/472) |
| degraded | 97.7% (171/175) |
| all | 99.3% (1787/1800) |

## By layout

| Layout | Accuracy | Values |
|---|---|---|
| banner | 83.6% | 280 |
| boxed | 73.1% | 268 |
| card_slip | 89.4% | 141 |
| classic | 82.1% | 364 |
| ledger | 86.4% | 508 |
| pos_wide | 87.1% | 163 |
| sidebar | 89.5% | 580 |
| thermal | 84.0% | 268 |
