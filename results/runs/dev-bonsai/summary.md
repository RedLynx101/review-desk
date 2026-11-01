# Run summary: dev split (synthetic documents)

Model: `prism-ml/bonsai-27b`, quantization Q1_0, loaded context 8192. Temperature 0.0, strict JSON schema, images for scans: False.

## Documents

Model seconds include time queued behind other requests on a shared server.

| Condition | Documents | Failed | All values right | No field flagged | Wrong though unflagged | Rows missed / truth | Mean model seconds |
|---|---|---|---|---|---|---|---|
| clean | 15 | 0 | 14 (93.3%) | 12 | 0 | 0 / 101 | 63.6667 |
| scanned | 15 | 0 | 5 (33.3%) | 0 | 0 | 0 / 101 | 64.5667 |
| degraded | 15 | 0 | 0 (0.0%) | 0 | 0 | 40 / 101 | 53.84 |
| all | 45 | 0 | 19 (42.2%) | 12 | 0 | 40 / 303 | 60.6911 |

## By scan quality

Values that are actually printed, so an easy null can't flatter a bad scan.

| Quality | Documents | Printed values right | Printed values OCR could read |
|---|---|---|---|
| clean | 15 | 99.8% (534/535) | 100.0% |
| light | 15 | 93.8% (502/535) | 97.4% |
| moderate | 9 | 52.6% (191/363) | 74.7% |
| heavy | 6 | 2.9% (5/172) | 12.2% |

## Accuracy by field

| Field | Clean | Scanned | Degraded | All |
|---|---|---|---|---|
| document_type | 100.0% (15/15) | 100.0% (15/15) | 86.7% (13/15) | 95.6% (43/45) |
| vendor_name | 100.0% (15/15) | 93.3% (14/15) | 33.3% (5/15) | 75.6% (34/45) |
| document_number | 100.0% (15/15) | 86.7% (13/15) | 6.7% (1/15) | 64.4% (29/45) |
| issue_date | 93.3% (14/15) | 93.3% (14/15) | 20.0% (3/15) | 68.9% (31/45) |
| due_date | 100.0% (15/15) | 86.7% (13/15) | 60.0% (9/15) | 82.2% (37/45) |
| purchase_order | 100.0% (15/15) | 100.0% (15/15) | 80.0% (12/15) | 93.3% (42/45) |
| bill_to_name | 100.0% (15/15) | 100.0% (15/15) | 53.3% (8/15) | 84.4% (38/45) |
| currency | 100.0% (15/15) | 100.0% (15/15) | 53.3% (8/15) | 84.4% (38/45) |
| subtotal | 100.0% (15/15) | 93.3% (14/15) | 13.3% (2/15) | 68.9% (31/45) |
| discount | 100.0% (15/15) | 100.0% (15/15) | 86.7% (13/15) | 95.6% (43/45) |
| shipping | 100.0% (15/15) | 93.3% (14/15) | 93.3% (14/15) | 95.6% (43/45) |
| tax | 100.0% (15/15) | 93.3% (14/15) | 40.0% (6/15) | 77.8% (35/45) |
| total | 100.0% (15/15) | 93.3% (14/15) | 33.3% (5/15) | 75.6% (34/45) |
| item.description | 100.0% (101/101) | 98.0% (99/101) | 37.0% (40/108) | 77.4% (240/310) |
| item.quantity | 100.0% (101/101) | 92.1% (93/101) | 48.1% (52/108) | 79.3% (246/310) |
| item.unit_price | 100.0% (101/101) | 88.1% (89/101) | 38.9% (42/108) | 74.8% (232/310) |
| item.amount | 100.0% (101/101) | 99.0% (100/101) | 40.7% (44/108) | 79.0% (245/310) |

## Coverage against error, all values

Coverage is the share of values above the threshold, which skip review. Error is how many of those were wrong.

| Threshold | Skip review | Coverage | Wrong among skipped | Error |
|---|---|---|---|---|
| 0.0 | 1825 / 1825 | 100.0% | 384 | 21.0% |
| 0.5 | 1362 / 1825 | 74.6% | 60 | 4.4% |
| 0.6 | 1302 / 1825 | 71.3% | 37 | 2.8% |
| 0.7 | 1261 / 1825 | 69.1% | 28 | 2.2% |
| 0.75 | 1255 / 1825 | 68.8% | 28 | 2.2% |
| 0.8 | 1221 / 1825 | 66.9% | 12 | 1.0% |
| 0.85 | 1205 / 1825 | 66.0% | 7 | 0.6% |
| 0.9 | 1191 / 1825 | 65.3% | 5 | 0.4% |
| 0.92 | 1190 / 1825 | 65.2% | 5 | 0.4% |
| 0.95 | 1111 / 1825 | 60.9% | 4 | 0.4% |
| 0.97 | 854 / 1825 | 46.8% | 1 | 0.1% |
| 0.98 | 809 / 1825 | 44.3% | 1 | 0.1% |
| 0.99 | 301 / 1825 | 16.5% | 1 | 0.3% |

## OCR losses against model errors

Printed values whose exact characters survive in the extracted words, and how often the model got those right.

| Condition | Readable | Right when readable |
|---|---|---|
| clean | 100.0% (505/505) | 99.8% (504/505) |
| scanned | 97.6% (493/505) | 95.3% (470/493) |
| degraded | 55.0% (278/505) | 62.6% (174/278) |
| all | 84.2% (1276/1515) | 90.0% (1148/1276) |

## Source highlight

Of right values that are printed, how often the highlight landed on the ink.

| Condition | Hit rate |
|---|---|
| clean | 99.6% (502/504) |
| scanned | 98.7% (466/472) |
| degraded | 97.7% (171/175) |
| all | 99.0% (1139/1151) |

## By layout

| Layout | Accuracy | Values |
|---|---|---|
| banner | 78.1% | 210 |
| boxed | 63.6% | 198 |
| card_slip | 82.7% | 75 |
| classic | 67.2% | 198 |
| ledger | 77.5% | 306 |
| pos_wide | 83.3% | 126 |
| sidebar | 88.1% | 498 |
| thermal | 81.8% | 214 |
