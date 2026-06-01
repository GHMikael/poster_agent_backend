from experiments.metrics.a3_hallucination import _classify_nli_result


def test_a3_neutral_abstention_is_unsupported_not_hallucination():
    result = {"p_entail": 0.33, "p_neutral": 0.34, "p_contradict": 0.33}

    assert _classify_nli_result(result, contradict_min=0.5) == "unsupported"


def test_a3_contradiction_requires_argmax_and_threshold():
    weak_contradiction = {"p_entail": 0.32, "p_neutral": 0.28, "p_contradict": 0.4}
    strong_contradiction = {"p_entail": 0.2, "p_neutral": 0.25, "p_contradict": 0.55}

    assert _classify_nli_result(weak_contradiction, contradict_min=0.5) == "unsupported"
    assert _classify_nli_result(strong_contradiction, contradict_min=0.5) == "contradicted"


def test_a3_entailment_argmax_is_supported():
    result = {"p_entail": 0.41, "p_neutral": 0.39, "p_contradict": 0.2}

    assert _classify_nli_result(result, contradict_min=0.5) == "supported"
