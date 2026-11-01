# How confidence works

A local model is wrong often enough that its opinion of itself can't decide what a person checks. So the
model never reports a confidence here. It copies text and cites a line number, and the score comes from
checks anyone can read and rerun.

## The signals

| Signal | Asks | Applies to |
|---|---|---|
| `source` | Is the value printed on the line the model cited? On another line? Only nearly? Nowhere? | Everything the model read |
| `reading` | On a scan, how sure was OCR about these words? On a digital PDF, does the page image show what the text layer claims at that spot? | Everything located on the page |
| `label` | Does a label beside the value fit the field? "Balance Due" next to a total counts against it. | Amounts, dates, identifiers, names |
| `format` | Does it parse? Could a date's day and month swap, with nothing to settle which? | Amounts, dates, identifiers |
| `arithmetic` | Do subtotal, discount, shipping and tax reach the total? Does quantity times price equal the line amount? | Amounts, table numbers |
| `column` | Do the line amounts sum to the subtotal? | Subtotal, table rows |
| `absence` | The model said there's no value. Is there a label on the page suggesting otherwise? | Null answers |
| `position` | Is this name near the top of the page, with a company suffix? | Vendor |
| `date_order` | Is the due date on or after the issue date? | Dates |
| `evidence` | Does the page name the document type? Is the currency a code, a unique symbol, or an assumed `$`? | Type, currency |

Each signal has an effect in log-odds. A field's score is the logistic of a base value plus those effects, so
the review screen can show exactly why a field scored what it did. `arithmetic` matters most for amounts:
numbers that add up are hard to get by accident, and amounts nothing can verify start too low to skip review.

## What the score can't see

- **A wrong value that also passes every check.** A printed date in the wrong field, with a label the
  vocabulary accepts, can score high. That's why the report counts errors among values that skipped review.
- **Identifiers and names can't be added up.** They rest on the source, the OCR rating and the label alone, so they
  carry more risk per point of score than amounts do.
- **The label vocabulary is English and was written against the dev split.** A real document with unusual
  wording loses the label signal's support, which sends more fields to review, the safe direction.

## Weights and threshold

Hand-set values in `confidence.py` are the starting point. `python -m review_desk.evaluation fit` refits them with
logistic regression on a dev run, pulled toward the hand-set values, and picks the lowest threshold that meets a
target error on that data. The result goes in `src/review_desk/calibration.json`. The command refuses the held-out
split, because those documents only stay useful if they're never tuned on.

Weights depend on the model's habits, so refit them after swapping models.
