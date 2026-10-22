from app.attachment_context import attachment_retrieval_query


def test_attachment_retrieval_query_expands_context_as_or_terms() -> None:
    query = attachment_retrieval_query(
        {
            "class_name": "Execução de Título",
            "subjects": [{"name": "Penhora de bem", "code": "123"}],
        },
        "sentença penhora decisão",
    )

    assert query == (
        "sentença OR penhora OR decisão OR Execução OR de OR Título OR bem OR 123"
    )


def test_attachment_retrieval_query_deduplicates_case_insensitively() -> None:
    query = attachment_retrieval_query(
        {"class_name": "PENHORA", "subjects": [{"name": "penhora"}]},
        "penhora",
    )

    assert query == "penhora"
