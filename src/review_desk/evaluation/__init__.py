"""Score the pipeline against documents with known answers.

Model answers are saved per document, so a long run can stop and resume, and scoring can be rerun
without another model call. Every number in a report comes from the files a run writes.
"""
