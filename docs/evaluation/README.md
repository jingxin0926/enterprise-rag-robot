# RAG Evaluation Dataset

`cases.jsonl` is the baseline regression dataset for the enterprise knowledge base.

Each line is a JSON object with these fields:

- `id`: stable test case identifier.
- `category`: `single_document`, `cross_document`, or `refusal`.
- `question`: question submitted to the RAG API.
- `expected_sources`: source documents that should be cited.
- `expected_answer_points`: facts that a correct answer should cover.
- `should_refuse`: whether the system should refuse due to insufficient evidence.

This first version has 23 cases based on the two uploaded requirement documents. Add a test case whenever a production issue, an ambiguous question, or a document processing defect is found.
